"""`vizcontrol serve`: the viewer's static files plus a read-only JSON API over .vizc files.

Local and single-user: binds 127.0.0.1 by default and only ever opens files from its run registry.
See docs/components/server.md.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import re
import sys
import threading
import webbrowser
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from vizcontrol import analysis as A
from vizcontrol import diagram as D
from vizcontrol.serialize import preview
from vizcontrol.store import Reader


class NotFound(Exception):
    pass


class Runs:
    """The .vizc files this server may read: one file, or every *.vizc under a directory."""

    def __init__(self, path: str | Path):
        self.root = Path(path).resolve()
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        self._readers: dict[str, tuple[int, Reader]] = {}
        self._cache: dict[tuple, object] = {}
        self.lock = threading.Lock()  # ponytail: one request at a time touches SQLite; fine for a local single user

    def registry(self) -> dict[str, Path]:
        """Run id -> file, discovered on disk. Paths only ever come from here, never from request input."""
        if self.root.is_file():
            return {self.root.name: self.root}
        return {str(p.relative_to(self.root)): p for p in sorted(self.root.rglob("*.vizc")) if p.is_file()}

    def ids(self) -> list[str]:
        return list(self.registry())

    def path(self, run_id: str) -> Path:
        path = self.registry().get(run_id)
        if path is None:
            raise NotFound(f"no run {run_id!r}")
        return path

    def reader(self, run_id: str) -> Reader:
        path = self.path(run_id)
        mtime = path.stat().st_mtime_ns
        hit = self._readers.get(run_id)
        if hit is None or hit[0] != mtime:  # reopen when the file changed (a run still being written)
            if hit is not None:
                hit[1].close()
            self._readers[run_id] = hit = (mtime, Reader(path))
            self._cache = {k: v for k, v in self._cache.items() if k[0] != run_id}
        return hit[1]

    def heads(self) -> frozenset:
        """The head functions of the served project (`analysis.Stager`), decided over all runs so that a stage has
        the same name in every run: none when the runs together have enough module stages, else every head any
        run found on its own."""
        ids = self.readable()
        if len(set().union(*(self.cached(i, A.module_stages) for i in ids))) >= A.MIN_STAGES:
            return frozenset()
        return frozenset().union(*(self.cached(i, A.Stager).names for i in ids))

    def readable(self) -> list[str]:
        """The runs that open; /api/runs lists the others with their error."""
        out = []
        for run_id in self.ids():
            with contextlib.suppress(Exception):  # one broken file must not hide the others
                self.reader(run_id).run()
                out.append(run_id)
        return out

    def cached(self, run_id: str, fn, *args):
        reader = self.reader(run_id)
        key = (run_id, fn, *args)
        if key not in self._cache:
            self._cache[key] = fn(reader, *args)
        return self._cache[key]


# -- endpoints ----------------------------------------------------------------------------------------------------

def _summary(runs: Runs, run_id: str) -> dict:
    try:
        r = runs.reader(run_id)
        run = r.run()
    except Exception as e:  # one broken file must not hide the others
        return {"id": run_id, "error": str(e)}
    args = run["argv"][1:] if run["kind"] in ("script", "module") else []  # pytest's argv is noise next to a node id
    return {"id": run_id, "kind": run["kind"], "target": run["target"], "args": args, "outcome": run["outcome"],
            "exit_code": run["exit_code"], "started": run["started_wall"],
            "duration_ns": run["ended_ns"] - run["started_ns"] if run["ended_ns"] else None,
            "calls": r.counts()["call"], "truncated": run["truncated"], "level": run["config"].get("level"),
            "process_gone": run["process_gone"], "regions": r.regions(),
            # what the viewer needs to mark cut nodes: calls at max_depth lost their children if limit_depth was hit,
            # and the call where max_calls stopped tracing
            "max_depth": run["config"].get("limits", {}).get("max_depth"),
            "cut_calls": [g["call_id"] for g in r.gaps() if g["kind"] == "limit_calls" and g["call_id"] is not None]}


def _int(q: dict, name: str, default: int | None, lo: int = 0, hi: int = 10_000) -> int | None:
    raw = q.get(name, [None])[0]
    if raw in (None, ""):
        return default
    return max(lo, min(hi, int(raw)))


def _bindings(r: Reader, call_id: int) -> list[dict]:
    return [{"name": b["name"], "kind": b["kind"], "line": b["line"], "seq": b["seq"], "value_id": b["value_id"],
             "object_id": b["object_id"], "type": b["type"], "preview": preview(json.loads(b["data"]))}
            for b in r.bindings(call_id)]


def _call(r: Reader, call_id: int, stager: A.Stager) -> dict:
    c = r.call(call_id)
    if c is None:
        raise NotFound(f"no call {call_id}")
    node = A.call_node(c, stager.of(r, c))
    node["file_id"] = c["file_id"]
    node["caller"] = None
    if c["parent_id"] is not None:
        p = r.call(c["parent_id"])
        node["caller"] = {"id": p["id"], "qualname": p["qualname"], "file": p["relpath"] or p["path"],
                          "line": c["call_line"]}
    if c["exception_id"] is not None:
        node["exception"] = r.exception(c["exception_id"])
    node["started_by"] = None
    if c["parent_id"] is None:  # a thread's root call: which Thread.start launched it (inferred)
        start_id = A.thread_starts(r).get(c["id"])
        if start_id is not None:
            s = r.call(start_id)
            spawner = s["parent_id"] and r.call(s["parent_id"])
            node["started_by"] = {"id": start_id, "caller_id": spawner and spawner["id"],
                                  "caller": spawner and spawner["qualname"], "inferred": True}
    return node


def api(runs: Runs, path: str, q: dict):
    """Dispatch one API request. Returns JSON-able data or raises NotFound."""
    if path == "/api/runs":
        return [_summary(runs, i) for i in runs.ids()]
    if path == "/api/map":
        heads, ids = runs.heads(), runs.readable()
        return A.route_map({i: runs.cached(i, A.stages, heads) for i in ids},
                           {i: runs.reader(i).run()["outcome"] for i in ids})
    if path == "/api/compare":  # both ids must be registered runs: reader() looks them up, never builds a path
        a, b = q.get("a", [""])[0], q.get("b", [""])[0]
        return A.compare(runs.reader(a), runs.reader(b))
    m = re.fullmatch(r"/api/runs/([^/]+)(/.*)?", path)
    if not m:
        raise NotFound(path)
    run_id, rest = unquote(m.group(1)), m.group(2) or ""
    r = runs.reader(run_id)
    heads = runs.heads()
    if rest == "":
        return {**_summary(runs, run_id), "run": r.run(), "counts": r.counts()}
    if rest == "/stages":
        return runs.cached(run_id, A.stages, heads)
    if rest in ("/stage", "/mermaid"):
        name = q.get("name", [None])[0]
        if rest == "/mermaid" and name is None:  # `vizcontrol diagram`, for the viewer's copy button
            return {"mermaid": D.from_stages(runs.cached(run_id, A.stages, heads))}
        if name not in {n["name"] for n in runs.cached(run_id, A.stages, heads)["nodes"]}:  # only real stages cached
            raise NotFound(f"no stage {name!r}")
        if rest == "/mermaid":
            return {"mermaid": D.from_detail(runs.cached(run_id, A.stage_detail, name, heads))}
        return runs.cached(run_id, A.stage_detail, name, heads)
    if rest == "/tree":
        node, code = _int(q, "node", None, 1, 1 << 62), _int(q, "code", None, 1, 1 << 62)
        offset, limit = _int(q, "offset", 0, 0, 1 << 62), _int(q, "limit", 200, 1, 1000)
        if code is not None:
            return A.group_members(r, node, code, offset, limit)
        stager = runs.cached(run_id, A.Stager, heads)
        return A.subtree(r, node, _int(q, "depth", 1, 1, 4), limit, stager, stager.head_of(r, node))
    if rest == "/error":
        return runs.cached(run_id, A.error_path, heads)
    if rest == "/search":
        return A.search(r, q.get("q", [""])[0])
    if rest == "/objects":
        return r.objects(_int(q, "limit", 50, 1, 500))
    m = re.fullmatch(r"/(calls|values|objects|source)/(\d+)(/bindings|/path)?", rest)
    if not m:
        raise NotFound(path)
    kind, ident, sub = m.group(1), int(m.group(2)), m.group(3)
    if kind == "calls" and sub is None:
        return _call(r, ident, runs.cached(run_id, A.Stager, heads))
    if kind == "calls" and sub == "/bindings":
        return _bindings(r, ident)
    if kind == "calls" and sub == "/path":
        path = r.ancestors(ident)
        return [A.call_node(c, s) for c, s in zip(path, runs.cached(run_id, A.Stager, heads).path(path), strict=True)]
    if sub is None and kind == "values":
        v = r.value(ident)
        if v is None:
            raise NotFound(f"no value {ident}")
        return v
    if sub is None and kind == "objects":
        return A.object_flow(r, ident)
    if sub is None and kind == "source":
        f = r.file(ident)
        if f is None:
            raise NotFound(f"no file {ident}")
        return {"file_id": f["id"], "path": f["path"], "relpath": f["relpath"], "sha256": f["sha256"],
                "source": f["source"]}
    raise NotFound(path)


# -- HTTP ---------------------------------------------------------------------------------------------------------

def _static_root():
    return resources.files("vizcontrol").joinpath("viewer", "static")


def _placeholder(runs: Runs) -> bytes:
    """Shown until the viewer is built: the runs and their API links."""
    rows = "".join(
        f'<li><a href="/api/runs/{quote(i, safe="")}">{escape(i)}</a> · '
        f'<a href="/api/runs/{quote(i, safe="")}/stages">stages</a> · '
        f'<a href="/api/runs/{quote(i, safe="")}/error">error</a></li>' for i in runs.ids())
    return (f"<!doctype html><meta charset=utf-8><title>VizControl</title>"
            f"<body style='font-family:system-ui;background:#0E1116;color:#E6EDF3'><h1>VizControl</h1>"
            f"<p>The viewer is not built yet. API:</p><ul>{rows}</ul>").encode()


LOOPBACK = ("127.0.0.1", "localhost", "::1")

# The viewer is static files plus same-origin fetches: nothing else may load or embed it.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _host_name(header: str | None) -> str:
    """The host part of a Host header: "localhost:7171" -> "localhost", "[::1]:7171" -> "::1"."""
    header = (header or "").strip().lower()
    if header.startswith("["):
        return header[1:header.find("]")] if "]" in header else ""
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


class Handler(BaseHTTPRequestHandler):
    server_version = "VizControl"
    sys_version = ""

    def log_message(self, format, *args):  # quiet: the terminal belongs to the user
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, default=str).encode(), "application/json")

    def do_GET(self):
        # DNS rebinding: a web page can point its own domain at 127.0.0.1 and read the traces through the
        # victim's browser. A loopback server only answers requests addressed to a loopback name.
        if self.server.allowed_hosts and _host_name(self.headers.get("Host")) not in self.server.allowed_hosts:
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden_host", "message": "unexpected Host header"})
            return
        url = urlsplit(self.path)
        runs: Runs = self.server.runs
        if url.path.startswith("/api/"):
            try:
                with runs.lock:
                    data = api(runs, url.path, parse_qs(url.query))
                self._json(HTTPStatus.OK, data)
            except NotFound as e:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found", "message": str(e)})
            except ValueError as e:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "bad_request", "message": str(e)})
            except Exception as e:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(e).__name__, "message": str(e)})
            return
        self._static(url.path)

    def _static(self, path: str) -> None:
        root = _static_root()
        if not root.joinpath("index.html").is_file():
            self._send(HTTPStatus.OK, _placeholder(self.server.runs), "text/html; charset=utf-8")
            return
        parts = [p for p in unquote(path).split("/") if p]
        if any(p in ("..", ".") or "\\" in p for p in parts):
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return
        target = root.joinpath(*parts) if parts else root.joinpath("index.html")
        if not target.is_file():  # SPA: client-side routes get the app
            target = root.joinpath("index.html")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)


def make_server(path: str | Path, host: str = "127.0.0.1", port: int = 7171) -> ThreadingHTTPServer:
    runs = Runs(path)
    for candidate in [port] if port == 0 else range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, candidate), Handler)
            break
        except OSError:
            continue
    else:
        raise OSError(f"no free port in {port}..{port + 19}")
    server.runs = runs
    server.daemon_threads = True
    # ponytail: a server bound to a non-loopback address was exposed on purpose (with a warning); no Host check
    server.allowed_hosts = LOOPBACK if host in LOOPBACK else ()
    return server


def serve(path: str | Path, host: str = "127.0.0.1", port: int = 7171, open_browser: bool = True) -> int:
    server = make_server(path, host, port)
    url = f"http://{'localhost' if host in ('127.0.0.1', 'localhost') else host}:{server.server_address[1]}/"
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"vizcontrol: warning: serving on {host}; traces may contain sensitive data", file=sys.stderr)
    print(f"vizcontrol: serving {server.runs.root} at {url}  (Ctrl+C to stop)", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
