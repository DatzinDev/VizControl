"""Start user code under capture with unchanged semantics. One run = one .vizc file."""

from __future__ import annotations

import atexit
import dataclasses
import datetime
import functools
import json
import os
import re
import runpy
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from vizcontrol.capture import Tracer
from vizcontrol.config import Config, load_config
from vizcontrol.serialize import Encoder
from vizcontrol.store import Writer

_PKG_DIR = os.path.dirname(os.path.abspath(__file__)) + os.sep
_active: Run | None = None


class _Terminated(BaseException):
    """Raised by our SIGTERM handler so the run is closed as 'unfinished' instead of being lost."""


def default_path(out_dir: str, target: str) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", os.path.basename(target.rstrip("/")) or "run").strip("-")[:60]
    d = Path(out_dir)
    if not d.exists():
        d.mkdir(parents=True)
        (d / ".gitignore").write_text("*\n")
    return d / f"{stamp}-{slug}.vizc"


class Run:
    def __init__(self, kind: str, target: str, argv: list[str], config: Config, output: str | os.PathLike | None = None,
                 roots: list[str] | None = None):
        self.config = config
        self.path = Path(output) if output else default_path(config.out_dir, target)
        self.encoder = Encoder(config)
        meta = {
            "kind": kind, "target": target, "argv": json.dumps([self.encoder.scrub(a)[0] for a in argv]),
            "cwd": os.getcwd(), "python": sys.version,
            "started_wall": datetime.datetime.now(datetime.UTC).isoformat(), "started_ns": time.perf_counter_ns(),
            "outcome": "running", "config": json.dumps(dataclasses.asdict(config)),
        }
        self.writer = Writer(self.path, meta)
        self.tracer = Tracer(config, self.writer, self.encoder, roots or [os.getcwd()])
        self.done = False

    def start(self) -> None:
        global _active
        if _active is not None:
            raise RuntimeError("a VizControl run is already active in this process")
        _active = self
        atexit.register(self._atexit)
        self.tracer.start()

    def finish(self, outcome: str, exit_code: int | None = None) -> None:
        global _active
        if self.done:
            return
        self.done = True
        self.tracer.stop()
        self.writer.close(outcome, exit_code, time.perf_counter_ns(), self.tracer.truncated)
        atexit.unregister(self._atexit)
        _active = None

    def _atexit(self) -> None:
        self.finish("unfinished")


def _outcome(exc: BaseException | None) -> str:
    if exc is None:
        return "ok"
    if isinstance(exc, (KeyboardInterrupt, _Terminated)):
        return "unfinished"
    if isinstance(exc, SystemExit):
        return "ok" if exc.code in (0, None) else "raised"
    return "raised"


class trace:
    """Trace a block: `with vizcontrol.trace("checkout") as t: ...; t.path`. Options override the config."""

    def __init__(self, name: str | None = None, *, output: str | os.PathLike | None = None,
                 config: Config | None = None, **options):
        self.name, self.output, self.config, self.options = name, output, config, options
        self.run: Run | None = None
        self.path: Path | None = None

    def __enter__(self) -> trace:
        if _active is not None:  # ponytail: nested trace() joins the active run; region markers come later
            self.path = _active.path
            return self
        config = self.config or load_config(**self.options)
        target = self.name or sys._getframe(1).f_code.co_qualname
        self.run = Run("api", target, sys.argv, config, self.output)
        self.path = self.run.path
        self.run.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.run is not None:
            self.run.finish(_outcome(exc))
        return False


def traced(fn=None, /, **options):
    """Decorator: every call of the function is its own run."""
    def decorate(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            with trace(f.__qualname__, **options):
                return f(*args, **kwargs)
        return wrapper
    return decorate(fn) if fn is not None else decorate


def _exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    print(exc.code, file=sys.stderr)
    return 1


def _print_user_traceback(exc: BaseException) -> None:
    tb = exc.__traceback__
    while tb is not None:
        name = tb.tb_frame.f_code.co_filename
        if not (name.startswith(_PKG_DIR) or "runpy" in name):
            break
        tb = tb.tb_next
    traceback.print_exception(type(exc), exc, tb)


def _execute(run: Run, target) -> int:
    def on_sigterm(signum, frame):
        raise _Terminated()

    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, on_sigterm)
    error: BaseException | None = None
    code = 0
    run.start()
    try:
        target()
    except SystemExit as e:
        error, code = e, _exit_code(e)
    except _Terminated as e:
        error, code = e, 143
    except KeyboardInterrupt as e:
        error, code = e, 130
    except BaseException as e:
        error, code = e, 1
    finally:
        run.finish(_outcome(error), code)
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if error is not None and not isinstance(error, (SystemExit, _Terminated)):
        _print_user_traceback(error)
    print(f"vizcontrol: trace written to {run.path}", file=sys.stderr)
    return code


def run_script(path: str, args: list[str], config: Config, output=None) -> int:
    script_dir = os.path.dirname(os.path.abspath(path))
    sys.argv = [path, *args]
    sys.path[0] = script_dir
    run = Run("script", path, sys.argv, config, output, roots=[os.getcwd(), script_dir])
    return _execute(run, lambda: runpy.run_path(path, run_name="__main__"))


def run_module(module: str, args: list[str], config: Config, output=None) -> int:
    sys.argv = [module, *args]
    sys.path[0] = os.getcwd()
    run = Run("module", module, sys.argv, config, output)
    return _execute(run, lambda: runpy.run_module(module, run_name="__main__", alter_sys=True))
