"""Turn stored, observed events into what the viewer navigates. Pure functions over a Reader.

Everything computed here (stages, groups, flow links, change attribution) is *inferred* and says so.
See docs/components/analysis.md.
"""

from __future__ import annotations

import bisect
import difflib
import fnmatch
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

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
            stager: Stager | None = None, head: tuple | None = None) -> list[dict]:
    """Children of `call_id` (roots if None), `depth` levels deep. Siblings running the same code fold into one
    group node (FR-ANA-3): a 10,000-iteration loop is one node until expanded with `group_members`. With a
    `stager`, nodes carry their stage; `head` is `stager.head_of(reader, call_id)`."""
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
            stage, below = stager.step(c["module"], c["observed"], c["code_id"], head) if stager else (None, None)
            node = call_node(c, stage)
            if depth > 1 and c["children"]:
                node["items"] = subtree(reader, c["id"], depth - 1, limit, stager, below)
        else:
            s = samples[g["code_id"]]
            node = {
                "type": "group", "parent_id": call_id, "code_id": g["code_id"], "qualname": s["qualname"],
                "module": s["module"], "kind": s["kind"], "observed": bool(s["observed"]), "count": g["count"],
                "outcomes": {"raised": g["raised"], "unfinished": g["unfinished"],
                             "ok": g["count"] - g["raised"] - g["unfinished"]},
                "total_ns": g["total_ns"], "first_call_id": g["first_id"], "seq": g["first_seq"],
                "stage": stager.step(s["module"], s["observed"], g["code_id"], head)[0] if stager else None,
                "inferred": True,
            }
        out.append(node)
    if len(groups) > limit:
        out.append({"type": "more", "parent_id": call_id, "remaining": len(groups) - limit})
    return out


def group_members(reader: Reader, call_id: int | None, code_id: int, offset: int = 0, limit: int = 200) -> list[dict]:
    return [call_node(c) for c in reader.group_members(call_id, code_id, offset, limit)]


# -- stages (Level 1) ---------------------------------------------------------------------------------------------

def stage_namer(config: dict, main: str | None = None, depth: int | None = None):
    """Map (module, observed) to a stage name: user rules first, then the module path cut to the configured
    `stage_depth`, else to `depth` (default 2). `main` names the `__main__` module (the script or `-m` module)."""
    rules = config.get("stages") or {}
    depth = config.get("stage_depth") or depth or 2
    cache: dict[tuple, str] = {}

    def stage_of(module: str | None, observed) -> str:
        key = (module, bool(observed))
        name = cache.get(key)
        if name is None:
            if module == "__main__" and main:
                module = main
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


MIN_STAGES = 3  # a project in fewer module stages than this is split into the functions it fans out to
MAX_STAGES = 12  # a project in more module stages than this is staged by top-level package
LOOP_SHARE = 0.9  # a callee with this share of its siblings' calls is a loop body: the split goes on below it


def main_module(run: dict) -> str | None:
    """What `__main__` stands for: the script's name, or the module of `python -m`."""
    if run["kind"] == "script":
        return Path(run["target"]).stem
    return run["target"] if run["kind"] == "module" else None


def module_stages(reader: Reader, depth: int | None = None) -> set[str]:
    """The observed module stages of a run: what decides the stage depth and whether a project is split into
    functions."""
    run = reader.run()
    namer = stage_namer(run["config"], main_module(run), depth)
    return {namer(c["module"], True) for c in reader.codes() if c["observed"]}


def auto_depth(names: set[str]) -> int:
    """The stage depth for a project whose depth-2 module stages are `names`: 2, or 1 when that is too many to
    read at a glance (a package of 20 modules is one stage, opened at Level 2)."""
    return 1 if len(names) > MAX_STAGES else 2


