import json
import shutil
import threading
import urllib.error
import urllib.request

import pytest

from vizcontrol.server import make_server


@pytest.fixture(scope="module")
def base(runs_dir, tmp_path_factory):
    root = tmp_path_factory.mktemp("served")
    (root / "nested").mkdir()
    shutil.copy(runs_dir / "basic.vizc", root / "basic.vizc")
    shutil.copy(runs_dir / "vip.vizc", root / "nested" / "vip.vizc")
    (root / "secret.txt").write_text("not a run")
    server = make_server(root, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def api(base, path):
    status, ctype, body = get(base + path)
    assert ctype == "application/json", (path, status, body[:200])
    return status, json.loads(body)


def test_runs_and_run(base):
    status, runs = api(base, "/api/runs")
    assert status == 200 and [r["id"] for r in runs] == ["basic.vizc", "nested/vip.vizc"]
    assert {r["outcome"] for r in runs} == {"ok", "raised"}
    status, run = api(base, "/api/runs/nested%2Fvip.vizc")
    assert run["outcome"] == "raised" and run["counts"]["call"] > 10


def test_navigation_endpoints(base):
    _, stages = api(base, "/api/runs/basic.vizc/stages")
    assert stages["main_path"][0] == "__main__"
    _, roots = api(base, "/api/runs/basic.vizc/tree")
    module = roots[0]
    _, kids = api(base, f"/api/runs/basic.vizc/tree?node={module['id']}&depth=2")
    post = next(k for k in kids if k["qualname"] == "post_order")
    assert post["stage"] == "app.api" and "items" in post
    _, call = api(base, f"/api/runs/basic.vizc/calls/{post['id']}")
    assert call["caller"]["qualname"] == "<module>" and call["file"].endswith("api.py")
    _, binds = api(base, f"/api/runs/basic.vizc/calls/{post['id']}/bindings")
    payload = next(b for b in binds if b["name"] == "payload")
    assert "‹redacted›" in payload["preview"] and "s3cr3t" not in json.dumps(binds)
    _, value = api(base, f"/api/runs/basic.vizc/values/{payload['value_id']}")
    assert value["data"]["t"] == "dict"
    _, path = api(base, f"/api/runs/basic.vizc/calls/{post['id']}/path")
    assert [c["qualname"] for c in path] == ["<module>", "post_order"]
    _, src = api(base, f"/api/runs/basic.vizc/source/{call['file_id']}")
    assert "def post_order" in src["source"]
    _, hits = api(base, "/api/runs/basic.vizc/search?q=place_order")
    assert hits and hits[0]["kind"] == "function"


def test_error_and_objects(base):
    _, err = api(base, "/api/runs/nested%2Fvip.vizc/error")
    assert err["exception"]["type"] == "ValueError" and err["origin"]["qualname"] == "apply_discount"
    rule = next(b for b in err["locals"] if b["name"] == "rule")
    _, flow = api(base, f"/api/runs/nested%2Fvip.vizc/objects/{rule['object_id']}")
    assert flow["changes"][-1]["changes"][0]["after"] == "120"
    _, none = api(base, "/api/runs/basic.vizc/error")
    assert none is None


def test_only_registered_runs_are_readable(base):
    for path in ("/api/runs/secret.txt", "/api/runs/..%2Fsecret.txt", "/api/runs/basic.vizc/calls/999999",
                 "/api/runs/basic.vizc/nope"):
        status, body = api(base, path)
        assert status == 404 and body["error"] == "not_found", path


def test_viewer_or_placeholder(base):
    status, ctype, body = get(base + "/")
    assert status == 200 and ctype.startswith("text/html") and b"VizControl" in body
