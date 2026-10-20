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
    from vizcontrol.runners import run_module, run_script

    ours, kind, target, target_args = _split_run(argv)
    p = argparse.ArgumentParser(prog="vizcontrol run")
    p.add_argument("-o", "--output")
    p.add_argument("--out-dir")
    p.add_argument("--level", choices=LEVELS)
    p.add_argument("--include", action="append")
    p.add_argument("--exclude", action="append")
    p.add_argument("--config")
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
    return runner(target, target_args, config, opts.output)


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
    args = p.parse_args(argv)
    if args.command == "info":
        return _info(args.file)
    return _ls(args.dir)
