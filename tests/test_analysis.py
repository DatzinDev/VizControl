import pytest

import vizcontrol
from vizcontrol import analysis as A
from vizcontrol.store import Reader


@pytest.fixture(scope="module")
def runs(runs_dir):
    return {tier: Reader(runs_dir / f"{tier}.vizc") for tier in ("basic", "vip")}


def test_stages_and_main_path(runs):
    g = A.stages(runs["basic"])
    assert g["inferred"] and g["main_path"][:3] == ["__main__", "app.api", "app.services"]
    nodes = {n["name"]: n for n in g["nodes"]}
    assert nodes["json"]["observed"] is False  # library stage, grouped under its top package
    first = runs["basic"].call(nodes["app.services"]["first_call"])
    assert first["module"] == "app.services" and first["kind"] not in ("module", "class")  # not the import
    assert nodes["app.services"]["self_ns"] <= nodes["app.services"]["total_ns"]
    edges = {(e["from"], e["to"]): e["calls"] for e in g["edges"]}
    assert edges[("app.services", "app.repo")] >= 1 and ("app.repo", "json") in edges


def test_stage_detail_opens_a_stage_into_functions(runs):
    d = A.stage_detail(runs["basic"], "app.services")
    funcs = {f["qualname"]: f for f in d["functions"]}
    assert funcs["OrderService.place_order"]["cls"] == "OrderService" and funcs["iter_valid"]["cls"] is None
    assert funcs["OrderService"]["kind"] == "class" and funcs["OrderService"]["cls"] == "OrderService"
    by_code = {f["code_id"]: f["qualname"] for f in d["functions"]}
    inside = {(by_code[e["from"]], by_code[e["to"]]) for e in d["edges"]}
    assert ("OrderService.place_order", "OrderService._build_order") in inside
    entered = {(i["stage"], i["from_qualname"], by_code[i["to"]]) for i in d["incoming"]}
    assert ("app.api", "post_order", "OrderService.place_order") in entered
    assert {o["stage"] for o in d["outgoing"]} >= {"app.repo", "app.pricing", "app.models"}
    assert d["stats"]["functions"] == len(funcs) and d["stats"]["self_ns"] <= d["stats"]["total_ns"]
    assert A.stage_detail(runs["basic"], "nope") is None


def test_stage_rules_override_module_paths():
    stage_of = A.stage_namer({"stages": {"Domain": ["app.models", "app.pricing.*"]}, "stage_depth": 1})
    assert stage_of("app.models", True) == "Domain"
    assert stage_of("app.pricing.rules", True) == "Domain"
    assert stage_of("app.api", True) == "app"
    assert stage_of("sqlalchemy.orm.session", False) == "sqlalchemy"


def extract():
    return [{"n": n} for n in range(4)]


def clean(rows):
    return [r for r in rows if r["n"]]


def transform(rows):
    return [{"n": r["n"] * 2} for r in clean(rows)]


def load(rows):
    return len(rows)


def pipeline():
    return load(transform(extract()))


def main():
    return pipeline()


def test_flat_project_is_staged_by_the_functions_its_entry_fans_out_to(tmp_path):
    with vizcontrol.trace(output=tmp_path / "flat.vizc") as t:
        main()
    r = Reader(t.path)
    g = A.stages(r)
    names = [n["name"] for n in g["nodes"]]
    assert names[1:] == ["extract", "transform", "load"]  # main() -> pipeline() is the entry, not a stage
    assert {(e["from"], e["to"]) for e in g["edges"]} == {(names[0], s) for s in names[1:]}
    assert [f["qualname"] for f in A.stage_detail(r, "transform")["functions"]] == ["transform", "clean"]
    stager = A.Stager(r)
    [root] = A.subtree(r, None, depth=3, stager=stager)
    [pipe] = root["items"]
    assert {c["qualname"]: c["stage"] for c in pipe["items"]}["transform"] == "transform"
    clean_call = next(c for c in r.all_calls() if r.call(c[0])["qualname"] == "clean")
    assert stager.of(r, r.call(clean_call[0])) == "transform"
    [inner] = A.subtree(r, clean_call[1], stager=stager, head=stager.head_of(r, clean_call[1]))
    assert inner["stage"] == "transform"


def test_function_rules_make_stages(tmp_path):
    with vizcontrol.trace(output=tmp_path / "rule.vizc", stages={"Load": ["*:load"], "T": ["*:trans*"]}) as t:
        main()
    names = [n["name"] for n in A.stages(Reader(t.path))["nodes"]]
    assert names[1:] == ["T", "Load"]  # with rules, only the rules split