class Stager:
    """Which stage each call belongs to (inferred). By module path (`stage_namer`), except below a head function:
    a code matched by a `module:function` rule, or, when the project is split (fewer than MIN_STAGES module stages),
    one of the functions a module stage fans out to. Fanning out starts at the codes entering the stage (root
    calls, or calls from another stage) and goes past lone callees (`main()` -> `run()`) and loop bodies (a callee
    with LOOP_SHARE of its siblings' calls, `process()` x 1000 next to `setup()`) to the first caller with two
    or more. A head's stage takes the calls below it in the head's own module stage; other modules keep theirs.
    `heads` decides for a set of runs at once (`vizcontrol serve` of a directory), as the (module, qualname) of
    every head any of them found, so a function is a stage in all of them; None decides from this run alone.
    `depth` likewise: the module path depth for a set of runs (`auto_depth`), None decides from this run alone; a
    configured `stage_depth` wins over both."""

    def __init__(self, reader: Reader, heads: frozenset[tuple[str, str]] | None = None, depth: int | None = None):
        run = reader.run()
        config = run["config"]
        if depth is None:
            depth = auto_depth(module_stages(reader, 2))
        self.namer = stage_namer(config, main_module(run), depth)
        codes = {c["id"]: c for c in reader.codes()}
        self.heads: dict[int, tuple[str, str]] = {}  # code_id -> (its stage, the module stage it splits)
        self.names: frozenset[tuple[str, str]] = frozenset()  # (module, qualname) of the heads found
        rules = [(s, g) for s, globs in (config.get("stages") or {}).items() for g in globs if ":" in g]
        for c in codes.values():
            s = next((s for s, g in rules if fnmatch.fnmatch(f"{c['module']}:{c['qualname']}", g)), None)
            if s is not None:
                self.heads[c["id"]] = (s, self.namer(c["module"], c["observed"]))
        if rules:
            return
        if heads is None and len(module_stages(reader, 2)) < MIN_STAGES:  # few modules, not few after grouping
            self._fan_out(reader, codes)
        for c in codes.values():
            if heads and (c["module"], c["qualname"]) in heads:
                self.heads[c["id"]] = (c["qualname"], self.namer(c["module"], c["observed"]))
        self.names = frozenset((codes[k]["module"], codes[k]["qualname"]) for k in self.heads)

    def _fan_out(self, reader: Reader, codes: dict[int, dict]) -> None:
        base = {k: self.namer(c["module"], c["observed"]) for k, c in codes.items()}
        # caller -> callee -> calls; ("root", stage) and ("in", stage) stand for root calls and calls from elsewhere
        callees: dict[object, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for caller, k, n in reader.code_calls():
            if not codes[k]["observed"]:
                continue
            if caller is None or base[caller] != base[k]:
                callees[("root" if caller is None else "in", base[k])][k] += n
            elif codes[k]["kind"] not in ("module", "class"):
                callees[caller][k] += n
        for stage in {base[k] for k, c in codes.items() if c["observed"]}:
            seen: set[int] = set()
            # the program's own entry first: callbacks from a library (doctest, a test runner) enter afterwards
            for frontier in ([("root", stage)], [("in", stage)]):
                while frontier:
                    lone = []
                    for caller in frontier:
                        calls = callees[caller]
                        ks = set(calls) - seen
                        seen |= ks
                        fns = {k for k in ks if codes[k]["kind"] not in ("module", "class")}
                        body = max(fns, key=calls.get, default=None)
                        if len(fns) > 1 and calls[body] >= LOOP_SHARE * sum(calls[k] for k in fns):
                            ks -= fns - {body}  # the loop body leads on; its few siblings stay in this stage
                        elif len(fns) > 1:
                            self.heads.update((k, (codes[k]["qualname"], stage)) for k in fns)
                            ks -= fns
                        lone += ks  # a lone function, an import or a class body: keep looking below it
                    frontier = lone

    def step(self, module: str | None, observed, code_id: int, head: tuple | None) -> tuple[str, tuple | None]:
        """(stage of a call, the head that applies to its callees), given the head that applies to the call."""
        h = self.heads.get(code_id)
        if h is not None:
            return h[0], h
        base = self.namer(module, observed)
        return (head[0] if head and head[1] == base else base), head

    def assign(self, rows: list[tuple]) -> dict[int, str]:
        """call id -> stage, for `Reader.all_calls()` rows (parents come before their children)."""
        stage: dict[int, str] = {}
        head: dict[int, tuple | None] = {}
        for cid, parent, _, _, _, _, module, observed, _, code_id in rows:
            stage[cid], head[cid] = self.step(module, observed, code_id, head.get(parent))
        return stage

    def path(self, chain: list[dict]) -> list[str]:
        """Stages of a root-first chain of calls, as `Reader.ancestors` returns it."""
        out, head = [], None
        for c in chain:
            s, head = self.step(c["module"], c["observed"], c["code_id"], head)
            out.append(s)
        return out

    def of(self, reader: Reader, c: dict) -> str:
        if not self.heads:
            return self.namer(c["module"], c["observed"])
        return self.path(reader.ancestors(c["id"]))[-1]

    def head_of(self, reader: Reader, call_id: int | None) -> tuple | None:
        """The head that applies to the callees of `call_id`."""
        head = None
        if call_id is not None and self.heads:
            for c in reader.ancestors(call_id):
                head = self.step(c["module"], c["observed"], c["code_id"], head)[1]
        return head


def _ends(rows: list[tuple]) -> dict[int, int]:
    """call id -> end, for `Reader.all_calls()` rows. A call with no end (open when tracing stopped at max_calls or
    a kill, a generator never run to the end) ends at the last thing observed inside it."""
    ends: dict[int, int] = {}
    for cid, parent, start, end, *_ in reversed(rows):  # children before their parents
        e = ends[cid] = end if end is not None else max(ends.get(cid, start), start)
        if parent is not None and e > ends.get(parent, 0):
            ends[parent] = e
    return ends


def stages(reader: Reader, heads: frozenset | None = None, depth: int | None = None) -> dict:
    """Stage graph for the Level-1 overview (FR-ANA-2). One pass over all calls."""
    rows = reader.all_calls()
    stage_by_call = Stager(reader, heads, depth).assign(rows)
    ends = _ends(rows)
    dur: dict[int, int] = {}
    child_dur: dict[int, int] = defaultdict(int)
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    roots: list[str] = []
    inside: dict[int, frozenset] = {}  # call -> stages open on its path, so a re-entered stage counts its time once
    for cid, parent, start, _, outcome, seq, module, observed, kind, _ in rows:
        stage = stage_by_call[cid]
        d = dur[cid] = ends[cid] - start
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
        above = inside.get(parent, frozenset())
        inside[cid] = above if stage in above else above | {stage}
        if stage not in above:
            n["total_ns"] += d  # time spent in a stage counts from where it is entered
        if parent is None:
            roots.append(stage)
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
        # json, re, functools...: an overview hides them unless they raised; third-party libraries stay
        n["stdlib"] = not n["observed"] and n["name"] in sys.stdlib_module_names
    return {"nodes": sorted(nodes.values(), key=lambda n: n["first_seq"]),
            "edges": sorted(edges.values(), key=lambda e: e["first_seq"]),
            "main_path": _main_path(roots, edges, {n["name"] for n in nodes.values() if n["stdlib"]}),
            "inferred": True}


def _main_path(roots: list[str], edges: dict[tuple[str, str], dict], skip: set[str]) -> list[str]:
    """From the entry stage, keep following the outgoing edge that leads to the most calls (its subtree size; time
    only breaks ties) to a stage not yet on the path nor in `skip` (the standard library). Call counts are
    deterministic, timings are noise in small runs."""
    if not roots:
        return []
    out: dict[str, list[dict]] = defaultdict(list)
    for e in edges.values():
        out[e["from"]].append(e)
    path = [roots[0]]
    while True:
        nxt = [e for e in out[path[-1]] if e["to"] not in path and e["to"] not in skip]
        if not nxt:
            return path
        path.append(max(nxt, key=lambda e: (e["calls_below"], e["total_ns"], -e["first_seq"]))["to"])


def route_map(graphs: dict[str, dict], outcomes: dict[str, str]) -> dict:
    """Level 1 over many runs (a test suite, requests to an app): every stage and stage edge that any run went
    through, each with the runs that did, and each run's route. `graphs` are the runs' `stages()`, staged alike."""
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    routes, entries = [], defaultdict(int)
    for i, (run_id, g) in enumerate(graphs.items()):
        for n in g["nodes"]:
            m = nodes.get(n["name"])
            if m is None:
                m = nodes[n["name"]] = {**n, "calls": 0, "total_ns": 0, "self_ns": 0, "raised": 0, "unfinished": 0,
                                        "modules": set(), "runs": [], "order": (i, n["first_seq"])}
            for k in ("calls", "total_ns", "self_ns", "raised", "unfinished"):
                m[k] += n[k]
            m["modules"].update(n["modules"])
            m["observed"] = m["observed"] or n["observed"]
            m["runs"].append(run_id)
        for e in g["edges"]:
            m = edges.get((e["from"], e["to"]))
            if m is None:
                m = edges[(e["from"], e["to"])] = {**e, "calls": 0, "total_ns": 0, "calls_below": 0, "runs": [],
                                                   "order": (i, e["first_seq"])}
            for k in ("calls", "total_ns", "calls_below"):
                m[k] += e[k]
            m["runs"].append(run_id)
        if g["main_path"]:
            entries[g["main_path"][0]] += 1
        routes.append({"run": run_id, "outcome": outcomes.get(run_id), "stages": [n["name"] for n in g["nodes"]],
                       "edges": [[e["from"], e["to"]] for e in g["edges"]]})
    for m in nodes.values():
        m["modules"] = sorted(m["modules"])
    main = _main_path(sorted(entries, key=lambda s: -entries[s]), edges,  # from the most common entry
                      {n["name"] for n in nodes.values() if n["stdlib"]})
    ns, es = sorted(nodes.values(), key=lambda n: n["order"]), sorted(edges.values(), key=lambda e: e["order"])
    for x in ns + es:
        del x["order"]
    return {"nodes": ns, "edges": es, "main_path": main, "routes": routes, "inferred": True}


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


def stage_detail(reader: Reader, stage: str, heads: frozenset | None = None, depth: int | None = None) -> dict | None:
    """Level 2 (FR-VIZ-2): one stage opened into module › class › function, one node per function (its calls
    aggregated), the calls between them, and the calls crossing the stage boundary per (caller, callee) pair.
    ponytail: every function of the stage is returned; page them if a stage ever has thousands."""
    codes = {c["id"]: c for c in reader.codes()}
    rows = reader.all_calls()
    stage_by_call = Stager(reader, heads, depth).assign(rows)
    ends = _ends(rows)
    inside: dict[int, bool] = {}  # call -> the stage is open on its path: its time is counted once, where entered
    code_by_call: dict[int, int] = {}
    dur: dict[int, int] = {}
    child_dur: dict[int, int] = defaultdict(int)
    funcs: dict[int, dict] = {}
    internal: dict[tuple, int] = defaultdict(int)
    incoming: dict[tuple, int] = defaultdict(int)
    outgoing: dict[tuple, int] = defaultdict(int)
    total = 0
    for cid, parent, start, _, outcome, seq, module, observed, kind, code_id in rows:
        s = stage_by_call[cid]
        code_by_call[cid] = code_id
        inside[cid] = s == stage or inside.get(parent, False)
        d = dur[cid] = ends[cid] - start
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
            if parent is None or code_by_call.get(parent) != code_id:  # ponytail: direct recursion only, not a->b->a
                f["total_ns"] += d
            f["raised"] += outcome == "raised"
            f["unfinished"] += outcome == "unfinished"
            if pstage == stage:
                internal[(code_by_call[parent], code_id)] += 1
            else:
                total += 0 if inside.get(parent) else d
                incoming[(pstage, code_by_call.get(parent), code_id)] += 1
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
                  # ponytail: a generator's span includes the time it sat suspended while its consumer ran
                  "self_ns": min(sum(f["self_ns"] for f in fs), total)},
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
            "changes": changes, **_derived(reader, object_id, appearances)}


