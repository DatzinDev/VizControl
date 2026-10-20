# VizControl documentation

VizControl is a Python package that observes a program while it runs and turns what it saw into an
interactive, hierarchical graph. It answers "what actually happened?" from runtime evidence, not from a
model's guess about the code.

> We don't want an AI to tell us how our software works. We want to observe how it really works.

Using it: [Understanding a project you didn't write](guide.md).

## Reading order

1. [Requirements](01-requirements.md): what v1 must do, numbered `FR-*` / `NFR-*`.
2. [Architecture](02-architecture.md): components, package layout, end-to-end flow, key decisions.
3. [Trace format](03-trace-format.md): the SQLite `.vizc` contract between capture and visualization.
4. Components:
   - [Capture](components/capture.md): `sys.monitoring` tracer.
   - [Serialization](components/serialization.md): safe value snapshots and redaction.
   - [Storage](components/storage.md): writing and reading `.vizc` files.
   - [Runners](components/runners.md): how an execution is started (script, module, API, pytest).
   - [Analysis](components/analysis.md): call tree, stages, data flow, error path, run diff.
   - [Server](components/server.md): local JSON API over trace files.
   - [Viewer](components/viewer.md): the browser SPA.
   - [CLI](components/cli.md): `vizcontrol` commands and configuration.
5. [UI design](ui-design.md): the Penpot concept for the viewer.
6. [Roadmap](roadmap.md): what comes after v1.

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
| **Stage** | A Level‑1 grouping of calls (e.g. by package or layer) used for the overview. Derived, never captured. |
| **Observed / not captured / inferred** | Provenance of every piece of information shown. See [NFR-OBS](01-requirements.md#non-functional-requirements). |
| **Outcome** | How a run or call ended: `ok`, `raised`, `unfinished`, plus `assertion_failed` for tests. |