def test_subtree_groups_repeated_siblings(tmp_path):
    def item(i):
        return i

    def loop():
        for i in range(30):
            item(i)
        return item(-1)

    with vizcontrol.trace(output=tmp_path / "g.vizc") as t:
        loop()
    r = Reader(t.path)
    [root] = A.subtree(r, None, depth=2)
    [group] = root["items"]
    assert (group["type"], group["count"], group["outcomes"]["ok"]) == ("group", 31, 31)
    members = A.group_members(r, root["id"], group["code_id"], offset=0, limit=5)
    assert len(members) == 5 and members[0]["qualname"].endswith("item")
    assert A.subtree(r, root["id"], limit=0)[-1] == {"type": "more", "parent_id": root["id"], "remaining": 1}


def test_error_path(runs):
    ep = A.error_path(runs["vip"])
    assert ep["exception"]["type"] == "ValueError"
    assert ep["origin"]["qualname"] == "apply_discount" and ep["origin"]["stage"] == "app.pricing"
    assert [c["qualname"] for c in ep["path"]][-3:] == ["OrderService._build_order", "compute", "apply_discount"]
    assert len(ep["propagation"]) == 6 and ep["propagation"][0] == ep["origin"]["id"]
    assert {b["name"]: b["preview"] for b in ep["locals"]}["rule"] == "Rule{pct: 120}"
    last = ep["recent_changes"][-1]
    assert last["changes"] == [{"path": ".pct", "op": "changed", "before": "20", "after": "120"}]
    assert runs["vip"].call(last["call_after"])["qualname"] == "load_vip_rules"
    assert A.error_path(runs["basic"]) is None


def test_object_flow_follows_the_order(runs):
    r = runs["basic"]
    place = next(c for c in r.search("place_order")["functions"])
    order_oid = next(b["object_id"] for b in r.bindings(place["call_id"]) if b["kind"] == "return")
    flow = A.object_flow(r, order_oid)
    calls = [a["qualname"] for a in flow["appearances"]]
    assert "OrderRepo.add" in calls and calls[-1] == "OrderService.place_order"
    statuses = [ch for c in flow["changes"] for ch in c["changes"] if ch["path"] == ".status"]
    assert statuses[-1] == {"path": ".status", "op": "changed", "before": "'draft'", "after": "'paid'"}
    assert all(link["inferred"] for link in flow["links"])
    assert flow["name"] == "order" and flow["type"] == "app.models.Order"
    assert any("status" in a["changed"] and ["status", "'paid'"] in a["fields"] for a in flow["appearances"])


def test_object_flow_finds_objects_nested_in_other_values(runs):
    r = runs["basic"]
    place = next(c for c in r.search("place_order")["functions"])
    oid = next(b["object_id"] for b in r.bindings(place["call_id"]) if b["name"] == "customer")
    flow = A.object_flow(r, oid)
    nested = [(a["qualname"], a["name"], a["path"]) for a in flow["appearances"] if a["path"]]
    assert ("compute", "order", ".customer") in nested and ("OrderRepo.add", "order", ".customer") in nested
    assert flow["name"] == "customer"
    assert flow["appearances"][0]["fields"] == [] and flow["appearances"][1]["changed"] == ["email", "id", "tier"]


def test_compare_finds_where_two_runs_split(runs):
    d = A.compare(runs["basic"], runs["vip"])
    assert d["summary"]["outcome_a"] == "ok" and d["summary"]["outcome_b"] == "raised"
    div = d["divergence"]
    assert div["parent"]["qualname"] == "compute"  # where it split, not place_order where B stopped early
    assert [p["a"]["qualname"] for p in div["path"]][-2:] == ["OrderService._build_order", "compute"]
    assert div["only_a"] == [] and [c["qualname"] for c in div["only_b"]] == ["load_vip_rules", "apply_discount"]
    order = next(x for x in div["args"] if x["name"] == "order")
    assert order["changes"] == [{"path": ".customer.tier", "op": "changed", "before": "'basic'", "after": "'vip'"}]
    assert any("tier == \"vip\"" in line for line in div["branch"]["lines"])
    assert A.compare(runs["basic"], runs["basic"])["divergence"] is None


def test_value_diff():
    a = {"t": "obj", "type": "T", "attrs": {"x": {"t": "int", "v": 1}, "gone": {"t": "none"}}}
    b = {"t": "obj", "type": "T", "attrs": {"x": {"t": "int", "v": 2}, "new": {"t": "str", "v": "s"}}}
    assert A.value_diff(a, b) == [
        {"path": ".x", "op": "changed", "before": "1", "after": "2"},
        {"path": ".gone", "op": "removed", "before": "None"},
        {"path": ".new", "op": "added", "after": "'s'"},
    ]
    assert A.value_diff({"t": "trunc"}, {"t": "int", "v": 1}) == [{"path": ".", "op": "unknown"}]


def test_search(runs):
    hits = A.search(runs["basic"], "pricing")
    assert any(h["kind"] == "function" and h["label"] == "compute" for h in hits)
    assert any(h["kind"] == "variable" and h["label"] == "tier" for h in A.search(runs["basic"], "tier"))
    assert A.search(runs["basic"], "  ") == []
