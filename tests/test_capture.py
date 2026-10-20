"""End-to-end capture tests: trace real code, then assert on the .vizc file."""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import vizcontrol
from vizcontrol.store import Reader

SHOP = Path(__file__).parent / "samples" / "shop"


def tree(r: Reader, call_id=None) -> list[dict]:
    """All calls in pre-order, each with its depth."""
    out = []
    for c in r.children(call_id, limit=10_000):
        out.append(c)
        out.extend(tree(r, c["id"]))
    return out


def by_name(r: Reader, qualname: str) -> list[dict]:
    return [c for c in tree(r) if c["qualname"] == qualname]


def bindings(r: Reader, call_id: int, kind: str) -> dict:
    return {b["name"]: json.loads(b["data"]) for b in r.bindings(call_id) if b["kind"] == kind}


def run_cli(tmp_path, *args):
    out = tmp_path / "run.vizc"
    proc = subprocess.run([sys.executable, "-m", "vizcontrol", "run", "-o", str(out), *args],
                          cwd=SHOP, capture_output=True, text=True, timeout=60)
    return proc, Reader(out)


# -- script runs through the CLI ------------------------------------------------------------------------------

def test_script_ok(tmp_path):
    proc, r = run_cli(tmp_path, "checkout.py", "basic")
    assert proc.returncode == 0, proc.stderr
    assert "'status': 'paid'" in proc.stdout  # the program's own output is untouched
    run = r.run()
    assert (run["kind"], run["outcome"], run["exit_code"], run["truncated"]) == ("script", "ok", 0, [])
    assert run["argv"] == ["checkout.py", "basic"]

    [root] = r.children(None)
    assert root["qualname"] == "<module>"
    place = by_name(r, "OrderService.place_order")[0]
    assert r.call(place["parent_id"])["qualname"] == "post_order"
    assert [c["qualname"] for c in r.children(place["id"])] == ["OrderService._build_order", "OrderRepo.add"]

    # values: args, return, redaction, identity of the order object across calls
    payload = bindings(r, by_name(r, "post_order")[0]["id"], "arg")["payload"]
    keys = {k["v"]: v for k, v in payload["items"]}
    assert keys["token"] == {"t": "redacted"} and keys["tier"] == {"t": "str", "v": "basic"}
    returned = bindings(r, place["id"], "return")[None]
    assert returned["attrs"]["status"] == {"t": "str", "v": "paid"}
    added = bindings(r, by_name(r, "OrderRepo.add")[0]["id"], "arg")["order"]
    assert added["oid"] == returned["oid"]

    # opaque calls: C constructor (exact end) and excluded Python function (approximate end)
    dumps = by_name(r, "dumps")[0]
    assert dumps["observed"] == 0 and dumps["end_exact"] == 0 and dumps["outcome"] == "ok"
    assert all(c["observed"] for c in tree(r) if c["qualname"].startswith("Order"))
    # nothing from the stdlib, site-packages or vizcontrol itself is traced
    paths = [f["path"] for f in r._all("SELECT path FROM file")]
    assert paths and all(str(SHOP) in p for p in paths)


def test_script_failure(tmp_path):
    proc, r = run_cli(tmp_path, "checkout.py", "vip")
    assert proc.returncode == 1
    assert "ValueError: discount 6.30 exceeds total 5.25" in proc.stderr
    assert "vizcontrol" not in proc.stderr.split("Traceback")[1].split("ValueError")[0]
    assert r.run()["outcome"] == "raised"

    [exc] = r.exceptions()
    origin = r.call(exc["raised_call_id"])
    assert (exc["type"], origin["qualname"], exc["handled_call_id"]) == ("ValueError", "apply_discount", None)
    assert [f[2] for f in json.loads(exc["traceback"])][0] == "<module>"
    assert bindings(r, origin["id"], "raise")["rule"]["attrs"]["pct"] == {"t": "int", "v": 120}

    # every call on the error path is 'raised' and linked to the exception; siblings are not
    raised = [c["qualname"] for c in tree(r) if c["outcome"] == "raised"]
    assert raised == ["<module>", "post_order", "OrderService.place_order", "OrderService._build_order",
                      "compute", "apply_discount"]
    assert all(c["exception_id"] == exc["id"] for c in tree(r) if c["outcome"] == "raised")
    assert by_name(r, "load_vip_rules")[0]["outcome"] == "ok"