def _derived(reader: Reader, oid: int, appearances: list[dict], limit: int = 500) -> dict:
    """Where the data went when it changed objects (inferred): `became`, what calls that took this one in returned
    (a new object, or a value such as a string); `made_from`, the objects passed to the call that returned this
    one. Identity alone stops at a function that builds a new list or record; this is the next hop."""
    def hop(rows: list[dict], values: bool) -> list[dict]:
        out, seen = [], {oid}
        for b in rows:
            data = json.loads(b["data"])
            if b["object_id"] in seen or b["name"] == "self":  # the method's own object is not the data
                continue
            if b["object_id"] is None and (not values or data["t"] == "none"):
                continue
            if _find(data, oid) is not None:  # still holds this object: not a different piece of data
                continue
            if b["object_id"] is not None:
                seen.add(b["object_id"])
            out.append({"call_id": b["call_id"], "qualname": b["qualname"], "name": b["name"],
                        "object_id": b["object_id"], "type": b["type"], "preview": preview(data, 80), "inferred": True})
        return out
    took = list(dict.fromkeys(a["call_id"] for a in appearances if a["role"] == "arg"))[:limit]
    made = list(dict.fromkeys(a["call_id"] for a in appearances if a["role"] == "return" and a["path"] == ""))[:limit]
    return {"became": hop(reader.top_bindings(took, "return"), values=True),  # a str or number result too
            "made_from": hop(reader.top_bindings(made, "arg"), values=False)}


