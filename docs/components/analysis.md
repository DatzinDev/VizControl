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
def stage_detail(reader, stage: str) -> StageDetail | None                                 # FR-VIZ-2
def error_path(reader) -> ErrorPath | None                                                # FR-ANA-5
def object_flow(reader, object_id: int) -> ObjectFlow                                     # FR-ANA-4/6
def value_diff(a: dict, b: dict) -> list[Change]                                          # FR-ANA-6
def compare(reader_a, reader_b) -> RunDiff  # GET /api/compare                          # FR-ANA-7
def search(reader, query: str) -> list[Hit]                                               # FR-ANA-8
```

Results are plain JSON-able dicts. The server caches the expensive ones (`stages`, `stage_detail`, `error_path`) per run and
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

A stage is a set of modules, or the calls below a head function. `Stager` assigns them, first match wins:

1. User rules: `[tool.vizcontrol.stages]` maps a stage name to module globs, e.g.
   `API = ["app.api.*"]`, `Service = ["app.services.*"]`, `Persistence = ["app.repo.*", "sqlalchemy.*"]`.
   A glob with a colon matches `module:qualname` and makes a head function: `Load = ["etl:load_*"]`.
2. Default: the module path truncated to `stage_depth` components below the project root (default 2:
   `app.services.orders` → `app.services`). When that gives more than 12 stages (`MAX_STAGES`), a package of
   many modules, the depth is 1: `black.linegen`, `black.nodes`, ... are one stage `black`, opened at Level 2.
   A configured `stage_depth` is used as is. `__main__` is named after the script (`checkout.py` → `checkout`)
   or the `-m` module. Opaque calls group under their top-level package (`sqlalchemy`, `requests`) and the stage
   is marked not observed inside. A standard library stage (`sys.stdlib_module_names`) is also marked `stdlib`:
   the main path never goes into one, and the viewer and Mermaid leave them out unless they raised.
3. Flat projects: with no function rules and fewer than 3 observed module stages, a one-file script would be one
   stage. Then each module stage is split: from the codes entering it (root calls, or calls from another stage),
   a chain of lone callees (`main()` → `run()`) stays the stage, and the functions the chain first fans out to
   (two or more) become head functions, one stage each, named by qualname (`extract`, `transform`, `load`). A
   loop body counts as lone: a callee with 90% of its siblings' calls from that caller (`handle()` ×1000 next to
   `setup()`) leads on, and its few siblings stay in the stage. The program's own entry (root calls) is followed
   before the calls a library makes back into the project (doctest, a test runner), so those don't take its place.

A head's stage takes every call below it that is in the head's own module stage, even through library calls
(callbacks); calls in other modules keep their module stage. Heads are code objects, found from the distinct
caller → callee code pairs, so assigning a stage to one call needs its ancestors (`Stager.of`, `head_of`), and a
full pass needs only the parent's head (`Stager.assign`).

Runs served together are staged alike (`Stager(reader, heads, depth)`, `Runs.staging`): the depth comes from all
runs' module stages together, and the server splits only when all runs together have fewer than 3 module stages, and then uses every head any run found on its own. A test that calls `parse()`
directly would otherwise see it as its module's entry, while the test that goes through `handle()` sees a
`parse` stage.

`StageGraph`: one node per stage (call count, total time of the calls entering it, self time, raised/unfinished
counts, first `seq`, modules, observed) and one edge per caller-stage → callee-stage pair (calls, time, first
`seq`). The **main path** starts at the stage of the first root call and keeps following the outgoing edge that
leads to the most calls (the subtree size of the calls crossing it; time only breaks ties) to a stage not yet on the
path. Counts are deterministic, while timings are noise in small runs (a time-based rule flipped between CI runs). That is the `Request → Controller → Service → ... → Response`
line of Level 1.

`vizcontrol diagram` (and `GET /api/runs/{run}/mermaid`) writes Level 1, or one stage opened as Level 2, as a
Mermaid flowchart (`vizcontrol.diagram`): the main path drawn thick, raised stages red, libraries dashed, and in
Level 2 module › class subgraphs with neighbouring stages as single nodes.

### Route map (many runs)

`route_map(graphs, outcomes)` joins the runs' stage graphs: one node per stage and one edge per stage pair that any
run went through, each with the ids of the runs that did and the sums of their counts, plus each run's route (its
stages and edges). Its main path starts at the most common entry stage. Served as `GET /api/map` over every
readable run; with `pytest --vizcontrol` that is the map of the whole suite, one route per test.

### One stage opened, Level 2 (FR-VIZ-2)

`stage_detail` makes one pass over all calls, with the same stage names as Level 1:

- **Functions**: one node per code object of the stage, with its calls, total and self time, raised and unfinished
  counts and first call. `cls` comes from the qualname (`OrderService.place_order` → `OrderService`; module-level
  and `<locals>` functions have none). A class body goes into its own class, and a module body is the import.
- **Edges**: caller code → callee code inside the stage, with a count.
- **Incoming / outgoing**: calls crossing the stage boundary, per (caller stage, caller code, callee code) and
  per (caller code, callee stage).
- **Stats**: modules, classes, functions, calls, the time of the calls entering the stage, and self time.

Every function of the stage is returned. Paging is the upgrade if a stage ever has thousands.

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

Built on the oids in stored value trees. An object is found when it is the bound value or sits anywhere inside it
(`order.customer`): the reader prefilters values whose JSON holds `"oid":N` followed by `,` or `}`, and analysis
walks each tree to the object's full encoding (a repeat in the same value is a `ref` and is skipped). The prefilter
scans the value table; an oid index written at capture is the upgrade if that gets slow.

- **Appearances**: every binding whose value (or a nested node) carries the oid, in `seq` order, with the call,
  module, role (`arg`, `return`, `self`, `local`, ...), the `path` inside the bound value (empty for the value
  itself), the object's first fields and the fields that changed since the previous full encoding. A
  depth-truncated encoding is an appearance but is not diffed. The response caps appearances at 200 and gives
  `total`.
- **Links**: consecutive appearances in different calls become a flow edge: producer → consumer.
  - `return` of A then `arg` of B: a handoff.
  - `arg` of A, then `arg` of a child of A: a pass-down.
- **Changes**: two consecutive encodings of the same oid that differ are diffed with `value_diff`. The change is
  attributed to the innermost call whose execution lies between the two appearances. The attribution is
  inferred, and exact only when `state` level captured snapshots at both ends.
- Links on oids from non-weak-referenceable objects are labelled `identity: inferred` (see
  [serialization](serialization.md#object-identity-fr-val-3)).
- **Hops** (inferred): identity stops where a function builds a new list or record from the object. `became` lists
  what the calls that took the object in returned (a new object to follow, or a value such as a string), and
  `made_from` the objects passed to the call that returned it (`self` aside). A result that still holds the
  object is the same data, not a hop.

`value_diff` compares two value trees by path and returns `added`, `removed` and `changed` entries
(`path`, `before`, `after`). Truncated subtrees compare as "unknown", never as "equal".

### Run comparison (FR-ANA-7)

- Each run's calls in pre-order (seq order) as `(depth, module, qualname)`. Outcomes are left out: a failing run's
  callers all raise and are still the same calls.
- The first divergence is where the common prefix ends, found in linear time. `difflib.SequenceMatcher` only
  aligns the middle, between the common prefix and suffix, capped at 5,000 calls per side, for the extent of that
  first block and the counts (`summary.truncated` says when the cap applied). A full `difflib` pass took 60 s on a
  repetitive 262k-call run; this takes about 1 s.
- The divergence's parent: of the parents of the first differing call on each side, the deeper one. That is where
  execution split; a shallower difference is usually its consequence (B raised, so it never reached the repo).
- Reported: the parent and its ancestors (paired, A and B), the parent's children only in A and only in B with
  their `call_line`, the parent's argument differences (`value_diff`), and the source lines around those call lines
  (usually the branch that decided).
- **Different results**: the aligned pairs (prefix, suffix and equal blocks) whose return values differ, object ids
  blanked since they differ between runs. Listed in the order they returned in A, so the first one is usually
  where the results started to differ and the next ones carry it to the callers; each with `value_diff`. This is
  how two runs on different inputs differ when they take the same path.
- Later: aggregation of repeated siblings before aligning.

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
