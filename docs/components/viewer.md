# Viewer

Source: `frontend/` (TypeScript, React, React Flow, elkjs, Vite). Built output: `src/vizcontrol/viewer/static/`.

## Responsibility

Show a run as a recording that can be stepped through, like a debugger after the fact: who called whom, with what,
what came back, where it failed. The viewer is a thin client: the [server](server.md) builds the timeline, the call
graph and the watches; the viewer lays them out and plays them.

## One view: Replay

- **Runs** on the left; the URL keeps `run` and `step`, so a link opens the same moment of the same run.
- **Call graph**: functions as nodes inside one box per module, so the layers of a project show; an arrow per
  "A called B". As the recording plays it lights up: functions already run, the call stack, the current function,
  red where it raised. Imports and class bodies are left off the graph. Callback arrows (see below) are dashed and
  labelled with where the function was (`via self.steps[1]`). Click a function to jump to its first call. In a graph
  of more than 40 functions the camera follows the current one.
- **Transcript**: one line per step, indented by depth: `→ clean_rows(rows=…)`, `← clean_rows returned …`,
  `✗ RateTable.rate raised KeyError: GBP`, and `↩` for a callback with its tag.
- **Scrubber**: play and pause (space), step (← →), jump (the bar, the red marks where a call raised), speed.
- **Panel**: the step's values (click one for its full tree and the object's history across the run), watched
  variables with their value at that step and "Δ now" when it changes there, and the call stack (click a frame to
  jump to where that call started). For a callback, how it was called: the caller's line, where the function was
  among the caller's arguments, and the first value it was seen in, as a jump.
- **Opening**: a failed run opens on the step where it raised; another where its work begins, past the imports.

The overview, component, call-tree, Data Flow, Diff and Map views were removed from the viewer in 0.4: they showed
structure, while what helps is seeing the run happen. Their analysis stays in the server and the CLI
(`vizcontrol diagram`, `vizcontrol diff`).

## Callbacks (inferred)

A call is a callback when the caller's source line doesn't name the function (`step(data)` runs `read_orders`,
`handler(data)` runs `Progress.on_step_done`, `map(clean_row, rows)` and `sorted(key=order_id)` hand a function to
a builtin), or when library code called it back. See [analysis](analysis.md#callbacks). Pending: step over / step
out, per-line steps.

## Build and packaging

- `npm run build` in `frontend/` writes to `src/vizcontrol/viewer/static/`. A hatch build hook runs it when
  building the wheel, so `pip install vizcontrol` needs no Node.
- No CDN, fonts bundled, so it works offline (NFR-PKG-2).
- Dev: run `vizcontrol serve` (port 7171) and `npm run dev` in `frontend/`. Vite proxies `/api` to it.
- Nodes are given a fixed `width`/`height`. Without it, React Flow 12 hides re-created nodes until they are
  measured, and a click landing in that frame falls through to the pane, which breaks double-click.

## Limits

- Steps are calls, returns and raises, not lines: a variable that changes inside one call shows its value at the
  call's arguments, its result and the locals where it raised (`--level state` adds every function's locals when
  it returns).
- The graph is every function of the run: a project of hundreds of functions is followed by the camera rather than
  read whole.
