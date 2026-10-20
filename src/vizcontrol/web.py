"""One run per HTTP request, for ASGI (FastAPI, Starlette) and WSGI (Flask, Django) apps.

    app.add_middleware(vizcontrol.web.ASGIMiddleware, every=1)     # FastAPI / Starlette
    app.wsgi_app = vizcontrol.web.WSGIMiddleware(app.wsgi_app)     # Flask

A request is traced when it carries the `X-VizControl` header, or one in `every` requests (every=1: all of them).
One traced request at a time: monitoring is process-wide, so the others run untraced, and the traced one is a
scoped run that leaves out what they do meanwhile. Runs are named after the request (`GET /orders?status=paid`,
secret-looking query values redacted) and keep the response status.
"""

from __future__ import annotations

import itertools
import threading
from urllib.parse import parse_qsl

from vizcontrol.config import load_config
from vizcontrol.runners import default_path, trace
from vizcontrol.serialize import Encoder


class _Gate:
    def __init__(self, every: int, options: dict):
        self.every = every
        self.config = load_config(**options)
        self.encoder = Encoder(self.config)
        self._count = itertools.count()
        self._lock = threading.Lock()

    def take(self, header: bool) -> bool:
        """Whether this request is traced; if so, release() when it ends."""
        n = next(self._count)
        wanted = header or (self.every > 0 and n % self.every == 0)
        return wanted and self._lock.acquire(blocking=False)

    def release(self) -> None:
        self._lock.release()

    def label(self, method: str, path: str, query: str) -> str:
        pairs = [(k, "‹redacted›" if self.encoder.redact_name(k) else v)
                 for k, v in parse_qsl(query, keep_blank_values=True)]
        query = "&".join(f"{k}={v}" for k, v in pairs)  # decoded: the run's name is for people to read
        return f"{method} {path}" + (f"?{self.encoder.scrub(query)[0]}" if pairs else "")

    def trace(self, label: str) -> trace:
        output = default_path(self.config.out_dir, label.split("?")[0].replace("/", " "))  # GET-orders-7
        return trace(label, scoped=True, kind="request", config=self.config, output=output)


class ASGIMiddleware:
    def __init__(self, app, *, every: int = 0, header: str = "x-vizcontrol", **options):
        self.app, self.header = app, header.lower().encode()
        self.gate = _Gate(every, options)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.gate.take(any(k == self.header for k, _ in scope["headers"])):
            return await self.app(scope, receive, send)
        status = []

        async def send_status(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])
            await send(message)

        try:
            label = self.gate.label(scope["method"], scope["path"], scope["query_string"].decode("latin-1"))
            with self.gate.trace(label) as t:
                try:
                    await self.app(scope, receive, send_status)
                finally:
                    if status and t.run is not None:
                        t.run.writer.set_meta("http_status", str(status[0]))
        finally:
            self.gate.release()


class WSGIMiddleware:
    """ponytail: a body the app streams after returning (a generator) is produced outside the run."""

    def __init__(self, app, *, every: int = 0, header: str = "x-vizcontrol", **options):
        self.app, self.environ_key = app, "HTTP_" + header.upper().replace("-", "_")
        self.gate = _Gate(every, options)

    def __call__(self, environ, start_response):
        if not self.gate.take(self.environ_key in environ):
            return self.app(environ, start_response)
        status = []

        def start_status(line, headers, exc_info=None):
            status.append(line.split(" ", 1)[0])
            return start_response(line, headers, exc_info)

        try:
            label = self.gate.label(environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/"),
                                    environ.get("QUERY_STRING", ""))
            with self.gate.trace(label) as t:
                try:
                    return self.app(environ, start_status)
                finally:
                    if status and t.run is not None:
                        t.run.writer.set_meta("http_status", status[0])
        finally:
            self.gate.release()
