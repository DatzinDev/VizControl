# Server

`src/vizcontrol/server.py`

## Responsibility

Serve the prebuilt [viewer](viewer.md) and a read-only JSON API over one or more `.vizc` files. It is the only
way the viewer gets data, so it is also where older file formats are adapted.

## Requirements covered

NFR-PERF-3, NFR-SAFE-3, NFR-DEC-1, NFR-PKG-2. Exposes FR-ANA-* to the viewer.

## Design

- `http.server.ThreadingHTTPServer` with one request handler class. No framework (NFR-PKG-1).
- Binds `127.0.0.1:<port>` (default 7171, or the next free port). `--host` lets it bind elsewhere, with a warning
  that traces may contain sensitive data.
- Serves `vizcontrol/viewer/static/` for every non-`/api` path (SPA fallback to `index.html`). Uses
  `importlib.resources`, so it works from a zip or wheel.
- The **run registry** is built at start. Given a file, it holds that file. Given a directory, it holds every
  `*.vizc` under it, rescanned on `GET /api/runs`. Run ids are paths relative to the served root. Requests for
  anything outside the registry get 404, which also prevents path traversal.
- One `Reader` per run, opened lazily, reopened (and its analysis cache dropped) when the file's mtime changes.
  A single lock serializes API requests, which is enough for one local user.
- Run ids in URLs are percent-encoded (`nested%2Fvip.vizc`).
- Until the viewer is built into `viewer/static/`, `/` serves a plain page listing the runs and their API links.
- Errors return JSON `{"error": code, "message": ...}` with a proper status.

## API

All responses are JSON. Lists are paged with `offset`/`limit`. Fields derived by analysis carry
`"inferred": true`.

### Used by the viewer (Replay)

| Method & path | Returns | Backed by |
|---|---|---|
| `GET /api/runs` | Run summaries: id, target, kind, outcome, started, duration, calls, truncated, `http_status` for request runs, `max_depth` and `cut_calls` (where `max_calls` stopped tracing). | `Reader.run` |
| `GET /api/runs/{run}/calls/{id}/path` | Ancestors to root: the call stack of a step. | `Reader` |
| `GET /api/runs/{run}/values/{id}` | Full value tree, opened from a step. | `Reader.value` |
| `GET /api/runs/{run}/objects/{oid}` | Object appearances (nested ones included), flow links, changes: the history in the value panel. | `analysis.object_flow` |
| `GET /api/runs/{run}/error` | Error path, or `null` (also for an `ok` run that let `SystemExit(0)` escape): where a failed run opens. | `analysis.error_path` |
| `GET /api/runs/{run}/timeline?offset=&limit=` (or `seq=`, `end_of=CALL`) | Replay steps in time order: every call's start, return or raise, with the values bound at that step (arguments, result, locals where it raised) and, for a call made through a variable, its inferred `callback`. Imports and class bodies are left out (`setup_calls`). `total` and the steps that raised come with each page; `seq` and `end_of` ask for the page at a point in time or where a call ended. | `analysis.Timeline` |
| `GET /api/runs/{run}/callgraph` | Functions as nodes and "A called B" as edges, with call counts, the `seq` each first happened and `callback` on edges made through a variable; setup left out as in the timeline. | `analysis.call_graph` |
| `GET /api/runs/{run}/watch?name=` | Every value a variable of that name had, anywhere in the run, in time order. | `analysis.watch` |

### For the CLI, scripts and other clients

The structure endpoints the viewer used before 0.4. They stay, tested, for `vizcontrol diagram`/`diff` and anything else that reads runs.

| Method & path | Returns | Backed by |
|---|---|---|
| `GET /api/runs/{run}` | Full run metadata and config. | `Reader.run` |
| `GET /api/runs/{run}/stages` | Stage graph + main path. | `analysis.stages` |
| `GET /api/runs/{run}/mermaid[?name=]` | The stage graph, or stage `name` opened, as Mermaid text (`{"mermaid": ...}`). Same 404 rule as `/stage`. | `diagram.stages`, `diagram.stage` |
| `GET /api/runs/{run}/stage?name=` | One stage opened into module › class › function. 404 unless `name` is a stage of the run, so only real stages are cached. | `analysis.stage_detail` |
| `GET /api/runs/{run}/tree?node=&depth=&limit=` | Tree slice with aggregated groups (Levels 2–3). Roots when `node` is omitted. `depth` ≤ 4. | `analysis.subtree` |
| `GET /api/runs/{run}/tree?node=&code=&offset=&limit=` | Members of one `×N` group, paged. | `analysis.group_members` |
| `GET /api/runs/{run}/calls/{id}` | Call detail: code, file, line, caller, timing, outcome, exception, child count. | `Reader.call` |
| `GET /api/runs/{run}/calls/{id}/bindings` | Args, return, snapshots (value ids + previews). | `Reader.bindings` |
| `GET /api/runs/{run}/objects?limit=` | The run's most seen objects (top-level bindings), named after their first binding that isn't `self`. `limit` ≤ 500. | `Reader.objects` |
| `GET /api/runs/{run}/source/{file_id}` | `{path, relpath, sha256, source}`; `source` is null if not stored. | `Reader.file` |
| `GET /api/runs/{run}/search?q=` | Hits linking to call ids. | `analysis.search` |
| `GET /api/map` | Every readable run's stages joined into one graph, each node and edge with the runs that went through it, and each run's route. Stage names are decided over all runs (`Runs.staging`: the module depth and the head functions). | `analysis.route_map` |
| `GET /api/compare?a={run}&b={run}` | Summary counts, the first divergence (parent, path, calls only in A / only in B, argument diff, branch source). Both ids go through the run registry. | `analysis.compare` |

Previews: list endpoints never inline full values. Each binding returns a one-line `preview` (≤ 120 chars,
computed from the stored value tree) plus `value_id` for lazy loading.

## Measured at scale

A `rich` run of 110k calls, 223k replay steps (Python 3.13, x86-64 laptop):

| Request | Time |
|---|---|
| `/timeline`, first page (builds the step list, then cached) | 0.13 s |
| `/timeline`, any later page | < 10 ms |
| `/callgraph` (392 functions) | 0.08 s |
| `/watch?name=` | 0.01 s |

The 893k-call run of the 0.2 scale test takes 2.1–2.8 s for its first `/stages` pass, instantly after; the timeline
holds three integers per step in memory (about 100 MB for millions of calls). In the browser, a step draws in a few
milliseconds; in a graph of more than 40 functions the camera follows the current one.

## Limits and failure modes

- Single user and local. No authentication in v1. The `--host` warning is the guard.
- Live runs: v1 reads files as they are. A run still being written shows what has been flushed so far, and
  reloading shows more. Server-sent events for live updates are roadmap.
