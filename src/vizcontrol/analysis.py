"""Turn stored, observed events into what the viewer navigates. Pure functions over a Reader.

Everything computed here (stages, groups, flow links, change attribution) is *inferred* and says so.
See docs/components/analysis.md.
"""

from __future__ import annotations

import difflib
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
    for cid, parent, start, end, outcome, seq, module, observed, kind, _ in rows:
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


# -- threads --------------------------------------------------------------------------------------------------------

def thread_starts(reader: Reader) -> dict[int, int]:
    """root call id -> the `Thread.start` call that launched its thread. Inferred: each root call outside the run's
    first thread takes the earliest unmatched `Thread.start` that began before it, in another thread. `start()`
    waits for the thread to run, so time order lines them up. Keyed by root call, not thread id: Python reuses
    idents. ponytail: a thread whose target reaches traced code twice from untraced code takes two starts."""
    roots = reader.roots()
    starts = reader.thread_start_calls()
    main = roots[0]["thread_id"] if roots else None  # alive the whole run, so its ident is never reused
    out: dict[int, int] = {}
    used: set[int] = set()
    for root in roots:
        if root["thread_id"] == main:
            continue
        for s in starts:
            if s["id"] not in used and s["start_ns"] < root["start_ns"] and s["thread_id"] != root["thread_id"]:
                out[root["id"]] = s["id"]
                used.add(s["id"])
                break
    return out


# -- one stage opened (Level 2) -----------------------------------------------------------------------------------

def _class_of(qualname: str) -> str | None:
    """`OrderService.place_order` -> `OrderService`; module-level and nested (`f.<locals>.g`) functions have none."""
    parts = qualname.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 and "<locals>" not in parts else None


def stage_detail(reader: Reader, stage: str) -> dict | None:
    """Level 2 (FR-VIZ-2): one stage opened into module › class › function, one node per function (its calls
    aggregated), the calls between them, and the calls crossing the stage boundary per (caller, callee) pair.
    ponytail: every function of the stage is returned; page them if a stage ever has thousands."""
    stage_of = stage_namer(reader.run()["config"])
    codes = {c["id"]: c for c in reader.codes()}
    stage_by_call: dict[int, str] = {}
    code_by_call: dict[int, int] = {}
    dur: dict[int, int] = {}
    child_dur: dict[int, int] = defaultdict(int)
    funcs: dict[int, dict] = {}
    internal: dict[tuple, int] = defaultdict(int)
    incoming: dict[tuple, int] = defaultdict(int)
    outgoing: dict[tuple, int] = defaultdict(int)
    total = 0
    for cid, parent, start, end, outcome, seq, module, observed, kind, code_id in reader.all_calls():
        s = stage_by_call[cid] = stage_of(module, observed)
        code_by_call[cid] = code_id
        d = dur[cid] = end - start if end is not None else 0
        if parent is not None:
            child_dur[parent] += d
        pstage = stage_by_call.get(parent)
        if s == stage:
            f = funcs.get(code_id)
            if f is None:
                c = codes[code_id]
                f = funcs[code_id] = {
                    "code_id": code_id, "qualname": c["qualname"], "module": module,
                    "cls": c["qualname"] if kind == "class" else _class_of(c["qualname"]),  # class body: in its class
                    "file": c["relpath"] or c["path"], "kind": kind, "observed": bool(observed), "calls": 0,
                    "total_ns": 0, "self_ns": 0, "raised": 0, "unfinished": 0, "first_call": cid, "first_seq": seq}
            f["calls"] += 1
            f["total_ns"] += d
            f["raised"] += outcome == "raised"
            f["unfinished"] += outcome == "unfinished"
            if pstage == stage:
                internal[(code_by_call[parent], code_id)] += 1
            else:
                total += d
                incoming[(pstage, parent and code_by_call[parent], code_id)] += 1
        elif pstage == stage:
            outgoing[(code_by_call[parent], s)] += 1
    if not funcs:
        return None
    for cid, s in stage_by_call.items():
        if s == stage:
            funcs[code_by_call[cid]]["self_ns"] += max(dur[cid] - child_dur.get(cid, 0), 0)
    fs = sorted(funcs.values(), key=lambda f: f["first_seq"])
    return {
        "stage": stage, "observed": any(f["observed"] for f in fs), "functions": fs,
        "edges": [{"from": a, "to": b, "calls": n} for (a, b), n in internal.items()],
        "incoming": [{"stage": st, "from_code": fc, "from_qualname": fc and codes[fc]["qualname"], "to": to,
                      "calls": n} for (st, fc, to), n in incoming.items()],
        "outgoing": [{"from": fc, "stage": st, "calls": n} for (fc, st), n in outgoing.items()],
        "stats": {"modules": len({f["module"] for f in fs}), "classes": len({(f["module"], f["cls"]) for f in fs
                                                                             if f["cls"]}),
                  "functions": len(fs), "calls": sum(f["calls"] for f in fs), "total_ns": total,
                  "self_ns": sum(f["self_ns"] for f in fs)},
        "inferred": True,
    }


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


