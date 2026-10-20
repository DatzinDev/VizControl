import asyncio

import vizcontrol
from vizcontrol.store import Reader


async def leaf(n):
    await asyncio.sleep(0)
    return n


async def fan():
    a, b = await asyncio.gather(leaf(1), leaf(2))
    await asyncio.to_thread(step, "pool")
    return a + b


def step(name):
    return name


def calls(path):
    r = Reader(path)
    rows = [r.call(c[0]) for c in r.all_calls()]
    return r, rows


def test_tasks_and_executor_jobs_hang_under_the_call_that_started_them(tmp_path):
    with vizcontrol.trace(output=tmp_path / "a.vizc") as t:
        assert asyncio.run(fan()) == 3
    r, rows = calls(t.path)
    by_name = {}
    for c in rows:
        by_name.setdefault(c["qualname"], []).append(c)
    [f] = by_name["fan"]
    assert [c["parent_id"] for c in by_name["leaf"]] == [f["id"], f["id"]]  # gather's tasks, not roots
    assert all(c["depth"] == f["depth"] + 1 for c in by_name["leaf"])
    [s] = by_name["step"]  # ran in a worker thread, which got a copy of the context
    assert s["parent_id"] == f["id"] and s["thread_id"] != f["thread_id"]
    assert r.exceptions() == []  # the StopIteration that hands an await its result is not a failure


async def work(name):
    for _ in range(3):
        await asyncio.sleep(0)
        step(name)


async def request(name, out):
    if out:
        with vizcontrol.trace(name, output=out, scoped=True, kind="request"):
            await work(name)
    else:
        await work(name)


def test_a_scoped_run_leaves_out_what_runs_beside_it(tmp_path):
    async def both():
        await asyncio.gather(request("a", tmp_path / "a.vizc"), request("b", None), request("c", None))

    asyncio.run(both())
    r, rows = calls(tmp_path / "a.vizc")
    assert sorted(c["qualname"] for c in rows) == ["sleep"] * 3 + ["step"] * 3 + ["work"]
    args = [b["data"] for c in rows for b in r.bindings(c["id"]) if b["kind"] == "arg"]
    assert all('"a"' in a for a in args)  # nothing from requests b and c
    assert r.run()["kind"] == "request"
