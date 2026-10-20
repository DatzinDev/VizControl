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

## API (v1)

All responses are JSON. Lists are paged with `offset`/`limit`. Fields derived by analysis carry
`"inferred": true`.

| Method & path | Returns | Backed by |
|---|---|---|
| `GET /api/runs` | Run summaries: id, target, kind, outcome, started, duration, calls, truncated, plus `max_depth` and `cut_calls` (where `max_calls` stopped tracing) so the viewer can mark cut nodes. | `Reader.run` |
| `GET /api/runs/{run}` | Full run metadata and config. | `Reader.run` |
| `GET /api/runs/{run}/stages` | Stage graph + main path (Level 1). | `analysis.stages` |
| `GET /api/runs/{run}/stage?name=` | One stage opened into module › class › function (Level 2). 404 unless `name` is a stage of the run, so only real stages are cached. | `analysis.stage_detail` |
| `GET /api/runs/{run}/tree?node=&depth=&limit=` | Tree slice with aggregated groups (Levels 2–3). Roots when `node` is omitted. `depth` ≤ 4. | `analysis.subtree` |
| `GET /api/runs/{run}/tree?node=&code=&offset=&limit=` | Members of one `×N` group, paged. | `analysis.group_members` |
| `GET /api/runs/{run}/calls/{id}` | Call detail: code, file, line, caller, timing, outcome, exception, child count. | `Reader.call` |
| `GET /api/runs/{run}/calls/{id}/bindings` | Args, return, snapshots (value ids + previews). | `Reader.bindings` |
| `GET /api/runs/{run}/calls/{id}/path` | Ancestors to root, for breadcrumbs and "reveal in tree". | `Reader` |
| `GET /api/runs/{run}/values/{id}` | Full value tree (Level 4). | `Reader.value` |
| `GET /api/runs/{run}/objects?limit=` | The run's most seen objects (top-level bindings), named after their first binding that isn't `self`. `limit` ≤ 500. | `Reader.objects` |
| `GET /api/runs/{run}/objects/{oid}` | Object appearances (nested ones included), flow links, changes. | `analysis.object_flow` |
| `GET /api/runs/{run}/error` | Error path, or `null`. | `analysis.error_path` |
| `GET /api/runs/{run}/source/{file_id}` | `{path, relpath, sha256, source}`; `source` is null if not stored. | `Reader.file` |
| `GET /api/runs/{run}/search?q=` | Hits linking to call ids. | `analysis.search` |
| `GET /api/compare?a={run}&b={run}` | Divergence point + differing subtrees (Later). | `analysis.compare` |

Previews: list endpoints never inline full values. Each binding returns a one-line `preview` (≤ 120 chars,
computed from the stored value tree) plus `value_id` for lazy loading.

## Limits and failure modes

- Single user and local. No authentication in v1. The `--host` warning is the guard.
- Live runs: v1 reads files as they are. A run still being written shows what has been flushed so far, and
  reloading shows more. Server-sent events for live updates are roadmap.
