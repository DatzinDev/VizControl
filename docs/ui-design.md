# UI design

Visual concept for the [viewer](components/viewer.md). The editable source is the Penpot file **VizControl**:

| Page | Contents |
|---|---|
| `Screens` | Boards `S1`…`S7` (1440×900), one per screen below. All screens use one sample project (an order checkout) and two runs, `test_checkout[basic]` (ok) and `test_checkout[vip]` (raised). |
| `Foundations` | Color swatches (dark and light), typography, provenance and status styles, spacing. |
| `Components` | `CallNode` (variant property `State`: ok, raised, unfinished, opaque, truncated, selected), `GroupNode`, `StageNode/*`, `FlowTag`, `LevelBadge`, `DivergenceMarker`, `Inspector/Section`, `Inspector/Callout`. |

The colors also exist in the file's library and as Penpot design tokens: sets `vizcontrol/dark` and
`vizcontrol/light`, themes `mode/dark` and `mode/light`.

## Principles

- **Graph first, details on demand.** The canvas always shows structure. Numbers and values live in the
  inspector.
- **Never lose your place.** A breadcrumb shows the current level (Run › Stage › Module › Function › Value). Each
  level transition is a zoom, not a page change.
- **Evidence is visible.** Observed is solid, inferred is dashed, not captured is hatched. Status is shown by shape
  and icon as well as color.
- **Failure is the landing page** when a run failed.

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

## Shell (all screens, 1440×900)

```
┌ TopBar: logo · run picker ▾ · [Execution Flow | Data Flow | Diff] · search ⌘K ─────────────┐
├ Breadcrumbs: Run › Stage › Module › Function › Value ────────────────────────────────────────┤
│ LeftPanel 260 │                 Canvas (graph)                         │ Inspector 360        │
│ runs          │                                                        │                      │
│ filters       │                                                        │                      │
├───────────────┴────────────────────────────────────────────────────────┴──────────────────────┤
└ Timeline 72: calls over seq, error marker, selection highlight ─────────────────────────────────┘
```

## Screens

| Screen | Level / view | What it shows | Answers (vision §2) |
|---|---|---|---|
| **S1 Overview** | L1, Execution Flow | Main path of stages `tests → app.api → app.services → app.repo → sqlalchemy`, with call counts on edges and the main path in accent. The opaque stage (`sqlalchemy`) is dashed and grey. Secondary stages (`app.validation`, `app.pricing`) hang below the stage that calls them. Inspector: run summary, slowest stages, and a provenance note saying stages are inferred. | Where did it start? What are the main stages? |
| **S2 Component expanded** | L2 | Stage `app.services` opened into module › class › function compound nodes (`orders.py › OrderService`, `cart.py › CartService`, `notify.py`) with call edges. Neighbouring stages stay collapsed around it. Inspector: stage stats, entry point, outgoing calls, files. | Which functions ran, from which files and modules? |
| **S3 Function inspector** | L3 | Call subtree of `OrderService.place_order`: aggregated group `validate_item ×12`, opaque `Session.commit`, and a "+11 more" paging node. Inspector: `app/services/orders.py:42`, caller, args with object ids, return, children, source excerpt. | What did each function receive and return? |
| **S4 Value inspector** | L4 | Canvas tags the calls where `order #40` appears. Inspector: value tree (with a redacted field), history of 5 appearances, diff between seq 318 and 1,021 (`status 'draft' → 'paid'`, `total`, `id`), and attribution marked inferred. | How did objects change? |
| **S5 Failure view** | L3, failure mode | Run `test_checkout[vip]` opens on the failure. The error path is red from the test down to `apply_discount`, and off-path calls are dimmed. A dashed data-flow link shows `rule #77` coming from `load_rules`. Inspector: `ValueError`, origin `discounts.py:88`, locals at raise, last change before raise (`rule.pct 20 → 120`), propagation, traceback with a not-observed frame. The timeline marks the raise. | Where did it fail and what led there? |
| **S6 Data Flow** | Data Flow | `order #40` followed across lanes (one per module) along time: `_build_order → Cart.total → _persist → send_receipt → place_order`, with Δ markers on changed fields. Inspector: lifecycle, changes, other objects, provenance. | How did data move between components? |
| **S7 Run comparison** | Diff | Run A `[basic]` vs run B `[vip]`, with call signatures aligned row by row. The first divergence is marked under `compute`: A calls `apply_percentage` (line 56), B calls `load_vip_rules` (line 54) and then the failing `apply_discount`. Inspector: summary counts, divergence, argument diff (`customer.tier 'basic' → 'vip'`), branch source. | Why did two runs take different paths? |

## Components

- **Call node**: name (mono), module (muted), duration badge, child count "+N". Variants:
  `ok`, `raised`, `unfinished`, `opaque`, `truncated` × `collapsed` / `expanded` / `selected`.
- **Group node** `×N`: stacked-card look, outcome summary (e.g. `11 ok · 1 raised`).
- **Stage node**: larger card with calls, time and a status strip.
- **Edges**: call (solid), inferred (dashed), data flow (`color.flow`), error propagation (`color.status.raised`,
  thicker).
- **Inspector section**: collapsible header + key/value rows (mono values, truncated with "…" and expand).
- **Value row**: name, type chip, preview, oid chip linking to Data Flow.

## Traceability

The success-criterion mapping in [requirements](01-requirements.md#traceability-to-the-success-criterion)
references screens S1–S6. S7 covers FR-VIZ-11 (Later).