# -- errors -------------------------------------------------------------------------------------------------------

def _binding_view(b: dict) -> dict:
    data = json.loads(b["data"])
    return {"name": b["name"], "kind": b["kind"], "value_id": b["value_id"], "object_id": b["object_id"],
            "type": b["type"], "preview": preview(data)}


def error_path(reader: Reader, heads: frozenset | None = None, depth: int | None = None) -> dict | None:
    """The exception that ended the run: where it started, how execution got there, how it propagated,
    and the latest observed changes to the objects in scope (FR-ANA-5)."""
    escaped = [e for e in reader.exceptions() if e["handled_call_id"] is None and e["raised_call_id"] is not None]
    if not escaped or reader.run()["outcome"] == "ok":  # sys.exit(0), pytest.raises: nothing failed
        return None
    exc = escaped[-1]
    path = reader.ancestors(exc["raised_call_id"])
    origin = path[-1]
    stages_on_path = Stager(reader, heads, depth).path(path)
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
        "origin": call_node(origin, stages_on_path[-1]),
        "path": [call_node(c, s) for c, s in zip(path, stages_on_path, strict=True)],
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


_OID = re.compile(r'"oid":\d+')


def _returns(reader: Reader) -> dict[int, tuple[int, int]]:
    """call id -> (seq of its return, hash of the value with object ids blanked: they differ between runs).
    ponytail: one entry per call in memory; stream both runs in order if runs of millions of calls need it."""
    return {cid: (seq, hash(_OID.sub('"oid":0', data))) for cid, seq, data in reader.returns()}


