"""Turn stored, observed events into what the viewer navigates. Pure functions over a Reader.

Everything computed here (stages, groups, flow links, change attribution) is *inferred* and says so.
See docs/components/analysis.md.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import defaultdict

from vizcontrol.serialize import preview
from vizcontrol.store import Reader

# -- nodes --------------------------------------------------------------------------------------------------------

def _duration(c: dict) -> int | None:
    return c["end_ns"] - c["start_ns"] if c.get("end_ns") is not None else None


def call_node(c: dict, stage: str | None = None) -> dict:
    """The JSON shape of one call, as the viewer receives it."""
    return {
        "type": "call", "id": c["id"], "parent_id": c["parent_id"], "code_id": c["code_id"], "qualname": c["qualname"],
        "module": c["module"],
        "kind": c["kind"], "observed": bool(c["observed"]), "outcome": c["outcome"], "start_ns": c["start_ns"],
        "duration_ns": _duration(c), "end_exact": bool(c["end_exact"]), "children": c["children"],
        "depth": c["depth"], "seq": c["seq_start"], "call_line": c["call_line"], "exception_id": c["exception_id"],
        "thread_id": c["thread_id"], "file": c.get("relpath") or c.get("path"), "first_line": c["first_line"],
        "stage": stage,
    }


def subtree(reader: Reader, call_id: int | None = None, depth: int = 1, limit: int = 200,
            stage_of=None) -> list[dict]:
    """Children of `call_id` (roots if None), `depth` levels deep. Siblings running the same code fold into one
    group node (FR-ANA-3): a 10,000-iteration loop is one node until expanded with `group_members`."""
    groups = reader.grouped_children(call_id)
    singles = [g["first_id"] for g in groups[:limit] if g["count"] == 1]
    by_id = {c["id"]: c for c in reader.calls(singles)}
    groups_code = {g["code_id"] for g in groups[:limit] if g["count"] > 1}
    samples = {}
    if groups_code:  # one representative call per group, for its names and kind
        rows = reader.calls([g["first_id"] for g in groups[:limit] if g["count"] > 1])
        samples = {r["code_id"]: r for r in rows}
    out = []
    for g in groups[:limit]:
        if g["count"] == 1:
            c = by_id[g["first_id"]]
            node = call_node(c, stage_of(c["module"], c["observed"]) if stage_of else None)
            if depth > 1 and c["children"]:
                node["items"] = subtree(reader, c["id"], depth - 1, limit, stage_of)
        else:
            s = samples[g["code_id"]]
            node = {
                "type": "group", "parent_id": call_id, "code_id": g["code_id"], "qualname": s["qualname"],
                "module": s["module"], "kind": s["kind"], "observed": bool(s["observed"]), "count": g["count"],
                "outcomes": {"raised": g["raised"], "unfinished": g["unfinished"],
                             "ok": g["count"] - g["raised"] - g["unfinished"]},
                "total_ns": g["total_ns"], "first_call_id": g["first_id"], "seq": g["first_seq"],
                "stage": stage_of(s["module"], s["observed"]) if stage_of else None, "inferred": True,
            }
        out.append(node)
    if len(groups) > limit:
        out.append({"type": "more", "parent_id": call_id, "remaining": len(groups) - limit})
    return out


def group_members(reader: Reader, call_id: int | None, code_id: int, offset: int = 0, limit: int = 200) -> list[dict]:
    return [call_node(c) for c in reader.group_members(call_id, code_id, offset, limit)]


# -- stages (Level 1) ---------------------------------------------------------------------------------------------

def stage_namer(config: dict):
    """Map (module, observed) to a stage name: user rules first, then the module path cut to `stage_depth`."""
    rules = config.get("stages") or {}
    depth = config.get("stage_depth", 2)
    cache: dict[tuple, str] = {}

    def stage_of(module: str | None, observed) -> str:
        key = (module, bool(observed))
        name = cache.get(key)
        if name is None:
            name = "?" if module is None else next(
                (stage for stage, globs in rules.items()
                 if any(fnmatch.fnmatch(module, g) or (g.endswith(".*") and module == g[:-2]) for g in globs)),
                None)
            if name is None:
                parts = module.split(".")
                name = ".".join(parts[: depth if observed else 1])  # libraries group under their top package
            cache[key] = name
        return name

    return stage_of


def stages(reader: Reader) -> dict:
    """Stage graph for the Level-1 overview (FR-ANA-2). One pass over all calls."""
    stage_of = stage_namer(reader.run()["config"])
    rows = reader.all_calls()
    stage_by_call: dict[int, str] = {}
    dur: dict[int, int] = {}
    child_dur: dict[int, int] = defaultdict(int)
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    roots: list[str] = []
    for cid, parent, start, end, outcome, seq, module, observed, kind in rows:
        stage = stage_by_call[cid] = stage_of(module, observed)
        d = dur[cid] = end - start if end is not None else 0
        n = nodes.get(stage)
        if n is None:
            n = nodes[stage] = {"name": stage, "calls": 0, "total_ns": 0, "self_ns": 0, "raised": 0,
                                "unfinished": 0, "first_seq": seq, "first_call": cid, "observed": bool(observed),
                                "modules": set(), "first_call_is_import": kind in ("module", "class")}
        if n["first_call_is_import"] and kind not in ("module", "class"):
            # where a stage "starts" for a reader is its first real call, not the import that defined it
            n["first_call"], n["first_call_is_import"] = cid, False
        n["calls"] += 1
        n["raised"] += outcome == "raised"
        n["unfinished"] += outcome == "unfinished"
        n["modules"].add(module)
        if parent is None:
            roots.append(stage)
            n["total_ns"] += d
        else:
            child_dur[parent] += d
            pstage = stage_by_call.get(parent)
            if pstage is not None and pstage != stage:
                e = edges.get((pstage, stage))
                if e is None:
                    e = edges[(pstage, stage)] = {"from": pstage, "to": stage, "calls": 0, "total_ns": 0,
                                                  "first_seq": seq, "calls_below": 0}
                e["calls"] += 1
                e["total_ns"] += d
                nodes[stage]["total_ns"] += d  # time spent in a stage counts from where it is entered
    for cid, stage in stage_by_call.items():
        nodes[stage]["self_ns"] += max(dur[cid] - child_dur.get(cid, 0), 0)
    # subtree sizes (children come after parents in seq order): how much work each stage crossing leads to
    size: dict[int, int] = defaultdict(lambda: 1)
    for cid, parent, *_ in reversed(rows):
        if parent is not None:
            size[parent] += size[cid]
    for cid, parent, *_ in rows:
        if parent is not None and stage_by_call.get(parent) not in (None, stage_by_call[cid]):
            edges[(stage_by_call[parent], stage_by_call[cid])]["calls_below"] += size[cid]
    for n in nodes.values():
        n["modules"] = sorted(m for m in n["modules"] if m)
        del n["first_call_is_import"]
    return {"nodes": sorted(nodes.values(), key=lambda n: n["first_seq"]),
            "edges": sorted(edges.values(), key=lambda e: e["first_seq"]),
            "main_path": _main_path(roots, edges), "inferred": True}


def _main_path(roots: list[str], edges: dict[tuple[str, str], dict]) -> list[str]:
    """From the entry stage, keep following the outgoing edge that leads to the most calls (its subtree size; time
    only breaks ties) to a stage not yet on the path. Call counts are deterministic, timings are noise in small runs."""
    if not roots:
        return []
    out: dict[str, list[dict]] = defaultdict(list)
    for e in edges.values():
        out[e["from"]].append(e)
    path = [roots[0]]
    while True:
        nxt = [e for e in out[path[-1]] if e["to"] not in path]
        if not nxt:
            return path
        path.append(max(nxt, key=lambda e: (e["calls_below"], e["total_ns"], -e["first_seq"]))["to"])


# -- values and data flow -----------------------------------------------------------------------------------------

def value_diff(a: dict, b: dict, path: str = "") -> list[dict]:
    """Field-level differences between two encoded value trees. Truncated parts compare as 'unknown', never equal."""
    if a == b:
        return []
    ta, tb = a.get("t"), b.get("t")
    if "trunc" in (ta, tb) or "redacted" in (ta, tb):
        return [{"path": path or ".", "op": "unknown"}]
    if ta == tb == "obj" and a.get("type") == b.get("type"):
        return _diff_maps(a["attrs"], b["attrs"], path, ".")
    if ta == tb == "dict":
        def keyed(d):
            return {preview(k, 40): v for k, v in d["items"]}
        return _diff_maps(keyed(a), keyed(b), path, "[]")
    if ta == tb and ta in ("list", "tuple") and "len" not in a and "len" not in b:
        changes = []
        for i in range(max(len(a["items"]), len(b["items"]))):
            p = f"{path}[{i}]"
            if i >= len(a["items"]):
                changes.append({"path": p, "op": "added", "after": preview(b["items"][i])})
            elif i >= len(b["items"]):
                changes.append({"path": p, "op": "removed", "before": preview(a["items"][i])})
            else:
                changes.extend(value_diff(a["items"][i], b["items"][i], p))
        return changes
    if ta == tb and a.get("oid") is not None and a.get("oid") == b.get("oid") and ta in ("list", "set", "dict"):
        return [{"path": path or ".", "op": "unknown"}]  # truncated containers: can't tell what changed
    return [{"path": path or ".", "op": "changed", "before": preview(a), "after": preview(b)}]


def _diff_maps(a: dict, b: dict, path: str, style: str) -> list[dict]:
    changes = []
    for key in list(a) + [k for k in b if k not in a]:
        p = f"{path}.{key}" if style == "." else f"{path}[{key}]"
        if key not in b:
            changes.append({"path": p, "op": "removed", "before": preview(a[key])})
        elif key not in a:
            changes.append({"path": p, "op": "added", "after": preview(b[key])})
        else:
            changes.extend(value_diff(a[key], b[key], p))
    return changes


def _find(d: dict, oid: int, path: str = "") -> tuple[str, dict] | None:
    """The encoding of object `oid` inside a value tree and its path (`.customer`, `[0]`). A repeat of an object in
    one value is a `ref` and comes after its full encoding, so the first non-ref match is the one with content."""
    if d.get("oid") == oid and d.get("t") != "ref":
        return path, d
    for key, child in _children(d):
        found = _find(child, oid, path + key)
        if found:
            return found
    return None


def _children(d: dict) -> list[tuple[str, dict]]:
    t = d.get("t")
    if t == "obj":
        return [(f".{k}", v) for k, v in d["attrs"].items()]
    if t == "dict":
        return [(f"[{preview(k, 40)}]", v) for k, v in d["items"]]
    if t in ("list", "tuple", "set", "frozenset"):
        return [(f"[{i}]", v) for i, v in enumerate(d["items"])]
    return []


_FIELD = re.compile(r"\.([^.\[]+)|(\[[^\]]*\])")


def object_flow(reader: Reader, object_id: int, limit: int = 200) -> dict:
    """Where one object was seen (as an argument, return value, local, or nested inside one of those), how it moved
    and what changed. Each appearance carries its fields and which of them changed since the previous one."""
    appearances, links, changes = [], [], []
    prev, last = None, None  # previous appearance; last full encoding (a depth-truncated one can't be diffed)
    for r in reader.object_bindings(object_id):
        found = _find(json.loads(r["data"]), object_id)
        if found is None:  # the oid matched only a ref
            continue
        path, data = found
        diff = value_diff(last[1], data) if last and data.get("t") != "trunc" else []
        appearances.append({
            "call_id": r["call_id"], "qualname": r["qualname"], "module": r["module"], "seq": r["seq"],
            "role": r["kind"], "name": r["name"], "path": path, "value_id": r["value_id"],
            "type": data.get("type") or data["t"], "preview": preview(data),
            "fields": [[k.lstrip("."), preview(v, 60)] for k, v in _children(data)[:8]],
            "changed": sorted({m.group(1) or m.group(2) for c in diff if (m := _FIELD.match(c["path"]))}),
        })
        if prev is not None and prev["call_id"] != r["call_id"]:
            links.append({"from": prev["call_id"], "to": r["call_id"], "seq": r["seq"], "inferred": True})
        if diff:
            changes.append({"seq_before": last[0]["seq"], "seq_after": r["seq"], "call_before": last[0]["call_id"],
                            "call_after": r["call_id"], "changes": diff, "inferred": True})
        prev = appearances[-1]
        if data.get("t") != "trunc":
            last = (prev, data)
    named = [a["name"] + a["path"] for a in appearances if a["name"] and a["name"] != "self"]
    return {"object_id": object_id, "type": appearances[0]["type"] if appearances else None,
            "name": min(named, key=len) if named else None,
            "total": len(appearances), "appearances": appearances[:limit], "links": links[:limit],
            "changes": changes}


# -- errors -------------------------------------------------------------------------------------------------------

def _binding_view(b: dict) -> dict:
    data = json.loads(b["data"])
    return {"name": b["name"], "kind": b["kind"], "value_id": b["value_id"], "object_id": b["object_id"],
            "type": b["type"], "preview": preview(data)}


def error_path(reader: Reader) -> dict | None:
    """The exception that ended the run: where it started, how execution got there, how it propagated,
    and the latest observed changes to the objects in scope (FR-ANA-5)."""
    escaped = [e for e in reader.exceptions() if e["handled_call_id"] is None and e["raised_call_id"] is not None]
    if not escaped:
        return None
    exc = escaped[-1]
    path = reader.ancestors(exc["raised_call_id"])
    origin = path[-1]
    stage_of = stage_namer(reader.run()["config"])
    locals_at_raise = [b for b in reader.bindings(origin["id"]) if b["kind"] == "raise"]
    recent = []
    for b in locals_at_raise:
        if b["object_id"] is not None:
            for ch in object_flow(reader, b["object_id"])["changes"][-3:]:
                recent.append({"name": b["name"], "object_id": b["object_id"], **ch})
    recent.sort(key=lambda c: c["seq_after"])
    chain, cause = [], exc["cause_id"]
    while cause is not None and len(chain) < 10:
        c = reader.exception(cause)
        if c is None:
            break
        chain.append({k: c[k] for k in ("id", "type", "message", "raised_call_id", "raised_line")})
        cause = c["cause_id"]
    return {
        "exception": {k: exc[k] for k in ("id", "type", "message", "raised_line", "value_id")},
        "traceback": json.loads(exc["traceback"]),
        "origin": call_node(origin, stage_of(origin["module"], origin["observed"])),
        "path": [call_node(c, stage_of(c["module"], c["observed"])) for c in path],
        "propagation": [c["id"] for c in reversed(path) if c["exception_id"] == exc["id"]],
        "locals": [_binding_view(b) for b in locals_at_raise],
        "recent_changes": recent[-5:],
        "chain": chain,
    }


# -- search -------------------------------------------------------------------------------------------------------

def search(reader: Reader, text: str, limit: int = 100) -> list[dict]:
    if not text.strip():
        return []
    found = reader.search(text.strip(), limit)
    hits = [{"kind": "function", "label": f["qualname"], "module": f["module"], "call_id": f["call_id"],
             "count": f["count"], "observed": bool(f["observed"])} for f in found["functions"]]
    for kind in ("variables", "values"):
        for v in found[kind]:
            hits.append({"kind": kind[:-1], "label": v["name"] or v["kind"], "call_id": v["call_id"],
                         "role": v["kind"], "preview": preview(json.loads(v["data"]))})
    return hits[:limit]
