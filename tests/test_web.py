import asyncio
import io

import pytest

from vizcontrol.server import Runs, _summary
from vizcontrol.store import Reader
from vizcontrol.web import ASGIMiddleware, WSGIMiddleware


def price(n):
    return n * 10


def names(path):
    r = Reader(path)
    return r, sorted(r.call(c[0])["qualname"] for c in r.all_calls())


async def raw_app(scope, receive, send):
    n = int(scope["query_string"].decode().split("n=")[1].split("&")[0])
    total = price(n)
    await asyncio.sleep(0)
    await send({"type": "http.response.start", "status": 200 if total else 404, "headers": []})
    await send({"type": "http.response.body", "body": str(total).encode()})


def test_asgi_traces_one_request_while_others_run_beside_it(tmp_path):
    app = ASGIMiddleware(raw_app, out_dir=str(tmp_path))

    async def call(n, traced):
        sent = []

        async def send(m):
            sent.append(m)

        headers = [(b"x-vizcontrol", b"1")] if traced else []
        await app({"type": "http", "method": "GET", "path": "/orders",
                   "query_string": f"n={n}&token=abc".encode(), "headers": headers}, None, send)
        return sent[0]["status"]

    async def many():
        return await asyncio.gather(call(0, True), call(2, False), call(3, False))

    assert asyncio.run(many()) == [404, 200, 200]
    [path] = tmp_path.glob("*.vizc")
    r, calls = names(path)
    run = r.run()
    assert run["kind"] == "request" and run["target"] == "GET /orders?n=0&token=‹redacted›"
    assert r.meta()["http_status"] == "404" and run["outcome"] == "ok"
    assert _summary(Runs(tmp_path), path.name)["http_status"] == "404"
    assert [c for c in calls if c in ("raw_app", "price")] == ["price", "raw_app"]  # requests 2 and 3 left out


def wsgi_app(environ, start_response):
    start_response("201 Created", [])
    return [str(price(2)).encode()]


def test_wsgi_every_request(tmp_path):
    app = WSGIMiddleware(wsgi_app, every=1, out_dir=str(tmp_path))
    environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/items", "QUERY_STRING": "", "wsgi.input": io.BytesIO()}
    assert app(environ, lambda status, headers, exc_info=None: None) == [b"20"]
    [path] = tmp_path.glob("*.vizc")
    r, calls = names(path)
    assert r.run()["target"] == "POST /items" and r.meta()["http_status"] == "201"
    assert "price" in calls and "wsgi_app" in calls


def test_fastapi(tmp_path):
    fastapi = pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    app = fastapi.FastAPI()

    @app.get("/orders/{oid}")
    async def get_order(oid: int):
        await asyncio.sleep(0)
        return {"id": oid, "total": price(oid)}

    @app.get("/boom")
    def boom():  # sync endpoints run in a worker thread
        raise ValueError(price(-1))

    app.add_middleware(ASGIMiddleware, every=1, out_dir=str(tmp_path))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                     base_url="http://t") as client:
            ok = await asyncio.gather(*(client.get(f"/orders/{i}") for i in (1, 2, 3)))
            bad = await client.get("/boom")
            return [r.status_code for r in ok], bad.status_code

    assert asyncio.run(run()) == ([200, 200, 200], 500)
    runs = {Reader(p).run()["target"]: p for p in tmp_path.glob("*.vizc")}
    assert "GET /boom" in runs and any(t.startswith("GET /orders/") for t in runs)
    orders = next(p for t, p in runs.items() if t.startswith("GET /orders/"))
    r, calls = names(orders)
    assert sum(c.endswith("get_order") for c in calls) == 1 and r.meta()["http_status"] == "200"  # one, not three
    r, calls = names(runs["GET /boom"])
    assert r.run()["outcome"] == "raised" and any(c.endswith("boom") for c in calls) and "price" in calls


def test_flask(tmp_path):
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)

    @app.route("/items/<int:n>")
    def item(n):
        return {"total": price(n)}

    app.wsgi_app = WSGIMiddleware(app.wsgi_app, out_dir=str(tmp_path))
    client = app.test_client()
    assert client.get("/items/4").json == {"total": 40}  # no header: not traced
    assert client.get("/items/5?api_key=s3cr3t", headers={"X-VizControl": "1"}).status_code == 200
    [path] = tmp_path.glob("*.vizc")
    r, calls = names(path)
    assert r.run()["target"] == "GET /items/5?api_key=‹redacted›" and r.meta()["http_status"] == "200"
    assert any(c.endswith("item") for c in calls) and "price" in calls
