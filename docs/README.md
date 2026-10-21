# VizControl documentation

VizControl is a Python package that records a program while it runs and plays the recording back in the browser,
step by step, like a debugger after the fact: who called whom, when, with what, what came back, where it failed.
It answers "what actually happened?" from runtime evidence, not from a model's guess about the code.

> We don't want an AI to tell us how our software works. We want to observe how it really works.

Using it: [Understanding a project you didn't write](guide.md), and the [ETL example](../examples/etl/README.md).

## Reading order

1. [Requirements](01-requirements.md): what v1 must do, numbered `FR-*` / `NFR-*`.
2. [Architecture](02-architecture.md): components, package layout, end-to-end flow, key decisions.
3. [Trace format](03-trace-format.md): the SQLite `.vizc` contract between capture and visualization.
4. Components:
   - [Capture](components/capture.md): `sys.monitoring` tracer.
   - [Serialization](components/serialization.md): safe value snapshots and redaction.
   - [Storage](components/storage.md): writing and reading `.vizc` files.
   - [Runners](components/runners.md): how an execution is started (script, module, `trace()`, pytest, ASGI/WSGI).
   - [Analysis](components/analysis.md): replay timeline, call graph, callbacks, watches, error path; stages and run
     diff for the CLI.
   - [Server](components/server.md): local JSON API over trace files.
   - [Viewer](components/viewer.md): the browser SPA, one view: Replay.
   - [CLI](components/cli.md): `vizcontrol` commands and configuration.
5. [UI design](ui-design.md): the Replay screen, its elements and tokens.
6. [Roadmap](roadmap.md): what each version brought and what comes next.

## Glossary

| Term | Meaning |
|---|---|
| **Run** | One instrumented execution (a script, a module, a function call, a test). Stored as one `.vizc` file. |
| **Event** | A raw runtime occurrence recorded by capture: call start, return, raise, unwind, line, snapshot. |
| **Call** (frame) | One invocation of a Python function or method, from start to return/unwind. A node in the call tree. |
| **Code location** | A `(file, qualified name, first line)` tuple identifying the code a call executed. Many calls share one. |
| **Value** | A serialized, bounded representation of a Python object at a moment in time. |
| **Object id** | A per-run stable identity for a live object, used to follow the same object across calls (data flow). |
| **Snapshot** | The set of values of a call's locals (or selected names) captured at a given event. |
| **Step** | One moment of a replay: a call starting, returning or raising. Imports and class bodies are not steps. |
| **Callback** | A call not made by the function's name: a function passed in, stored on an object, handed to a builtin, or called back by a library. Inferred. |
| **Stage** | A grouping of calls (by package, module or head function) for `vizcontrol diagram` and the API. Derived, never captured. |
| **Observed / not captured / inferred** | Provenance of every piece of information shown. See [NFR-OBS](01-requirements.md#non-functional-requirements). |
| **Outcome** | How a run or call ended: `ok`, `raised`, `unfinished`, plus `assertion_failed` for tests. |
