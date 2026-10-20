"""Level 1 and Level 2 as Mermaid flowcharts, to paste into a README, docs or a pull request."""

from __future__ import annotations

from vizcontrol import analysis as A
from vizcontrol.store import Reader

_STYLES = ["classDef raised stroke:#e5484d,stroke-width:2px", "classDef library stroke-dasharray:4 3"]


def _ms(ns: int) -> str:
    return f"{ns / 1e6:.1f} ms"


def _label(*lines: str) -> str:
    return '["' + "<br/>".join(t.replace('"', "#quot;") for t in lines) + '"]'


def _calls(n: int) -> str:
    return f"{n} call" if n == 1 else f"{n} calls"


def _edge(a: str, b: str, calls: int, thick: bool = False) -> str:
    arrow = "==>" if thick else "-->"
    return f"  {a} {arrow}|×{calls}| {b}" if calls > 1 else f"  {a} {arrow} {b}"


def stages(reader: Reader, heads: frozenset | None = None) -> str:
    """Level 1: the stages, the calls between them, the main path drawn thick."""
    g = A.stages(reader, heads)
    ids = {n["name"]: f"s{i}" for i, n in enumerate(g["nodes"])}
    out = ["flowchart LR"]
    for n in g["nodes"]:
        cls = ":::raised" if n["raised"] else "" if n["observed"] else ":::library"
        out.append(f"  {ids[n['name']]}{_label(n['name'], f"{_calls(n['calls'])} · {_ms(n['total_ns'])}")}{cls}")
    main = set(zip(g["main_path"], g["main_path"][1:], strict=False))
    out += [_edge(ids[e["from"]], ids[e["to"]], e["calls"], (e["from"], e["to"]) in main) for e in g["edges"]]
    return "\n".join(out + _STYLES) + "\n"


def stage(reader: Reader, name: str, heads: frozenset | None = None) -> str | None:
    """Level 2: one stage as module › class › function subgraphs, neighbouring stages as single nodes."""
    d = A.stage_detail(reader, name, heads)
    if d is None:
        return None
    out = ["flowchart TB", f"  subgraph stage {_label(d['stage'])}"]
    modules: dict[str, dict[str | None, list[dict]]] = {}
    for f in d["functions"]:
        modules.setdefault(f["module"] or "?", {}).setdefault(f["cls"], []).append(f)
    for i, (module, classes) in enumerate(modules.items()):
        out.append(f"    subgraph m{i} {_label(next(iter(classes.values()))[0]['file'] or module)}")
        for j, (cls, fs) in enumerate(classes.items()):
            pad = "      "
            if cls:
                out.append(f"      subgraph m{i}c{j} {_label('class ' + cls)}")
                pad += "  "
            for f in fs:
                title = {"module": "import", "class": "class body"}.get(f["kind"], f["qualname"].split(".")[-1])
                count = f" ×{f['calls']}" if f["calls"] > 1 else ""
                cls_mark = ":::raised" if f["raised"] else ""
                out.append(f"{pad}f{f['code_id']}{_label(title + count, _ms(f['total_ns']))}{cls_mark}")
            if cls:
                out.append("      end")
        out.append("    end")
    out.append("  end")
    near = sorted({x["stage"] for x in d["incoming"] + d["outgoing"] if x["stage"] not in (None, d["stage"])})
    ids = {s: f"n{i}" for i, s in enumerate(near)}
    out += [f"  {ids[s]}{_label(s, 'stage')}" for s in near]
    edges: dict[tuple[str, str], int] = {}
    for e in d["edges"]:
        if e["from"] != e["to"]:
            edges[(f"f{e['from']}", f"f{e['to']}")] = e["calls"]
    for x in d["incoming"]:
        if x["stage"] in ids:
            key = (ids[x["stage"]], f"f{x['to']}")
            edges[key] = edges.get(key, 0) + x["calls"]
    for x in d["outgoing"]:
        key = (f"f{x['from']}", ids[x["stage"]])
        edges[key] = edges.get(key, 0) + x["calls"]
    out += [_edge(a, b, n) for (a, b), n in edges.items()]
    return "\n".join(out + _STYLES) + "\n"
