import vizcontrol
from vizcontrol import analysis as A
from vizcontrol.server import Runs, api
from vizcontrol.store import Reader


def double(x):
    return x * 2


def check(x):
    if x > 2:
        raise ValueError(f"too big: {x}")
    return double(x)


def work(items):
    out = []
    for x in items:
        try:
            out.append(check(x))
        except ValueError:
            out.append(None)
    return out


def test_a_run_replays_as_calls_and_returns_in_time_order(tmp_path):
    with vizcontrol.trace(output=tmp_path / "r.vizc"):
        work([1, 3])
    runs = Runs(tmp_path)
    page = api(runs, "/api/runs/r.vizc/timeline", {})
    steps = [(s["kind"], s["qualname"], s["depth"]) for s in page["steps"]]
    assert steps == [("call", "work", 0), ("call", "check", 1), ("call", "double", 2), ("return", "double", 2),
                     ("return", "check", 1), ("call", "check", 1), ("raise", "check", 1), ("return", "work", 0)]
    first = page["steps"][1]
    assert [(v["name"], v["preview"]) for v in first["values"]] == [("x", "1")]  # the arguments, at the call
    assert [(v["kind"], v["preview"]) for v in page["steps"][3]["values"]] == [("return", "2")]
    boom = page["steps"][6]
    assert boom["exception"] == {"type": "ValueError", "message": "too big: 3", "origin": True, "line": 13}
    assert ("x", "3") in [(v["name"], v["preview"]) for v in boom["values"]]  # the locals where it was raised
    assert page["raises"] == [6] and page["total"] == 8
    later = api(runs, "/api/runs/r.vizc/timeline", {"seq": [str(boom["seq"])], "limit": ["2"]})
    assert later["offset"] == 6 and [s["kind"] for s in later["steps"]] == ["raise", "return"]
    assert api(runs, "/api/runs/r.vizc/timeline", {"end_of": [str(boom["call_id"])]})["offset"] == 6


def test_call_graph_and_watch(tmp_path):
    with vizcontrol.trace(output=tmp_path / "g.vizc") as t:
        work([1, 2])
    r = Reader(t.path)
    g = A.call_graph(r)
    name = {n["code_id"]: n["qualname"] for n in g["nodes"]}
    assert {(name[e["source"]], name[e["target"]], e["calls"]) for e in g["edges"]} == {
        ("work", "check", 2), ("check", "double", 2)}
    assert [n["first_seq"] for n in g["nodes"]] == sorted(n["first_seq"] for n in g["nodes"])  # revealed in order
    xs = A.watch(r, "x")
    assert [(w["qualname"], w["preview"]) for w in xs] == [("check", "1"), ("double", "1"), ("check", "2"),
                                                           ("double", "2")]
    assert xs == sorted(xs, key=lambda w: w["seq"])


class Bus:
    def __init__(self):
        self.handlers = {}

    def on(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    def emit(self, name, data):
        for handler in self.handlers[name]:
            handler(data)


class Sink:
    def __init__(self):
        self.got = []

    def take(self, data):
        self.got.append(data)


def triple(x):
    return x * 3


def neg(x):
    return -x


def run_steps(steps, x):
    for step in steps:
        x = step(x)
    return x


def wiring():
    bus, sink = Bus(), Sink()
    bus.on("done", sink.take)
    bus.emit("done", run_steps([double, triple], 2))
    return sorted([3, 1, 2], key=neg)


def test_callbacks_say_how_they_were_called_and_where_the_function_came_from(tmp_path):
    with vizcontrol.trace(output=tmp_path / "cb.vizc"):
        wiring()
    r = Reader(tmp_path / "cb.vizc")
    calls = r.calls([c[0] for c in r.all_calls()])
    cb = A.callbacks(r, calls)
    by = {}
    for c in calls:
        by.setdefault(c["qualname"], cb.get(c["id"]))
    assert by["double"]["via"] == "steps[0]" and by["triple"]["via"] == "steps[1]"
    assert by["double"]["line"] == "x = step(x)"
    assert by["double"]["registered"]["qualname"] == "run_steps"  # the first value it was seen in
    assert by["Sink.take"]["via"] == "self.handlers['done'][0]"  # a bound method stored on the bus
    assert (by["Sink.take"]["registered"]["qualname"], by["Sink.take"]["registered"]["path"]) == ("Bus.on", "handler")
    assert by["neg"]["line"] == "return sorted([3, 1, 2], key=neg)" and "via" not in by["neg"]  # given to sorted()
    assert all(by[n] is None for n in ("wiring", "run_steps", "Bus.on", "Bus.emit", "Bus.__init__"))  # by name
    step = next(s for s in A.Timeline(r).page(r, 0, 100)["steps"] if s["qualname"] == "triple")
    assert step["callback"]["via"] == "steps[1]"
    g = A.call_graph(r)
    name = {n["code_id"]: n["qualname"] for n in g["nodes"]}
    edges = {(name[e["source"]], name[e["target"]]): e["callback"] for e in g["edges"]}
    assert edges[("run_steps", "double")]["via"] == "steps[0]" and edges[("wiring", "run_steps")] is None
