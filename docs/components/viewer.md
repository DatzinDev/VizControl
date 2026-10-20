# Viewer

Source: `frontend/` (TypeScript, React, React Flow, elkjs, Vite). Built output: `src/vizcontrol/viewer/static/`.

## Responsibility

Render a run as a navigable, progressive graph and let the user move from the overview down to single values
without losing context. The viewer is a thin client. All analysis happens in the [server](server.md); the viewer
lays out, renders and navigates. Visual concept: [UI design](../ui-design.md).

## Status (v0.1)

| Done | Pending |
|---|---|
| Shell (top bar, breadcrumbs, runs/stages/filters panel, inspector), dark and light themes from the Penpot tokens | Timeline strip |
| Level 1 stage graph with main path. Double-click (or the stage list) opens the stage at Level 2. "Copy as Mermaid" on Level 1 and 2 | Moving between levels without animated zoom |
| Level 2: the stage as module › class › function boxes (elk compound layout, top to bottom), neighbouring stages collapsed around it; a function opens its first call, a collapsed stage opens that stage | |
| Level 3 call tree: lazy expansion, `×N` groups with paged members, opaque/unfinished/raised states | Diff: synced selection between the two sides |
| Diff (S7): pick run B (same target first), common path paired with `=`, the split in a dashed block, inspector with summary, first divergence, argument diff and the branch source; a card opens its call in its run | |
| Failure mode: opens on the origin, red error path, off-path dimming, exception/locals/last-changes/traceback/source. In context: "stopped here" on the stage (Level 1) and function (Level 2), the stages a reference run reached in grey, a "Stopped here" card and a compare link | Filters on the stage graph and Level 2 (they apply to the call tree) |
| Filters: stage checkboxes, minimum duration, library leaves. Filtered calls and what is expanded under them fold into one "hidden (N)" stub per parent; the selection and the error path always show | |
| Level 4 value tree, object history and field-level changes | Provenance marks beyond groups, stages, flow and changes |
| Source: excerpt in the inspector, and "View file" opens the whole file with the call or raise line in view (Esc closes it) | |
| ✂ on calls cut by a limit (at `max_depth` when `limit_depth` was hit, or where `max_calls` stopped), with the reason in the inspector | |
| ⌘K search; URL hash keeps `run` and `call`, `stage` or `obj`; browser back/forward walk every navigation (a run opening on its failure doesn't add an entry) | |
| Data Flow: object picker, lanes per module, Δ on changed fields, object inspector, follow from any oid chip | |
| Map: every served run on one stage graph, "N runs" on stages and edges, the selected run's route in accent (picking a run keeps the map); a stage lists the runs that went through it and those that never reached it, and double-click opens it in a run that did | |

## Requirements covered

FR-VIZ-1..13.

## Structure

```
App
├─ TopBar          run picker · view tabs (Execution Flow | Data Flow | Diff) · search (⌘K)
├─ Breadcrumbs     Run › Stage › Module › Function › Value   (Level 1→4 position)
├─ LeftPanel       runs list · module/stage filter tree · outcome & duration filters
├─ Canvas          React Flow graph, layout by elkjs (layered, left→right)
├─ Inspector       right panel: details of the selected node/edge/value
└─ Timeline        bottom strip: calls over time (seq), error marker, selection sync
```

State lives in one store (React context + reducer): `run`, `view`, `focus` (current breadcrumb path), `selection`,
`expanded` (set of node ids), `filters`. The URL hash encodes `run`, `view`, `focus` and `selection`, so any state
can be bookmarked and back/forward work (FR-VIZ-5).

## Navigation levels

| Level | Canvas shows | Inspector shows | Enter by |
|---|---|---|---|
| 1 Overview (FR-VIZ-1) | Stage nodes on the main path, other stages dimmed below; edges with call counts. | Run summary: target, outcome, duration, calls, truncation. | Opening a run. |
| 2 Components (FR-VIZ-2) | The selected stage expanded into module → class → function compound nodes (one node per function, its calls added up; imports and class bodies show as `import` and `class body`); call edges between them. Other stages stay as collapsed nodes at the edges for context. | Stage stats, entered from, calls out to, files. | Double-click a stage, the stage list, or the stage crumb at Level 3. |
| 3 Function (FR-VIZ-3) | Call subtree of the selected call (children loaded lazily, groups as "×N" nodes). | Location (file:line, link to source), caller, args, return, duration, exception, children, snapshots. | Click a function node (its first call). |
| 4 Values (FR-VIZ-4) | Unchanged. | Value tree with expand/collapse; for objects: appearances over time, diff between two selected states. | Click a value in the inspector. |

Moving between levels animates the zoom in or out and never replaces the canvas abruptly. `Esc` or a breadcrumb
click goes up one level.

## Graph rules

- **Progressive disclosure** (NFR-PERF-3): the canvas renders at most ~300 nodes. Expanding a node fetches its
  slice. Large child sets show the first page plus a "+N more" node. Groups (`×N`) expand into a paged list.
- **Node states** (FR-VIZ-7), shown by border and icon, never by color alone:
  `ok` solid, `raised` red with ⚠, `unfinished` amber with dashed border, `not observed` (opaque) hatched grey,
  `truncated` with a scissor badge.
- **Provenance** (FR-VIZ-13): observed edges are solid. Inferred edges (stage grouping, data-flow on inferred
  identity, thread-start links) are dashed and say "inferred" on hover.
- **Filters** (FR-VIZ-6): hide modules or stages, show only failing paths, minimum duration, hide opaque calls.
  Hidden nodes collapse into a single "hidden (N)" stub so the structure stays honest.
- **Search** (FR-ANA-8): ⌘K palette. Picking a hit reveals the node, expanding ancestors via `/calls/{id}/path`.

## Views

- **Execution Flow** (default): the levels above.
- **Failure mode** (FR-VIZ-8): when `run.outcome` is `raised` or `assertion_failed`, the viewer opens at Level 3 on
  the origin call. The error path is highlighted from root to origin, propagation is shown with red edges, and the
  inspector opens the exception panel (type, message, traceback with observed frames linked, recent changes).
  The failure is also shown in the whole graph. A **reference run** is picked: the latest ok run of the same
  target, else the ok run whose route on the map shares the most stages (another test, another request). Level 1
  marks the stage where it stopped and draws, in grey and dashed, the stages the reference reached and this run
  didn't ("not reached · seen in …": evidence from that run, never guessed); double-click opens one in the
  reference. Level 2 marks the function where it stopped. The run inspector's "Stopped here" card has the line,
  the locals at the raise and their last changes, and the failure banner and the card link to the Diff with the
  reference.
- **Data Flow** (FR-VIZ-10, Should): follow one object. Without one, the canvas lists the run's most seen objects
  to pick from. Any oid chip in the value tree, or "Follow in Data Flow" in the value inspector, opens it too. The
  canvas shows each appearance as a step, left → right in `seq` order, one lane per module. A step shows its role,
  the call, the object's fields and Δ on the fields that changed since the previous step; a step where the object
  sits inside another value says so (`in order.customer`). Clicking a step opens its call in Execution Flow, and the
  Data Flow tab goes back to the object. The inspector shows the lifecycle, the changes, what the data was made
  from and what it became (inferred hops to follow past a function that builds a new object), other objects in the
  run and the provenance note. The canvas starts at a readable zoom from the first step instead of fitting a long flow.
- **Diff** (FR-VIZ-11): the current run (A) and a picked run (B) side by side: the common path to where they split,
  each side's calls there, and why (argument diff, branch source), then the same calls that returned something
  different, in the order they returned, with the first difference in the inspector. The URL keeps `vs`. A card
  opens its call in its run; Back returns to the diff.
- **Source panel** (FR-VIZ-9, Should): read-only code view of the call's file with the call/raise line
  highlighted, from `/source`.

## Build and packaging

- `npm run build` in `frontend/` writes to `src/vizcontrol/viewer/static/`. A hatch build hook runs it when
  building the wheel, so `pip install vizcontrol` needs no Node.
- No CDN, fonts bundled, so it works offline (NFR-PKG-2).
- Dev: run `vizcontrol serve` (port 7171) and `npm run dev` in `frontend/`. Vite proxies `/api` to it.
- Nodes are given a fixed `width`/`height`. Without it, React Flow 12 hides re-created nodes until they are
  measured, and a click landing in that frame falls through to the pane, which breaks double-click.

## Limits

- Layout of very wide levels (hundreds of siblings) relies on paging, not on elkjs scaling.
- Light and dark themes come from the same tokens as the Penpot concept. The status colors are checked for
  contrast in both.
