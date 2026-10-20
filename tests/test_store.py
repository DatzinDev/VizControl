import json

from vizcontrol.store import Reader, Writer

RUN = {"kind": "api", "target": "t", "argv": "[]", "cwd": "/", "python": "3.12", "started_wall": "now",
       "started_ns": 0, "outcome": "running", "config": json.dumps({"level": "values"})}


def write_tree(w: Writer):
    f = w.new_id("file")
    w.add("file", (f, "/x/app.py", "app.py", "abc", "print(1)"))
    c = w.new_id("code")
    w.add("code", (c, f, "app", "main", 1, "function", 1))
    root, child = w.new_id("call"), w.new_id("call")
    w.add("call", (root, None, 1, c, None, 0, 1, None, 10, None, "unfinished", None, 1, 0))  # written while open
    w.add("call", (child, root, 1, c, 3, 1, 2, None, 20, None, "unfinished", None, 1, 0))
    v = w.new_id("value")
    w.add("value", (v, None, "int", '{"t":"int","v":1}', 0, 0))
    w.add("binding", (child, 3, "return", None, 3, v))
    w.close_call(child, 4, 30, "ok")
    return root, child


def test_roundtrip(tmp_path):
    w = Writer(tmp_path / "r.vizc", RUN)
    root, child = write_tree(w)
    w.flush()  # the close update lands in a later batch than the insert
    w.close_call(root, 5, 40, "ok", children=1)
    w.close("ok", 0, 40, [])
    assert not (tmp_path / "r.vizc-wal").exists()

    r = Reader(tmp_path / "r.vizc")
    assert r.run()["outcome"] == "ok"
    assert r.run()["config"] == {"level": "values"}
    assert [c["id"] for c in r.children(None)] == [root]
    assert r.call(root)["children"] == 1
    kid = r.children(root)[0]
    assert (kid["outcome"], kid["end_ns"], kid["qualname"]) == ("ok", 30, "main")
    assert r.bindings(child)[0]["kind"] == "return"
    assert r.value(r.bindings(child)[0]["value_id"])["data"] == {"t": "int", "v": 1}
    assert r.source(1) == "print(1)"


def test_unclosed_file_keeps_flushed_rows(tmp_path):
    w = Writer(tmp_path / "crash.vizc", RUN)
    root, _ = write_tree(w)
    w.flush()  # simulate a kill after the last batch: no close()
    r = Reader(tmp_path / "crash.vizc")
    assert r.run()["outcome"] == "running"
    assert r.call(root)["outcome"] == "unfinished"
    assert r.counts()["call"] == 2
