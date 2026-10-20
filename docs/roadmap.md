# Roadmap

v1 proves the core loop: trace a Python script, function or test, then explore it visually
([requirements](01-requirements.md)). The items below are evolutions, ordered roughly by value and dependency.
The trace format and APIs are designed so none of them needs a breaking change.

| # | Item | Builds on | Notes |
|---|---|---|---|
| 1 | pytest plugin, if not finished in v1 (FR-RUN-4) | runners | Done: `pytest --vizcontrol`. |
| 2 | Data Flow view and object evolution (FR-ANA-4/6, FR-VIZ-10) | analysis, viewer | Done: Data Flow view, nested objects included. Exact attribution still needs the `state` level. |
| 3 | Run comparison (FR-ANA-7, FR-VIZ-11) | analysis.compare | Diff view done. Pending: a `vizcontrol diff a.vizc b.vizc` command and differing return values. |
| — | Route map over many runs | analysis.route_map, viewer | Done: Map tab and `/api/map`, each run's route on the joined stage graph. |
| 4 | Chronological replay (FR-VIZ-12) | `seq`, timeline | Scrub events in order and watch values change. |
| 5 | Async capture (FR-CAP-12) | capture | Task creation/await causality via `asyncio` task hooks, so coroutines nest by logical caller. |
| 6 | FastAPI/ASGI instrumentation (FR-RUN-5) | 5, runners | Middleware, one run per request, sampling or header trigger. |
| 7 | Live view | storage, server | Server-sent events while a run is being written. |
| 8 | Serializers for popular libraries | serialization | Done: `__vizcontrol__`, `register` through the MRO, built-in views for pandas and numpy, pydantic/dataclasses as fields. Pending: SQLAlchemy models. |
| 9 | `settrace` backend for Python < 3.12 | capture | Emits the same records; slower, documented. |
| 10 | Search index | storage | SQLite FTS5 over names and string values. |
| 11 | Editor integration (VS Code) | server API | Open the viewer in a panel; "reveal call" ↔ "go to source". |
| 12 | Export and collaboration | trace format | Self-contained HTML export of a run; annotations stored beside the `.vizc`. |
| 13 | Multi-process and distributed | trace format | Follow subprocesses; merge runs by trace context (OpenTelemetry interop). |
| 14 | Other runtimes | trace format | Any producer of valid `.vizc` files gets the viewer for free. |
| 15 | Optional AI assistance | server API | Natural-language queries answered **only** from stored observations, with citations to calls/values (NFR-AI-1). |
