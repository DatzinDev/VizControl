"""pytest plugin: `pytest --vizcontrol` traces each test into its own .vizc file (FR-RUN-4).

Inactive unless the flag is given. By default only the test body is traced; `--vizcontrol-fixtures` adds setup
and teardown. A failed `assert` makes the run `assertion_failed` with the assertion as its failure point, which
covers logical errors that raise nothing on their own.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vizcontrol.config import LEVELS, load_config
from vizcontrol.runners import Run, ensure_dir, slug, stamp


def pytest_addoption(parser):
    group = parser.getgroup("vizcontrol", "trace tests with VizControl")
    group.addoption("--vizcontrol", action="store_true", help="trace each test into a .vizc file")
    group.addoption("--vizcontrol-level", choices=LEVELS, help="capture level (default: from config)")
    group.addoption("--vizcontrol-fixtures", action="store_true", help="also trace fixture setup and teardown")
    group.addoption("--vizcontrol-dir", help="directory for this session's runs (default: <out_dir>/pytest-<time>)")


def pytest_configure(config):
    if config.getoption("vizcontrol"):
        config.pluginmanager.register(VizControlPlugin(config), "vizcontrol-tracer")


def _outcome(excinfo) -> str:
    if excinfo is None:
        return "ok"
    exc = excinfo[1] if isinstance(excinfo, tuple) else excinfo.value
    if isinstance(exc, pytest.skip.Exception):
        return "skipped"
    if isinstance(exc, AssertionError):
        return "assertion_failed"
    if isinstance(exc, KeyboardInterrupt):
        return "unfinished"
    return "raised"


class VizControlPlugin:
    def __init__(self, config):
        root = Path(config.rootpath)
        self.config = load_config(cwd=root, level=config.getoption("vizcontrol_level"))
        chosen = config.getoption("vizcontrol_dir")
        out = Path(self.config.out_dir)
        self.dir = Path(chosen) if chosen else (out if out.is_absolute() else root / out) / f"pytest-{stamp()}"
        self.root = str(root)
        self.fixtures = config.getoption("vizcontrol_fixtures")
        self.count = 0
        self.failed = 0

    def _run(self, item) -> Run:
        self.count += 1
        path = ensure_dir(self.dir) / f"{self.count:04d}-{slug(item.nodeid, 120)}.vizc"
        return Run("pytest", item.nodeid, sys.argv, self.config, path, roots=[self.root])

    def _finish(self, run: Run, outcome: str) -> None:
        self.failed += outcome in ("assertion_failed", "raised")
        run.finish(outcome)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        if self.fixtures:
            yield
            return
        run = self._run(item)
        run.start()
        result = yield
        self._finish(run, _outcome(result.excinfo))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        if not self.fixtures:
            yield
            return
        item.stash[_EXCINFO] = []
        run = self._run(item)
        run.start()
        yield
        outcomes = [_outcome(e) for e in item.stash[_EXCINFO]] or ["ok"]
        worst = next((o for o in ("raised", "assertion_failed", "unfinished", "skipped") if o in outcomes), "ok")
        self._finish(run, worst)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        yield
        if self.fixtures and call.excinfo is not None and _EXCINFO in item.stash:
            item.stash[_EXCINFO].append(call.excinfo)

    def pytest_terminal_summary(self, terminalreporter):
        if not self.count:
            return
        terminalreporter.write_sep("-", "vizcontrol")
        terminalreporter.write_line(f"{self.count} traced tests ({self.failed} failed) in {self.dir}")
        terminalreporter.write_line(f"explore them with: vizcontrol serve {self.dir}")


_EXCINFO = pytest.StashKey[list]()
