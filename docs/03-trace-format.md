# Trace format (`.vizc`)

A `.vizc` file is a SQLite 3 database holding exactly one run. It is the only contract between the capture
side and the exploration side (NFR-DEC-1). Covers FR-PER-1..5 and NFR-OBS.

## Conventions

- Time: integers in nanoseconds from `time.perf_counter_ns()`. `run.started_wall` holds the wall-clock anchor.
- Ordering: `seq` is a global, strictly increasing event counter per run. It gives a total order across threads
  as observed by the tracer.
- Ids: `INTEGER PRIMARY KEY` (rowid). They are only meaningful within one file.
- JSON columns hold compact JSON text.
- Journal mode is `WAL` while writing. The writer checkpoints and switches to `DELETE` on close, so the result is a
  single file.

## Schema (format version 1)

```sql
CREATE TABLE meta (            -- format_version, vizcontrol_version, created_at, ...
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE run (
  id             INTEGER PRIMARY KEY CHECK (id = 1),
  kind           TEXT NOT NULL,      -- script | module | api | pytest
  target         TEXT NOT NULL,      -- path, module name, qualname or test node id
  argv           TEXT NOT NULL,      -- JSON array
  cwd            TEXT NOT NULL,
  python         TEXT NOT NULL,      -- sys.version
  started_wall   TEXT NOT NULL,      -- ISO 8601 UTC
  started_ns     INTEGER NOT NULL,
  ended_ns       INTEGER,            -- NULL while running / unfinished
  outcome        TEXT NOT NULL,      -- running | ok | raised | assertion_failed | unfinished
  exit_code      INTEGER,
  config         TEXT NOT NULL,      -- JSON, effective capture config
  truncated      TEXT                -- JSON list of limits hit, NULL if none
);

CREATE TABLE file (
  id      INTEGER PRIMARY KEY,
  path    TEXT NOT NULL UNIQUE,      -- absolute path at capture time
  relpath TEXT,                      -- relative to run.cwd when inside it
  sha256  TEXT,
  source  TEXT                       -- NULL when store_source = false (FR-PER-3)
);

CREATE TABLE code (                  -- one row per Python code object seen
  id         INTEGER PRIMARY KEY,
  file_id    INTEGER NOT NULL REFERENCES file(id),
  module     TEXT,                   -- dotted module name, if known
  qualname   TEXT NOT NULL,          -- co_qualname
  first_line INTEGER NOT NULL,
  kind       TEXT NOT NULL,          -- function | method | lambda | module | generator | coroutine | comprehension
  observed   INTEGER NOT NULL        -- 1 included and traced; 0 opaque (excluded / native)
);

CREATE TABLE call (
  id           INTEGER PRIMARY KEY,
  parent_id    INTEGER REFERENCES call(id),   -- NULL only for the root call(s)
  thread_id    INTEGER NOT NULL,
  code_id      INTEGER NOT NULL REFERENCES code(id),
  call_line    INTEGER,              -- line in the parent where this call happened
  depth        INTEGER NOT NULL,
  seq_start    INTEGER NOT NULL,
  seq_end      INTEGER,
  start_ns     INTEGER NOT NULL,
  end_ns       INTEGER,
  outcome      TEXT NOT NULL,        -- ok | raised | unfinished
  exception_id INTEGER REFERENCES exception(id),
  children     INTEGER NOT NULL DEFAULT 0     -- direct child count, for lazy UI expansion
);

CREATE TABLE value (
  id        INTEGER PRIMARY KEY,
  object_id INTEGER,                 -- per-run identity of the live object (FR-VAL-3), NULL for immutables
  type      TEXT NOT NULL,           -- qualified type name
  data      TEXT NOT NULL,           -- JSON, see "Value encoding"
  truncated INTEGER NOT NULL DEFAULT 0,
  redacted  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE binding (               -- a name bound to a value at a moment
  call_id  INTEGER NOT NULL REFERENCES call(id),
  seq      INTEGER NOT NULL,
  kind     TEXT NOT NULL,            -- arg | return | local | yield | raise
  name     TEXT,                     -- arg/local name; NULL for return
  line     INTEGER,                  -- current line when captured
  value_id INTEGER NOT NULL REFERENCES value(id)
);

CREATE TABLE exception (
  id             INTEGER PRIMARY KEY,
  type           TEXT NOT NULL,
  message        TEXT,               -- redacted, truncated
  raised_call_id INTEGER NOT NULL REFERENCES call(id),
  raised_line    INTEGER NOT NULL,
  handled_call_id INTEGER REFERENCES call(id),  -- where it was caught; NULL if it escaped the run
  cause_id       INTEGER REFERENCES exception(id), -- __cause__ / __context__
  traceback      TEXT NOT NULL,      -- JSON [[file, line, qualname], ...] incl. frames not observed
  value_id       INTEGER REFERENCES value(id)
);

CREATE TABLE line (                  -- only for functions with per-line capture (FR-CAP-5)
  call_id INTEGER NOT NULL REFERENCES call(id),
  seq     INTEGER NOT NULL,
  line    INTEGER NOT NULL
);

CREATE TABLE gap (                   -- explicit "not captured" facts (NFR-OBS)
  id      INTEGER PRIMARY KEY,
  call_id INTEGER REFERENCES call(id),
  seq     INTEGER NOT NULL,
  kind    TEXT NOT NULL,             -- limit_calls | limit_depth | limit_values | thread_skipped | tracer_error
  detail  TEXT
);

CREATE INDEX call_parent  ON call(parent_id, seq_start);
CREATE INDEX call_code    ON call(code_id);
CREATE INDEX binding_call ON binding(call_id, seq);
CREATE INDEX value_object ON value(object_id);
```