def test_module_run_and_exit_code(tmp_path):
    (tmp_path / "exiter.py").write_text("import sys\n\ndef main():\n    sys.exit(3)\n\nmain()\n")
    out = tmp_path / "m.vizc"
    proc = subprocess.run([sys.executable, "-m", "vizcontrol", "run", "-o", str(out), "-m", "exiter"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 3, proc.stderr
    r = Reader(out)
    assert (r.run()["kind"], r.run()["outcome"], r.run()["exit_code"]) == ("module", "raised", 3)
    assert by_name(r, "main")[0]["outcome"] == "raised"


# -- in-process API --------------------------------------------------------------------------------------------

class Box:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def lookup(d, key):
    try:
        return d[key]
    except KeyError:
        return None


def fail_in_with():
    with Box():
        raise RuntimeError("inside with")


def recurse(n):
    return n if n == 0 else recurse(n - 1)


def test_trace_block_exceptions_and_limits(tmp_path):
    with vizcontrol.trace("block", output=tmp_path / "t.vizc", limits={"max_depth": 10}) as t:
        lookup({}, "missing")
        try:
            fail_in_with()
        except RuntimeError:
            pass
        recurse(30)
    r = Reader(t.path)
    assert r.run()["kind"] == "api" and r.run()["outcome"] == "ok"

    excs = {e["type"]: e for e in r.exceptions()}
    assert set(excs) == {"KeyError", "RuntimeError"}  # with-block re-raise is not a second exception
    assert r.call(excs["KeyError"]["handled_call_id"])["qualname"] == "lookup"
    assert excs["RuntimeError"]["handled_call_id"] is None  # caught by the untraced block frame
    assert by_name(r, "fail_in_with")[0]["outcome"] == "raised"

    assert max(c["depth"] for c in by_name(r, "recurse")) == 10
    assert r.run()["truncated"] == ["limit_depth"]
    assert [g["kind"] for g in r.gaps()] == ["limit_depth"]


def test_threads_and_traced_decorator(tmp_path):
    @vizcontrol.traced(output=tmp_path / "d.vizc")
    def job():
        t = threading.Thread(target=recurse, args=(2,))
        t.start()
        t.join()
        return "done"

    assert job() == "done"
    r = Reader(tmp_path / "d.vizc")
    roots = r.children(None)
    assert {c["qualname"] for c in roots} >= {"test_threads_and_traced_decorator.<locals>.job", "recurse"}
    assert len({c["thread_id"] for c in tree(r)}) == 2


def test_nested_trace_joins_active_run(tmp_path):
    with vizcontrol.trace(output=tmp_path / "outer.vizc") as outer:
        with vizcontrol.trace(output=tmp_path / "inner.vizc") as inner:
            recurse(1)
    assert inner.path == outer.path and not (tmp_path / "inner.vizc").exists()


def test_tracer_is_released(tmp_path):
    with vizcontrol.trace(output=tmp_path / "a.vizc"):
        pass
    assert sys.monitoring.get_tool(sys.monitoring.PROFILER_ID) is None


@pytest.mark.parametrize("level", ["calls", "state"])
def test_levels(tmp_path, level):
    with vizcontrol.trace(output=tmp_path / "l.vizc", level=level) as t:
        recurse(1)
    r = Reader(t.path)
    calls = by_name(r, "recurse")
    if level == "calls":
        assert r.counts()["binding"] == 0
    else:
        assert bindings(r, calls[0]["id"], "local") == {"n": {"t": "int", "v": 1}}
