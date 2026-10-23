# UI design

The [viewer](components/viewer.md) is one screen, Replay: a run played back like a debugger after the fact. This
page is its visual design. The colors and type come from the Penpot file **VizControl** (page `Foundations`, design
token sets `vizcontrol/dark` and `vizcontrol/light`).

## Principles

- **Show the run happening.** What explains code is watching who calls whom, when and with what, not a structure
  diagram. Every element follows the current step.
- **Never lose your place.** The current call is lit on the graph, centered in the transcript and on top of the
  call stack; the URL keeps the run and the step.
- **Evidence is visible.** Library code (not observed inside) is dashed; inferred facts (callbacks) say so. Status
  is shown by symbol and shape as well as color.
- **Failure is the landing page** when a run failed: it opens on the step that raised.

## Foundations

Dark-first with a light theme. These tokens are the same names used by the frontend CSS variables.

| Token | Dark | Light | Use |
|---|---|---|---|
| `color.bg` | `#0E1116` | `#F6F8FA` | App background, canvas |
| `color.surface` | `#161B22` | `#FFFFFF` | Panels, nodes |
| `color.surface-2` | `#1F2630` | `#EEF1F4` | Hover, nested sections |
| `color.border` | `#2D3643` | `#D0D7DE` | Panel and node borders |
| `color.text` | `#E6EDF3` | `#1F2328` | Primary text |
| `color.text-muted` | `#8B98A9` | `#59636E` | Secondary text, labels |
| `color.accent` | `#4F8CFF` | `#0969DA` | Selection, focus, main path |
| `color.status.ok` | `#3FB950` | `#1A7F37` | Completed calls |
| `color.status.raised` | `#F85149` | `#CF222E` | Failed calls, error path |
| `color.status.unfinished` | `#D29922` | `#9A6700` | Unfinished calls |
| `color.status.opaque` | `#6E7681` | `#8C959F` | Not observed inside |
| `color.flow` | `#A371F7` | `#8250DF` | Data-flow edges and markers |

Type: **Inter** for UI (12/14/16/20), **JetBrains Mono** for code, values and locations (12/13). Spacing scale:
4, 8, 12, 16, 24, 32. Radius: 6 for nodes, 8 for panels.

## Layout (1440×900)

```
┌ TopBar 48: logo · run (outcome · duration) ───────────────────────────────────────────────────────────┐
│ Runs 260  │ Call graph: one box per module, functions in  [PNG] [GIF] [Reset layout] │ Panel 380          │
│ ● etl     │   ┌ etl.pipeline ────┐      ┌ etl.transforms.clean ┐                    │ STEP 61            │
│ ⚠ etl bad │   │ Pipeline.run ━━━━┿━━━━▶ │ clean_rows ━━▶ clean_row│                  │ ↩ clean_row(row=…) │
│           │   └──────────────────┘      └──────────────────────┘                    │ Callback · inferred│
│           ├──────────────────────────────────────────────────────────────────────────┤ values (click: tree)│
│           │ Transcript 230: one line per step, indented by depth, current centered   │ WATCH: name → value │
│           ├──────────────────────────────────────────────────────────────────────────┤ CALL STACK          │
│           │ ⏮ ◀ ▶ ▶| ⏭ ─────●──────────── raise marks ───── 61 / 80  speed          │                    │
└───────────┴──────────────────────────────────────────────────────────────────────────┴────────────────────┘
```

## Elements

- **Function node** (180×46, mono): name and `×N` calls. States: *not yet run* (faint), *already run*, *on the call
  stack* (accent border), *current* (accent fill and halo), *raised* (red fill and halo), *library* (dashed,
  muted). Dragging moves it within its module's box; selected nodes get a `color.flow` outline.
- **Module box**: the module's name at the top-left, its functions inside; dashed for a library. Dragging it carries
  its functions.
- **Edges** (bezier, arrowheads): *seen* `color.text-muted`, *not yet run* faint, *the path to the current call*
  thick `color.accent` with a glow and on top (red when the step raised), *callback* dashed `color.flow` labelled
  `via …` or `callback`. Labels carry `×N` calls; the path has none.
- **Transcript line** (mono 12): `→ call(args)`, `↩` for a callback with a `color.flow` tag, `← returned value` in
  `color.status.ok`, `✗ raised Type: message` in `color.status.raised`; later steps faded, the current one
  highlighted.
- **Run row**: outcome icon, label, duration; a muted ✕ on hover deletes the run (red on hover).
- **Graph actions** (top right, small outlined buttons): PNG, GIF, Reset layout; while exporting, the progress
  (`GIF 12/80`) replaces them.
- **Scrubber**: the play button in accent, red tick marks where calls raised, the step counter in mono.
- **Panel**: the step line, file and line, a dashed `color.flow` callback box, value rows (name, preview, 👁 to
  watch), watch cards (amber border and "Δ now" when the value changed at this step), call stack rows.

## History

The first concept (Penpot page `Screens`, boards S1–S7) had a zoomable overview of stages, a component view, a call
tree, a value inspector, a failure view, a Data Flow view and a run comparison. It was built in 0.1–0.3 and replaced
by Replay in 0.4, keeping the tokens and the value inspector.
