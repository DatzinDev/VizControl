# Storage

`src/vizcontrol/store.py`

## Responsibility

Persist a run into a `.vizc` file ([trace format](../03-trace-format.md)) with low overhead and crash tolerance,
and provide the read API used by analysis and the server.

## Requirements covered

FR-PER-1..5, NFR-PERF-2, NFR-DEC-1.

## Interface

```python
class Writer:
    def __init__(self, path: Path, run_meta: dict): ...   # creates file, schema, meta, run row
    def new_id(self, table: str) -> int                    # ids assigned in Python, before rows are written
    def add(self, table: str, row: tuple) -> None          # append to the in-memory batch (lock + append)
    def close_call(self, call_id, ...) -> None             # UPDATE for a call written while still open
    pending: int                                           # rows waiting; the tracer decides when to flush
    def flush(self) -> None                                # one transaction
    def close(self, outcome, exit_code, ended_ns, truncated) -> None

class Reader:
    def __init__(self, path: Path): ...                   # read-only connection (mode=ro URI)
    def run(self) -> dict
    def children(self, call_id: int | None, offset=0, limit=200) -> list[dict]
    def call(self, call_id: int) -> dict                  # with code, file, exception
    def bindings(self, call_id: int) -> list[dict]
    def value(self, value_id: int) -> dict
    def object_history(self, object_id: int) -> list[dict]
    def source(self, file_id: int) -> str | None
    def search(self, text: str, limit=100) -> list[dict]
```

## Internal design

### Writer

- Ids are assigned in Python (counters), not by SQLite, so the tracer can reference a call id before the row is
  written.
- Rows go into per-table in-memory lists. `flush()` runs `executemany` per table inside one transaction. The tracer
  flushes when more than 20,000 rows are pending. There is no writer thread and no time-based flush in v1.
- Calls are normally written once, already closed (see [capture](capture.md#shadow-stack-and-call-tree-fr-cap-123)).
  Only calls open during a flush get an `INSERT` followed by an `UPDATE` on close.
- Indexes are created in `close()`, because maintaining them during capture slows inserts. A killed run's file has
  none, and readers still work, just slower.
- Pragmas while writing: `journal_mode=WAL`, `synchronous=NORMAL`. On `close()`: final flush, `run` row update,
  indexes, `wal_checkpoint(TRUNCATE)`, `journal_mode=DELETE`, so the file is one self-contained file.
- `file` rows are written on the first code object of each file, with source text if `store_source` is on
  (FR-PER-3) and always with the `sha256`.
- Crash tolerance (FR-PER-4): the runner registers `atexit` and `SIGTERM`/`SIGINT` handlers that flush and close
  with outcome `unfinished`. On `SIGKILL` the last committed batch survives thanks to WAL. Such a file keeps
  `outcome = running`. Telling it apart from a live run (writer lock check) is not implemented yet.

### Reader

- Opens with `file:...?mode=ro` URIs. Several readers can share a file safely.
- Checks `meta.format_version` (FR-PER-5).
- Returns plain dicts, which are serialized straight to JSON by the [server](server.md).
- `children()` pages by `seq_start` so a node with 100,000 children is never loaded at once (NFR-PERF-3).
- `search()` uses `LIKE` over `code.qualname`, `code.module`, `binding.name` in v1. An FTS5 index is a roadmap
  item if `LIKE` is too slow.

### File location and naming

Default output directory: `.vizcontrol/runs/` under the project root, file name
`<YYYYmmdd-HHMMSS>-<target-slug>.vizc`. `-o/--output` overrides the file, `--out-dir` the directory. VizControl
adds `.vizcontrol/` to a `.gitignore` inside that directory on first use.

## Config knobs

`out_dir`, `store_source`, `flush_every`, `flush_ms`.

## Limits and failure modes

- Disk full or permission errors on write disable tracing (as a tracer error) and leave the program running.
- SQLite writes happen on the traced thread that triggers the flush. The pause is bounded by the batch size and
  measured in the benchmarks. A background writer thread is the upgrade path if pauses matter.
- Very large runs (tens of millions of rows) are bounded by `max_calls` first.