def _return_diffs(a: Reader, b: Reader, sa: list[dict], sb: list[dict], pairs, limit: int = 20) -> tuple[int, list]:
    """Matched calls whose return values differ, in the order they returned in A: the first one is usually where
    the results started to differ, the later ones (its callers) carry it along."""
    ra, rb = _returns(a), _returns(b)
    found = sorted((ra[sa[i]["id"]][0], i, j) for i, j in pairs
                   if sa[i]["id"] in ra and sb[j]["id"] in rb and ra[sa[i]["id"]][1] != rb[sb[j]["id"]][1])
    out = []
    for _, i, j in found[:limit]:
        va, vb = (next(json.loads(x["data"]) for x in r.bindings(c["id"]) if x["kind"] == "return")
                  for r, c in ((a, sa[i]), (b, sb[j])))
        out.append({"a": sa[i]["id"], "b": sb[j]["id"], "qualname": sa[i]["qualname"], "module": sa[i]["module"],
                    "a_value": preview(va), "b_value": preview(vb), "changes": value_diff(va, vb)[:10]})
    return len(found), out


def compare(a: Reader, b: Reader) -> dict:
    """Why two runs took different paths (FR-ANA-7). Each run's calls in pre-order, as (depth, module, qualname), are
    aligned with difflib; the first block that is not equal is the divergence. Reported: its parent call in both
    runs, the calls only in A and only in B under it, the parent's argument differences and the source around the
    lines where the children differ, which is usually the branch that decided. Outcomes are not part of the
    alignment: a failing run's ancestors all raise, and they are still the same calls. Also reported: matched calls
    that returned something different (`returns`), which is how two runs on different inputs differ when they
    take the same path."""
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
    pairs = [(i, i) for i in range(pre)] + [(len(ka) - 1 - k, len(kb) - 1 - k) for k in range(suf)] + [
        (pre + i1 + t, pre + j1 + t) for tag, i1, i2, j1, _ in ops if tag == "equal" for t in range(i2 - i1)]
    returns_differ, returns = _return_diffs(a, b, sa, sb, pairs)
    summary = {
        "matched": len(pairs), "returns_differ": returns_differ,
        "only_a": sum(i2 - i1 for tag, i1, i2, *_ in ops if tag != "equal"),
        "only_b": sum(j2 - j1 for tag, _, __, j1, j2 in ops if tag != "equal"),
        "outcome_a": a.run()["outcome"], "outcome_b": b.run()["outcome"],
        "truncated": capped,  # counts cover the first ALIGN_LIMIT calls after the divergence
    }
    first = next(((i1, i2, j1, j2) for tag, i1, i2, j1, j2 in ops if tag != "equal"), None)
    if first is None:
        return {"summary": summary, "divergence": None, "returns": returns, "inferred": True}
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
        "returns": returns,
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


