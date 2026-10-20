"""The `vizcontrol` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vizcontrol.config import LEVELS, load_config

_RUN_VALUE_OPTS = {"-o", "--output", "--out-dir", "--level", "--include", "--exclude", "--config"}


def _split_run(argv: list[str]) -> tuple[list[str], str, str, list[str]]:
    """Split `run` arguments into (our options, 'script'|'module', target, target args), like `python` does."""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "-m":
            if i + 1 >= len(argv):
                raise SystemExit("vizcontrol run: -m needs a module name")
            return argv[:i], "module", argv[i + 1], argv[i + 2:]
        if arg in _RUN_VALUE_OPTS:
            i += 2
        elif arg.startswith("-"):
            i += 1
        else:
            return argv[:i], "script", arg, argv[i + 1:]
    raise SystemExit("vizcontrol run: expected a script path or -m module")


def _run(argv: list[str]) -> int:
    from vizcontrol.runners import default_path, run_module, run_script

    ours, kind, target, target_args = _split_run(argv)
    p = argparse.ArgumentParser(prog="vizcontrol run")
    p.add_argument("-o", "--output")
    p.add_argument("--out-dir")
    p.add_argument("--level", choices=LEVELS)
    p.add_argument("--include", action="append")
    p.add_argument("--exclude", action="append")
    p.add_argument("--config")
    p.add_argument("--serve", action="store_true", help="then open the run in the browser")
    opts = p.parse_args(ours)
    try:
        config = load_config(opts.config, level=opts.level, out_dir=opts.out_dir,
                             include=tuple(opts.include) if opts.include else None,
                             exclude=tuple(opts.exclude) if opts.exclude else None)
    except (ValueError, OSError) as e:
        print(f"vizcontrol: {e}", file=sys.stderr)
        return 2
    if kind == "script" and not Path(target).is_file():
        print(f"vizcontrol: can't open file {target!r}", file=sys.stderr)
        return 2
    runner = run_script if kind == "script" else run_module
    output = Path(opts.output) if opts.output else default_path(config.out_dir, target)
    code = runner(target, target_args, config, output)
    if not opts.serve:
        default = output.parent.resolve() == Path(load_config().out_dir).resolve()  # `vizcontrol serve` finds it
        where = "" if default else f" {output.parent}"
        print(f"vizcontrol: explore it with: vizcontrol serve{where}  (or trace with run --serve)", file=sys.stderr)
        return code
    from vizcontrol.server import serve

    try:
        serve(output.parent, run=output.name)
    except OSError:  # the default port is taken (another viewer): any free one
        serve(output.parent, port=0, run=output.name)
    return code


def _duration(run: dict) -> str:
    if run["ended_ns"] is None:
        return "—"
    ms = (run["ended_ns"] - run["started_ns"]) / 1e6
    return f"{ms:.1f} ms" if ms < 1000 else f"{ms / 1000:.2f} s"


def _info(path: str) -> int:
    from vizcontrol.store import Reader

    try:
        r = Reader(path)
    except (FileNotFoundError, ValueError) as e:
        print(f"vizcontrol: {e}", file=sys.stderr)
        return 1
    run = r.run()
    print(f"{run['kind']} {run['target']}")
    print(f"  outcome   {run['outcome']} (exit {run['exit_code']})  ·  {_duration(run)}")
    print(f"  level     {run['config']['level']}  ·  python {run['python'].split()[0]}")
    print(f"  truncated {', '.join(run['truncated']) or 'no'}")
    print("  rows      " + "  ".join(f"{t} {n}" for t, n in r.counts().items()))
    for root in r.children(None, limit=10):
        print(f"  root      {root['qualname']}  [{root['outcome']}]  {root['children']} children")
    for exc in r.exceptions():
        if exc["handled_call_id"] is None:
            print(f"  escaped   {exc['type']}: {exc['message']}  (line {exc['raised_line']})")
    return 0


def _diagram(path: str, stage: str | None) -> int:
    from vizcontrol import analysis, diagram
    from vizcontrol.store import Reader

    try:
        r = Reader(path)
    except (FileNotFoundError, ValueError) as e:
        print(f"vizcontrol: {e}", file=sys.stderr)
        return 1
    text = diagram.stages(r) if stage is None else diagram.stage(r, stage)
    if text is None:
        print(f"vizcontrol: no stage {stage!r}; the stages are: "
              + ", ".join(n["name"] for n in analysis.stages(r)["nodes"]),
              file=sys.stderr)
        return 1
    print(text, end="")
    return 0


def _change(name: str, c: dict) -> str:
    field = name + ("" if c["path"] == "." else c["path"])
    if c["op"] == "added":
        return f"+ {field} = {c['after']}"
    if c["op"] == "removed":
        return f"- {field} (was {c['before']})"
    if c["op"] == "unknown":
        return f"? {field} (not fully captured)"
    return f"~ {field} {c['before']} -> {c['after']}"


def _diff(path_a: str, path_b: str) -> int:
    """Like diff(1): 0 when the runs made the same calls with the same results, 1 when they differ, 2 on trouble."""
    from vizcontrol import analysis
    from vizcontrol.store import Reader

    try:
        a, b = Reader(path_a), Reader(path_b)
    except (FileNotFoundError, ValueError) as e:
        print(f"vizcontrol: {e}", file=sys.stderr)
        return 2
    d = analysis.compare(a, b)
    s, div = d["summary"], d["divergence"]
    for side, path, r in (("A", path_a, a), ("B", path_b, b)):
        print(f"{side}  {path}  {r.run()['outcome']}")
    print(f"matched {s['matched']} · only in A {s['only_a']} · only in B {s['only_b']} · "
          f"different result {s['returns_differ']}" + (" (counts capped)" if s["truncated"] else ""))
    if div:
        p = div["parent"]
        where = f"{p['qualname']} ({p['file']}:{p['line']})" if p else "the run itself"
        print(f"\nfirst divergence in {where}")
        print("  path      " + " > ".join(x["a"]["qualname"] for x in div["path"]))
        for side, calls in (("A", div["only_a"]), ("B", div["only_b"])):
            names = ", ".join(c["qualname"] + (f" (line {c['call_line']})" if c["call_line"] else "") for c in calls)
            print(f"  only in {side} {names or 'nothing more'}")
        for arg in div["args"]:
            for c in arg["changes"]:
                print(f"  {_change(arg['name'], c)}")
        if div["branch"]:
            br = div["branch"]
            print(f"  branch    {br['file']}")
            for i, line in enumerate(br["lines"], br["first"]):
                mark = "   # B" if i in br["b_lines"] else "   # A" if i in br["a_lines"] else ""
                print(f"  {i:>6}  {line}{mark}")
    if d["returns"]:
        print(f"\ndifferent results (first {len(d['returns'])} of {s['returns_differ']}, in the order they returned)")
        for r in d["returns"]:
            print(f"  {r['qualname']}  {r['a_value']}  ->  {r['b_value']}")
            for c in r["changes"][:3]:
                print(f"      {_change('return', c)}")
    return 1 if div or d["returns"] else 0


def _ls(directory: str) -> int:
    from vizcontrol.store import Reader

    files = sorted(Path(directory).glob("*.vizc"))
    if not files:
        print(f"no runs in {directory}")
        return 0
    for f in files:
        try:
            r = Reader(f)
            run, calls = r.run(), r.counts()["call"]
            print(f"{f.name:<60} {run['outcome']:<17} {_duration(run):>10} {calls:>9} calls")
        except Exception as e:  # one broken file shouldn't hide the others
            print(f"{f.name:<60} unreadable: {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["run"]:
        return _run(argv[1:])
    p = argparse.ArgumentParser(prog="vizcontrol", description="Observe real Python executions as graphs.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="trace a script or module: vizcontrol run [options] script.py|-m module [args...]")
    info = sub.add_parser("info", help="summary of a .vizc file")
    info.add_argument("file")
    ls = sub.add_parser("ls", help="list runs in a directory")
    ls.add_argument("dir", nargs="?", default=load_config().out_dir)
    dia = sub.add_parser("diagram", help="a run's stages (or one stage opened) as a Mermaid flowchart")
    dia.add_argument("file")
    dia.add_argument("--stage", help="open this stage into its modules, classes and functions (Level 2)")
    dif = sub.add_parser("diff", help="where two runs split, and which calls returned something else")
    dif.add_argument("a")
    dif.add_argument("b")
    srv = sub.add_parser("serve", help="explore runs in the browser")
    srv.add_argument("path", nargs="?", default=load_config().out_dir, help="a .vizc file or a directory of runs")
    srv.add_argument("--port", type=int, default=7171)
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)
    if args.command == "info":
        return _info(args.file)
    if args.command == "diff":
        return _diff(args.a, args.b)
    if args.command == "diagram":
        return _diagram(args.file, args.stage)
    if args.command == "serve":
        from vizcontrol.server import serve

        if not Path(args.path).exists():
            print(f"vizcontrol: no runs yet: {args.path} does not exist. Trace a program first: "
                  "vizcontrol run --serve app.py", file=sys.stderr)
            return 1
        try:
            return serve(args.path, args.host, args.port, not args.no_browser)
        except OSError as e:
            print(f"vizcontrol: {e}", file=sys.stderr)
            return 1
    return _ls(args.dir)