Tables prefixed `x_` are reserved for future derived caches written by analysis. They must stay recomputable and
optional for readers. v1 writes none and caches analysis in memory.

## Value encoding

`value.data` is a tagged JSON tree. Every node has a `t` (tag):

| Tag | Shape | Notes |
|---|---|---|
| `none`, `bool`, `int`, `float` | `{"t":"int","v":42}` | Big ints stored as strings above 2^53. |
| `str`, `bytes` | `{"t":"str","v":"abc","len":1200}` | `len` present when truncated. Bytes as base64. |
| `list`, `tuple`, `set`, `frozenset` | `{"t":"list","oid":7,"items":[...],"len":500}` | |
| `dict` | `{"t":"dict","oid":8,"items":[[k,v],...],"len":30}` | Keys are encoded values. |
| `obj` | `{"t":"obj","oid":9,"type":"app.models.User","attrs":{"name":{...}}}` | From `__dict__`/`__slots__`, never from properties. |
| `ref` | `{"t":"ref","oid":9}` | Cycle or already-encoded object within the same value. |
| `opaque` | `{"t":"opaque","type":"socket.socket","oid":3}` | Type known, content not captured. |
| `redacted` | `{"t":"redacted"}` | Content removed by a redaction rule. |
| `trunc` | `{"t":"trunc"}` | Depth limit reached. |
| `custom` | `{"t":"custom","type":"...","v":{...}}` | From a user-registered serializer (FR-VAL-5). |

`oid` is the per-run object id (see [serialization](components/serialization.md)).

## Provenance mapping (NFR-OBS)

| Class | Where it lives |
|---|---|
| Observed | `call`, `binding`, `exception`, `line` rows; `code.observed = 1`. |
| Not captured | `code.observed = 0` (opaque calls), `gap` rows, `value.truncated/redacted`, `opaque`/`trunc`/`redacted` tags, `exception.traceback` frames without a matching call. |
| Inferred | Anything computed by analysis, exposed as API fields marked `"inferred": true`. |

## Compatibility rules

- `meta.format_version` is `MAJOR.MINOR`. Minor bumps only add tables, columns or tags; readers ignore what they
  don't know. Major bumps require a reader upgrade. Old readers refuse with a clear message (FR-PER-5).
- The viewer never reads SQLite directly. It sees the [server API](components/server.md), which can adapt older
  files.
- A file with `run.outcome = 'running'` that is not locked by a live writer is reported as `unfinished`.