# -- replay: the run as a recording ------------------------------------------------------------------------------

class Timeline:
    """Every call's start and end in time order: the steps of a replay. Built once per run and paged.
    ponytail: three ints per step in memory; a run of millions of calls costs ~100 MB here, page from SQL if needed."""

    def __init__(self, reader: Reader):
        steps = []
        for cid, start, end, outcome in reader.call_events():
            steps.append((start, 0, cid))
            if end is not None:  # an unfinished call never ends
                steps.append((end, 2 if outcome == "raised" else 1, cid))
        steps.sort()
        self.steps = steps
        self.raises = [i for i, s in enumerate(steps) if s[1] == 2]

    def index_at(self, seq: int) -> int:
        """The first step at or after `seq`."""
        return bisect.bisect_left(self.steps, (seq,))

    def page(self, reader: Reader, offset: int, limit: int) -> dict:
        window = self.steps[offset:offset + limit]
        ids = sorted({cid for _, _, cid in window})
        calls = {c["id"]: c for c in reader.calls(ids)}
        called_back = callbacks(reader, [calls[cid] for _, kind, cid in window if kind == 0])
        values: dict[tuple, list] = defaultdict(list)  # (call, seq) -> bindings made at that step
        raised: dict[int, list] = defaultdict(list)  # call -> locals where its exception was raised
        for b in reader.bindings_of(ids):
            v = {"name": b["name"], "kind": b["kind"], "preview": preview(json.loads(b["data"])),
                 "object_id": b["object_id"], "value_id": b["value_id"], "type": b["type"]}
            (raised[b["call_id"]] if b["kind"] == "raise" else values[(b["call_id"], b["seq"])]).append(v)
        out = []
        for i, (seq, kind, cid) in enumerate(window, offset):
            c = calls[cid]
            step = {"i": i, "seq": seq, "kind": ("call", "return", "raise")[kind], "call_id": cid,
                    "parent_id": c["parent_id"], "depth": c["depth"], "code_id": c["code_id"],
                    "qualname": c["qualname"], "module": c["module"], "observed": bool(c["observed"]),
                    "line": c["call_line"], "file": c["relpath"] or c["path"], "first_line": c["first_line"],
                    "values": values.get((cid, seq), [])}
            if kind == 0 and cid in called_back:
                step["callback"] = called_back[cid]
            if kind == 2:
                exc = reader.exception(c["exception_id"]) if c["exception_id"] else None
                step["exception"] = exc and {"type": exc["type"], "message": exc["message"],
                                             "origin": exc["raised_call_id"] == cid, "line": exc["raised_line"]}
                step["values"] = raised.get(cid, [])
            out.append(step)
        return {"total": len(self.steps), "offset": offset, "steps": out, "raises": self.raises}


def call_graph(reader: Reader) -> dict:
    """Functions as nodes, "A called B" as edges, each with when it first happened (seq): a replay reveals them in
    that order."""
    nodes, edges = reader.code_graph()
    for n in nodes:
        n["observed"] = bool(n["observed"])
    called_back = callbacks(reader, reader.calls([e["first_call"] for e in edges]))  # an edge is as its first call
    for e in edges:
        cb = called_back.get(e.pop("first_call"))
        e["callback"] = cb and {k: cb[k] for k in ("by", "via", "line") if k in cb}
    return {"nodes": nodes, "edges": edges}


