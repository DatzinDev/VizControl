# Roadmap

v1 proved the core loop: trace a Python script, module, test or request, then explore what it did
([requirements](01-requirements.md)). Since 0.4 exploring means replaying the run step by step. The items below are
evolutions, ordered roughly by value and dependency. The trace format and APIs are designed so none of them needs
a breaking change.

## Released

| Version | What it brought |
|---|---|
| 0.1 | Capture (`sys.monitoring`), `.vizc` files, runners (script, module, `trace()`, pytest), the first viewer. |
| 0.2 | Readable stages for any project, Mermaid export, route map over many runs, serializers for libraries, failure in context, `vizcontrol diff`. |
| 0.3 | Async capture, scoped runs, ASGI/WSGI middleware; fixes from tracing real projects (black, rich, Flask, more-itertools), bounded memory and file size, `run --serve`. |
| 0.4 | The viewer becomes one view, Replay: call graph by module, transcript, timeline, watches, inferred callbacks, rearrangeable layout. The structure views left the viewer (their analysis stays in the CLI and the API). |

## Next

| # | Item | Builds on | Notes |
|---|---|---|---|
| 1 | Step over / step out | timeline | Jump to the next step at the same depth, or to the caller's next step. |
| 2 | Per-line steps (FR-CAP-5 Should) | capture `LINE` events | Opt-in per function: see a variable change inside a loop, not only at calls and returns. |
| 3 | Live view | storage, server | Replay a run while it is being written (server-sent events). |
| 4 | Serializers for more libraries | serialization | SQLAlchemy models, polars, pyarrow. |
| 5 | `settrace` backend for Python < 3.12 | capture | Emits the same records; slower, documented. |
| 6 | Search | storage | Find a function or a value and jump to its step (SQLite FTS5). |
| 7 | Editor integration (VS Code) | server API | Replay in a panel; "go to source" from a step. |
| 8 | Export and collaboration | trace format | A self-contained HTML replay of a run; annotations beside the `.vizc`. |
| 9 | Multi-process and distributed | trace format | Follow subprocesses; merge runs by trace context (OpenTelemetry interop). |
| 10 | Other runtimes | trace format | Any producer of valid `.vizc` files gets the viewer for free. |
| 11 | Optional AI assistance | server API | Questions answered **only** from stored observations, citing steps and values (NFR-AI-1). |

## Done along the way

pytest plugin, run comparison (`vizcontrol diff`), async capture, ASGI/WSGI instrumentation, serializers for
pandas/numpy/pydantic/dataclasses, chronological replay (FR-VIZ-12).
