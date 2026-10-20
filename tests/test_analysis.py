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


def test_stage_rules_override_module_paths():
    stage_of = A.stage_namer({"stages": {"Domain": ["app.models", "app.pricing.*"]}, "stage_depth": 1})
    assert stage_of("app.models", True) == "Domain"
    assert stage_of("app.pricing.rules", True) == "Domain"
    assert stage_of("app.api", True) == "app"
    assert stage_of("sqlalchemy.orm.session", False) == "sqlalchemy"


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