def watch(reader: Reader, name: str, limit: int = 5000) -> list[dict]:
    """Every value a variable called `name` had, anywhere in the run, in time order: a watch at any step is the
    last one at or before it."""
    return [{"seq": b["seq"], "call_id": b["call_id"], "qualname": b["qualname"], "kind": b["kind"],
             "preview": preview(json.loads(b["data"])), "object_id": b["object_id"], "value_id": b["value_id"],
             "type": b["type"]} for b in reader.named_bindings(name, limit)]


def _called_by_name(qualname: str, lines: list[str]) -> bool:
    """Whether the caller's source names the callee in a call (`clean_rows(...)`, `self.save(...)`, `Pipeline(...)`
    for `Pipeline.__init__`). Dunder methods run by protocol (`len()`, `for`) and lambdas count as named: nothing to
    tell apart there. The lines are the call's and the two before it, for calls written over several lines."""
    parts = qualname.split(".")
    name = parts[-2] if parts[-1] in ("__init__", "__new__") and len(parts) > 1 else parts[-1]
    if (name.startswith("__") and name.endswith("__")) or name.startswith("<"):
        return True
    return any(re.search(rf"\b{re.escape(name)}\s*\(", line) for line in lines)


def _find_label(d: dict, label: str, path: str) -> str | None:
    """Where a function with this label sits inside an encoded value: `steps[1]`, `self.handlers['done'][0]`."""
    if d.get("t") == "opaque" and d.get("label") == label:
        return path
    for key, child in _children(d):
        found = _find_label(child, label, path + key)
        if found is not None:
            return found
    return None


def callbacks(reader: Reader, calls: list[dict]) -> dict[int, dict]:
    """call id -> how it was called, for the calls of `calls` (`Reader.calls` rows) that were not called by name
    (inferred). A library calling back (`by`: the library function), or a function called through a variable: a
    step passed in, a handler stored on an object, a key given to sorted(). For those: the caller's source line,
    where among the caller's arguments the function was (`via`), and the first value it was seen in (`registered`:
    the call that received or returned it). ponytail: one scan of the value table per distinct callback."""
    parents = {p["id"]: p for p in reader.calls(sorted({c["parent_id"] for c in calls if c["parent_id"]}))}
    args: dict[int, list[dict]] = defaultdict(list)
    for b in reader.bindings_of(list(parents)):
        if b["kind"] == "arg":
            args[b["call_id"]].append(b)
    sources: dict[int, list[str]] = {}
    seen: dict[str, dict | None] = {}
    out = {}
    for c in calls:
        p = parents.get(c["parent_id"])
        if p is None or c["kind"] in ("module", "class") or not c["observed"]:
            continue
        if not p["observed"]:
            out[c["id"]] = {"by": f"{p['module']}.{p['qualname']}" if p["module"] else p["qualname"], "inferred": True}
            continue
        if c["call_line"] is None or p["file_id"] is None:
            continue
        if p["file_id"] not in sources:
            sources[p["file_id"]] = (reader.source(p["file_id"]) or "").splitlines()
        lines = sources[p["file_id"]][max(c["call_line"] - 3, 0):c["call_line"]]
        if not lines or _called_by_name(c["qualname"], lines):
            continue
        label = f"{c['module']}.{c['qualname']}"
        cb = {"line": lines[-1].split("  #")[0].strip(), "at": c["call_line"], "inferred": True}  # no trailing comment
        for b in args[p["id"]]:
            via = _find_label(json.loads(b["data"]), label, b["name"] or "")
            if via is not None:
                cb["via"] = via
                break
        if label not in seen:
            first = reader.first_binding_with(f'"label":{json.dumps(label)}')
            seen[label] = first and {
                "call_id": first["call_id"], "qualname": first["qualname"], "seq": first["seq"], "kind": first["kind"],
                "path": _find_label(json.loads(first["data"]), label, first["name"] or "")}
        if seen[label]:
            cb["registered"] = seen[label]
        out[c["id"]] = cb
    return out
