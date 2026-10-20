"""The .vizc trace file: schema, batched Writer, read-only Reader. See docs/03-trace-format.md."""

from __future__ import annotations

import itertools
import json
import sqlite3
import threading
from pathlib import Path

from vizcontrol import FORMAT_VERSION, __version__

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE run (
  id INTEGER PRIMARY KEY CHECK (id = 1), kind TEXT NOT NULL, target TEXT NOT NULL, argv TEXT NOT NULL,
  cwd TEXT NOT NULL, python TEXT NOT NULL, started_wall TEXT NOT NULL, started_ns INTEGER NOT NULL,
  ended_ns INTEGER, outcome TEXT NOT NULL, exit_code INTEGER, config TEXT NOT NULL, truncated TEXT
);
CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, relpath TEXT, sha256 TEXT, source TEXT);
CREATE TABLE code (
  id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES file(id), module TEXT, qualname TEXT NOT NULL,
  first_line INTEGER, kind TEXT NOT NULL, observed INTEGER NOT NULL
);
CREATE TABLE call (
  id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES call(id), thread_id INTEGER NOT NULL,
  code_id INTEGER NOT NULL REFERENCES code(id), call_line INTEGER, depth INTEGER NOT NULL,
  seq_start INTEGER NOT NULL, seq_end INTEGER, start_ns INTEGER NOT NULL, end_ns INTEGER,
  outcome TEXT NOT NULL, exception_id INTEGER REFERENCES exception(id),
  end_exact INTEGER NOT NULL DEFAULT 1, children INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE value (
  id INTEGER PRIMARY KEY, object_id INTEGER, type TEXT NOT NULL, data TEXT NOT NULL,
  truncated INTEGER NOT NULL DEFAULT 0, redacted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE binding (
  call_id INTEGER REFERENCES call(id), seq INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT,
  line INTEGER, value_id INTEGER NOT NULL REFERENCES value(id)
);
CREATE TABLE exception (
  id INTEGER PRIMARY KEY, type TEXT NOT NULL, message TEXT, raised_call_id INTEGER REFERENCES call(id),
  raised_line INTEGER, handled_call_id INTEGER REFERENCES call(id), cause_id INTEGER REFERENCES exception(id),
  traceback TEXT NOT NULL, value_id INTEGER REFERENCES value(id)
);
CREATE TABLE gap (id INTEGER PRIMARY KEY, call_id INTEGER REFERENCES call(id), seq INTEGER NOT NULL,
  kind TEXT NOT NULL, detail TEXT);
"""
# Built on close: maintaining them during capture slows inserts. ponytail: a killed run's file has no indexes.
INDEXES = """
CREATE INDEX IF NOT EXISTS call_parent ON call(parent_id, seq_start);
CREATE INDEX IF NOT EXISTS call_code ON call(code_id);
CREATE INDEX IF NOT EXISTS binding_call ON binding(call_id, seq);
CREATE INDEX IF NOT EXISTS value_object ON value(object_id);
"""

COLUMNS = {
    "file": ("id", "path", "relpath", "sha256", "source"),
    "code": ("id", "file_id", "module", "qualname", "first_line", "kind", "observed"),
    "call": ("id", "parent_id", "thread_id", "code_id", "call_line", "depth", "seq_start", "seq_end",
             "start_ns", "end_ns", "outcome", "exception_id", "end_exact", "children"),
    "value": ("id", "object_id", "type", "data", "truncated", "redacted"),
    "binding": ("call_id", "seq", "kind", "name", "line", "value_id"),
    "exception": ("id", "type", "message", "raised_call_id", "raised_line", "handled_call_id", "cause_id",
                  "traceback", "value_id"),
    "gap": ("id", "call_id", "seq", "kind", "detail"),
}
_INSERT = {t: f"INSERT INTO {t} ({', '.join(c)}) VALUES ({', '.join('?' * len(c))})" for t, c in COLUMNS.items()}
_CLOSE = ("UPDATE call SET seq_end = ?, end_ns = ?, outcome = ?, exception_id = ?, end_exact = ?, children = ? "
          "WHERE id = ?")
RUN_FIELDS = ("kind", "target", "argv", "cwd", "python", "started_wall", "started_ns", "outcome", "config")


class Writer:
    """Buffers rows in memory; `flush()` writes them in one transaction. The caller decides when to flush
    (it watches `pending`), so the hot path is a lock and an append."""

    # ponytail: no time-based flush; add one if idle long-running programs need fresher files
    def __init__(self, path: str | Path, run: dict):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for leftover in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):  # an explicit -o overwrites
            leftover.unlink(missing_ok=True)
        self._ids = {t: itertools.count(1).__next__ for t in ("file", "code", "call", "value", "exception", "gap")}
        self._rows: dict[str, list] = {t: [] for t in COLUMNS}
        self._closes: list[tuple] = []
        self.pending = 0
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.executescript(SCHEMA)
        self.db.executemany("INSERT INTO meta VALUES (?, ?)",
                            [("format_version", FORMAT_VERSION), ("vizcontrol_version", __version__)])
        self.db.execute(f"INSERT INTO run (id, {', '.join(RUN_FIELDS)}) VALUES (1, {', '.join('?' * len(RUN_FIELDS))})",
                        [run[f] for f in RUN_FIELDS])

    def new_id(self, table: str) -> int:
        return self._ids[table]()

    def add(self, table: str, row: tuple) -> None:
        with self._lock:
            self._rows[table].append(row)
            self.pending += 1

    def close_call(self, call_id, seq_end, end_ns, outcome, exception_id=None, end_exact=1, children=0) -> None:
        """End a call whose row was already written while it was open."""
        with self._lock:
            self._closes.append((seq_end, end_ns, outcome, exception_id, end_exact, children, call_id))
            self.pending += 1

    def flush(self) -> None:
        with self._lock:
            if not self.pending:
                return
            self.db.execute("BEGIN")
            for table, rows in self._rows.items():
                if rows:
                    self.db.executemany(_INSERT[table], rows)
                    rows.clear()
            if self._closes:  # after inserts: a call may open and close within one batch
                self.db.executemany(_CLOSE, self._closes)
                self._closes.clear()
            self.db.execute("COMMIT")
            self.pending = 0

    def close(self, outcome: str, exit_code: int | None, ended_ns: int | None, truncated: list[str]) -> None:
        self.flush()
        with self._lock:
            self.db.execute("UPDATE run SET outcome = ?, exit_code = ?, ended_ns = ?, truncated = ? WHERE id = 1",
                            (outcome, exit_code, ended_ns, json.dumps(truncated) if truncated else None))
            self.db.executescript(INDEXES)
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("PRAGMA journal_mode = DELETE")
            self.db.close()


class Reader:
    """Read-only access to one .vizc file. Returns plain dicts."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        version = self._one("SELECT value FROM meta WHERE key = 'format_version'")["value"]
        if version.split(".")[0] != FORMAT_VERSION.split(".")[0]:
            raise ValueError(f"{self.path}: trace format {version} is not supported (expected {FORMAT_VERSION})")

    def _one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def run(self) -> dict:
        run = self._one("SELECT * FROM run")
        run["argv"] = json.loads(run["argv"])
        run["config"] = json.loads(run["config"])
        run["truncated"] = json.loads(run["truncated"]) if run["truncated"] else []
        return run

    def counts(self) -> dict[str, int]:
        return {t: self._one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in COLUMNS}

    def call(self, call_id: int) -> dict | None:
        return self._one(f"SELECT {self._CALL_COLS} {self._CALL_FROM} WHERE call.id = ?", (call_id,))

    def children(self, call_id: int | None, offset: int = 0, limit: int = 200) -> list[dict]:
        where = "call.parent_id IS NULL" if call_id is None else "call.parent_id = ?"
        args = () if call_id is None else (call_id,)
        return self._all(
            "SELECT call.*, code.qualname, code.module, code.observed FROM call JOIN code ON code.id = call.code_id "
            f"WHERE {where} ORDER BY call.seq_start LIMIT ? OFFSET ?", (*args, limit, offset))

    _CALL_COLS = ("call.*, code.qualname, code.module, code.kind, code.observed, code.first_line, code.file_id, "
                  "file.relpath, file.path")
    _CALL_FROM = "FROM call JOIN code ON code.id = call.code_id LEFT JOIN file ON file.id = code.file_id"

    def grouped_children(self, call_id: int | None) -> list[dict]:
        """Children of a call grouped by code: one row per code with count, first call and outcome summary."""
        where = "parent_id IS NULL" if call_id is None else "parent_id = ?"
        args = () if call_id is None else (call_id,)
        return self._all(
            "SELECT code_id, COUNT(*) AS count, MIN(seq_start) AS first_seq, MIN(id) AS first_id, "
            "SUM(outcome = 'raised') AS raised, SUM(outcome = 'unfinished') AS unfinished, "
            "SUM(COALESCE(end_ns - start_ns, 0)) AS total_ns "
            f"FROM call WHERE {where} GROUP BY code_id ORDER BY first_seq", args)

    def calls(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ", ".join("?" * len(ids))
        return self._all(f"SELECT {self._CALL_COLS} {self._CALL_FROM} WHERE call.id IN ({marks}) "
                         "ORDER BY call.seq_start", ids)

    def group_members(self, call_id: int | None, code_id: int, offset: int = 0, limit: int = 200) -> list[dict]:
        where = "call.parent_id IS NULL" if call_id is None else "call.parent_id = ?"
        args = () if call_id is None else (call_id,)
        return self._all(f"SELECT {self._CALL_COLS} {self._CALL_FROM} WHERE {where} AND call.code_id = ? "
                         "ORDER BY call.seq_start LIMIT ? OFFSET ?", (*args, code_id, limit, offset))

    def ancestors(self, call_id: int) -> list[dict]:
        """The call and its ancestors, root first."""
        rows = self._all(
            "WITH RECURSIVE up(id, n) AS (SELECT ?, 0 UNION ALL "
            "SELECT call.parent_id, up.n + 1 FROM call JOIN up ON call.id = up.id WHERE call.parent_id IS NOT NULL) "
            f"SELECT {self._CALL_COLS} {self._CALL_FROM} JOIN up ON up.id = call.id ORDER BY up.n DESC", (call_id,))
        return rows

    def all_calls(self) -> list[tuple]:
        """(id, parent_id, start_ns, end_ns, outcome, seq_start, module, observed) for every call, in seq order."""
        return self.db.execute(
            "SELECT call.id, call.parent_id, call.start_ns, call.end_ns, call.outcome, call.seq_start, code.module, "
            "code.observed FROM call JOIN code ON code.id = call.code_id ORDER BY call.seq_start").fetchall()

    def object_bindings(self, object_id: int) -> list[dict]:
        return self._all(
            "SELECT binding.call_id, binding.seq, binding.kind, binding.name, binding.line, binding.value_id, "
            "value.data, code.qualname FROM value JOIN binding ON binding.value_id = value.id "
            "JOIN call ON call.id = binding.call_id JOIN code ON code.id = call.code_id "
            "WHERE value.object_id = ? ORDER BY binding.seq", (object_id,))

    def search(self, text: str, limit: int = 100) -> dict[str, list[dict]]:
        like = f"%{text}%"
        return {
            "functions": self._all(
                "SELECT code.id AS code_id, code.qualname, code.module, code.observed, COUNT(call.id) AS count, "
                "MIN(call.id) AS call_id FROM code JOIN call ON call.code_id = code.id "
                "WHERE code.qualname LIKE ? OR code.module LIKE ? GROUP BY code.id ORDER BY count DESC LIMIT ?",
                (like, like, limit)),
            "variables": self._all(
                "SELECT binding.name, binding.kind, binding.call_id, value.data FROM binding "
                "JOIN value ON value.id = binding.value_id WHERE binding.name LIKE ? ORDER BY binding.seq LIMIT ?",
                (like, limit)),
            "values": self._all(
                "SELECT binding.name, binding.kind, binding.call_id, value.data FROM binding "
                "JOIN value ON value.id = binding.value_id WHERE value.data LIKE ? ORDER BY binding.seq LIMIT ?",
                (like, limit)),
        }

    def bindings(self, call_id: int) -> list[dict]:
        return self._all(
            "SELECT binding.*, value.type, value.data, value.object_id FROM binding "
            "JOIN value ON value.id = binding.value_id WHERE binding.call_id = ? ORDER BY binding.seq", (call_id,))

    def value(self, value_id: int) -> dict | None:
        v = self._one("SELECT * FROM value WHERE id = ?", (value_id,))
        if v:
            v["data"] = json.loads(v["data"])
        return v

    def exceptions(self) -> list[dict]:
        return self._all("SELECT * FROM exception ORDER BY id")

    def exception(self, exception_id: int) -> dict | None:
        return self._one("SELECT * FROM exception WHERE id = ?", (exception_id,))

    def gaps(self) -> list[dict]:
        return self._all("SELECT * FROM gap ORDER BY seq")

    def source(self, file_id: int) -> str | None:
        row = self._one("SELECT source FROM file WHERE id = ?", (file_id,))
        return row and row["source"]

    def file(self, file_id: int) -> dict | None:
        return self._one("SELECT * FROM file WHERE id = ?", (file_id,))

    def close(self) -> None:
        self.db.close()
