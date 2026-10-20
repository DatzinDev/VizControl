# Analysis

`src/vizcontrol/analysis.py` (one module until it outgrows it)

## Responsibility

Pure functions that turn stored, observed events into the structures the viewer navigates: call tree, stages,
aggregated groups, data-flow links, value diffs, the error path and run differences. Analysis only reads through
the [storage Reader](storage.md). Everything it produces is **inferred** and labelled that way (NFR-OBS).

## Requirements covered

FR-ANA-1..8, supports FR-VIZ-1..13.

## Interface

```python
def subtree(reader, call_id: int | None, depth=1, limit=200, stage_of=None) -> list[dict]   # FR-ANA-1/3
def group_members(reader, call_id, code_id, offset=0, limit=200) -> list[dict]           # expand a "×N" group
def stages(reader) -> StageGraph   # rules come from the run's recorded config             # FR-ANA-2
def error_path(reader) -> ErrorPath | None                                                # FR-ANA-5
def object_flow(reader, object_id: int) -> ObjectFlow                                     # FR-ANA-4/6
def value_diff(a: dict, b: dict) -> list[Change]                                          # FR-ANA-6
def compare(reader_a, reader_b) -> RunDiff                                                # FR-ANA-7
def search(reader, query: str) -> list[Hit]                                               # FR-ANA-8
```

Results are plain JSON-able dicts. The server caches the expensive ones (`stages`, `error_path`) per run and
drops them when the file's mtime changes. Nothing is written back into `.vizc` files in v1.

## Algorithms

### Call tree and aggregation (FR-ANA-1/3)

- The tree comes straight from `call.parent_id`. `subtree()` returns `depth` levels below `call_id`, paging
  children with `limit`.
- **Aggregation**: siblings with the same `code_id` under one parent fold into a group node
  `{code_id, count, outcomes: {ok, raised, unfinished}, total_ns, first_call_id}` when `count > 1`. It is computed
  with one `GROUP BY` over the parent's children, ordered by first occurrence. Expanding a group pages its members
  (`group_members`). This turns a 10,000-iteration loop into one node. More than `limit` groups end with a
  `{"type": "more", "remaining": N}` node.
- Each node carries `children` (direct child count, from the trace) so the viewer can show "+N" without loading.

### Stages, Level 1 (FR-ANA-2)

A stage is a set of modules. Assignment, first match wins:

1. User rules: `[tool.vizcontrol.stages]` maps a stage name to module globs, e.g.
   `API = ["app.api.*"]`, `Service = ["app.services.*"]`, `Persistence = ["app.repo.*", "sqlalchemy.*"]`.
2. Default: the module path truncated to `stage_depth` components below the project root (default 2:
   `app.services.orders` → `app.services`). Opaque calls group under their top-level package (`sqlalchemy`,
   `requests`) and the stage is marked not observed inside.

`StageGraph`: one node per stage (call count, total time of the calls entering it, self time, raised/unfinished
counts, first `seq`, modules, observed) and one edge per caller-stage → callee-stage pair (calls, time, first
`seq`). The **main path** starts at the stage of the first root call and keeps following the heaviest outgoing
edge (by time) to a stage not yet on the path. That is the `Request → Controller → Service → ... → Response`
line of Level 1.

### Error path (FR-ANA-5)

For the exception that ended the run (or, for `assertion_failed`, the assertion):

- **Origin**: `raised_call_id` and `raised_line`, plus the last snapshot of that call.
- **Path to origin**: the ancestors of the origin call up to the root (how execution got there).
- **Propagation**: calls with `outcome = raised` and the same `exception_id`, in unwind order. It ends at the
  handler or escapes the run.
- **Chain**: `cause_id` links for `raise ... from ...` / implicit context.
- **Recent changes**: for each object among the locals at the raise, the last changes found by `object_flow`.
  For example, `rule.pct 20 → 120` between `Rule.__init__` and `load_vip_rules`. This answers "what changed just
  before it broke".

Exceptions that were raised and handled are listed separately. The viewer can show them, but they are not the
failure.

### Data flow (FR-ANA-4/6)

Built on `value.object_id` (top-level bindings only in v1; an object nested inside another value is not found):

- **Appearances**: every binding whose value (or a nested node) carries the oid, in `seq` order, with the call and
  role (`arg`, `return`, `local`, `attr-of`).
- **Links**: consecutive appearances in different calls become a flow edge: producer → consumer.
  - `return` of A then `arg` of B: a handoff.
  - `arg` of A, then `arg` of a child of A: a pass-down.
- **Changes**: two consecutive encodings of the same oid that differ are diffed with `value_diff`. The change is
  attributed to the innermost call whose execution lies between the two appearances. The attribution is
  inferred, and exact only when `state` level captured snapshots at both ends.
- Links on oids from non-weak-referenceable objects are labelled `identity: inferred` (see
  [serialization](serialization.md#object-identity-fr-val-3)).

`value_diff` compares two value trees by path and returns `added`, `removed` and `changed` entries
(`path`, `before`, `after`). Truncated subtrees compare as "unknown", never as "equal".

### Run comparison (FR-ANA-7, Later)

- Build each run's pre-order signature: `(depth, module, qualname, outcome)` per call, after aggregation.
- Align with `difflib.SequenceMatcher`. The first non-`equal` opcode is the **divergence point**.
- Report its parent call in both runs, with argument differences (`value_diff` on the parent's args) and the
  `call_line`s where the children start to differ, which points at the branch that decided the path. With
  per-line capture on that parent, the differing line sequences are reported too.
- Also returned: subtrees only in A, only in B, and matched calls whose return values differ.

### Search (FR-ANA-8)

Function/module name substring, variable name, and string/number content of encoded values. Results link to call
ids. Pages of 100.

## Limits and failure modes

- Stage heuristics are only as good as the module layout. Rules in `pyproject.toml` are the escape hatch, and the
  UI labels stages as inferred groupings.
- Data-flow on immutable values (ints, strings) is not tracked by identity. Equal values are not claimed to be the
  same datum.
- `compare` on very different runs produces large diffs. The API caps the result and returns the first divergence
  first.