# -- run comparison ------------------------------------------------------------------------------------------------

ALIGN_LIMIT = 5_000  # calls per run between the common prefix and suffix fed to difflib, quadratic at worst


def _signature(reader: Reader) -> list[dict]:
    codes = {c["id"]: c for c in reader.codes()}
    out = []
    for cid, parent, start, end, outcome, seq, module, _, _, code_id in reader.all_calls():
        out.append({"id": cid, "parent_id": parent, "module": module, "qualname": codes[code_id]["qualname"],
                    "outcome": outcome, "duration_ns": end - start if end is not None else None, "seq": seq})
    depth: dict[int, int] = {}
    for c in out:  # pre-order: a parent comes before its children
        c["depth"] = depth[c["id"]] = depth.get(c["parent_id"], -1) + 1 if c["parent_id"] is not None else 0
    return out


def _args(reader: Reader, call_id: int) -> dict[str, dict]:
    return {b["name"]: json.loads(b["data"]) for b in reader.bindings(call_id) if b["kind"] == "arg"}


def compare(a: Reader, b: Reader) -> dict:
    """Why two runs took different paths (FR-ANA-7). Each run's calls in pre-order, as (depth, module, qualname), are
    aligned with difflib; the first block that is not equal is the divergence. Reported: its parent call in both
    runs, the calls only in A and only in B under it, the parent's argument differences and the source around the
    lines where the children differ, which is usually the branch that decided. Outcomes are not part of the
    alignment: a failing run's ancestors all raise, and they are still the same calls."""
    sa, sb = _signature(a), _signature(b)
    ka, kb = ([(c["depth"], c["module"], c["qualname"]) for c in side] for side in (sa, sb))
    # The first divergence is where the common prefix ends: found in linear time. difflib only measures the middle
    # (between the common prefix and suffix), capped, for the extent of that first block and the counts.
    n = min(len(ka), len(kb))
    pre = next((i for i in range(n) if ka[i] != kb[i]), n)
    suf = 0
    while suf < n - pre and ka[-1 - suf] == kb[-1 - suf]:
        suf += 1
    mid_a, mid_b = ka[pre:len(ka) - suf], kb[pre:len(kb) - suf]
    capped = len(mid_a) > ALIGN_LIMIT or len(mid_b) > ALIGN_LIMIT
    ops = difflib.SequenceMatcher(None, mid_a[:ALIGN_LIMIT], mid_b[:ALIGN_LIMIT], autojunk=False).get_opcodes()
    summary = {
        "matched": pre + suf + sum(i2 - i1 for tag, i1, i2, *_ in ops if tag == "equal"),
        "only_a": sum(i2 - i1 for tag, i1, i2, *_ in ops if tag != "equal"),
        "only_b": sum(j2 - j1 for tag, _, __, j1, j2 in ops if tag != "equal"),
        "outcome_a": a.run()["outcome"], "outcome_b": b.run()["outcome"],
        "truncated": capped,  # counts cover the first ALIGN_LIMIT calls after the divergence
    }
    first = next(((i1, i2, j1, j2) for tag, i1, i2, j1, j2 in ops if tag != "equal"), None)
    if first is None:
        return {"summary": summary, "divergence": None, "inferred": True}
    i1, i2, j1, j2 = (pre + x for x in first)  # i1 == j1 == pre: the prefix is maximal
    idx_a, idx_b = {c["id"]: i for i, c in enumerate(sa)}, {c["id"]: j for j, c in enumerate(sb)}
    # The parent of the first differing call on each side; both lie in the common prefix, where index i in A is
    # index i in B. The deeper one is where execution split first; the shallower difference is usually its
    # consequence (B raised, so it never got further).
    cands = [idx[side[k]["parent_id"]] for side, idx, k in ((sa, idx_a, i1), (sb, idx_b, j1))
             if k < len(side) and side[k]["parent_id"] in idx]
    pi = max(cands, key=lambda i: sa[i]["depth"], default=None)
    to_b = {i: i for i in range(pre)}
    path_a = []
    i = pi
    while i is not None:  # the parent and its ancestors, root first
        path_a.insert(0, i)
        i = idx_a.get(sa[i]["parent_id"])
    ca, cb = (a.call(sa[pi]["id"]), b.call(sb[to_b[pi]]["id"])) if pi is not None else (None, None)
    pid_a = sa[pi]["id"] if pi is not None else None
    pid_b = sb[to_b[pi]]["id"] if pi is not None else None
    only_a = [sa[i] for i in range(i1, i2) if sa[i]["parent_id"] == pid_a]
    only_b = [sb[j] for j in range(j1, j2) if sb[j]["parent_id"] == pid_b]
    for side, reader in ((only_a, a), (only_b, b)):
        for c in side:
            c["call_line"] = reader.call(c["id"])["call_line"]
    args_a, args_b = (_args(a, ca["id"]), _args(b, cb["id"])) if ca else ({}, {})
    arg_diff = [{"name": n, "same": args_a.get(n) == args_b.get(n),
                 "a": preview(args_a[n]) if n in args_a else None, "b": preview(args_b[n]) if n in args_b else None,
                 "changes": value_diff(args_a[n], args_b[n]) if n in args_a and n in args_b else []}
                for n in list(args_a) + [n for n in args_b if n not in args_a]]
    branch = None
    lines = [c["call_line"] for c in only_a + only_b if c["call_line"]]
    if ca and lines and ca["file_id"] and (src := a.source(ca["file_id"])):
        lo, hi = max(min(lines) - 3, 1), max(lines) + 1
        text = src.split("\n")
        branch = {"file": ca["relpath"] or ca["path"], "first": lo,
                  "lines": text[lo - 1:hi], "a_lines": sorted({c["call_line"] for c in only_a if c["call_line"]}),
                  "b_lines": sorted({c["call_line"] for c in only_b if c["call_line"]})}
    row = lambda c: {k: c[k] for k in ("id", "module", "qualname", "outcome", "duration_ns", "depth")}  # noqa: E731
    return {
        "summary": summary,
        "divergence": {
            "parent_a": ca and ca["id"], "parent_b": cb and cb["id"],
            "parent": ca and {"qualname": ca["qualname"], "module": ca["module"], "file": ca["relpath"] or ca["path"],
                              "line": ca["first_line"]},
            "path": [{"a": row(sa[i]), "b": row(sb[to_b[i]])} for i in path_a],
            "only_a": [{**row(c), "call_line": c["call_line"]} for c in only_a[:20]],
            "only_b": [{**row(c), "call_line": c["call_line"]} for c in only_b[:20]],
            "args": arg_diff, "branch": branch,
        },
        "inferred": True,
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
