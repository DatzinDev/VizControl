import { applyNodeChanges, Background, Controls, Edge, Handle, MarkerType, Node, NodeChange, NodeProps, Position, ReactFlow, useReactFlow } from "@xyflow/react";
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, Binding, Callback, CallGraph, CallNode, Step, StepValue, Watched } from "./api";
import { shortName } from "./format";
import { ValueInspector } from "./Inspector";
import { layoutTree, TreeBox } from "./layout";

const PAGE = 500;  // steps fetched at a time
const FN_W = 180;
const FN_H = 46;
const SPEEDS = [1, 2, 5, 10, 25];  // steps per second

type FnState = "future" | "seen" | "stack" | "current" | "failed";

const handles = (
  <>
    <Handle type="target" position={Position.Left} />
    <Handle type="source" position={Position.Right} />
  </>
);

/** Each function's state at the current step. Read from context so that the node objects handed to React Flow stay
 * the same between steps: new node objects make it re-adopt them and drop every edge for a frame (the arrows
 * flickered away while playing). */
const StepState = createContext<(codeId: number) => FnState>(() => "future");

function FnNode({ data }: NodeProps) {
  const d = data as { codeId: number; label: string; kind: string; calls: number; observed: boolean };
  const state = useContext(StepState)(d.codeId);
  const name = d.kind === "module" ? "import" : d.kind === "class" ? `class ${d.label}` : d.label;
  return (
    <div className={`fn ${state} ${d.observed ? "" : "lib"}`} title={d.label}>
      {handles}
      <span className="q">{name}</span>
      <span className="count">{d.calls > 1 ? `×${d.calls}` : ""}</span>
    </div>
  );
}
/** A module (a file of the project, or a library): the box its functions sit in, so the layers show. */
function FileNode({ data }: NodeProps) {
  const d = data as { label: string; observed: boolean };
  return <div className={`file-box ${d.observed ? "" : "lib"}`}><span className="mono">{d.label}</span></div>;
}

const nodeTypes = { fn: FnNode, file: FileNode };

/** In a graph too big to read whole, the camera follows the current function, as a debugger follows the line. */
function FollowCurrent({ at, big }: { at?: { x: number; y: number }; big: boolean }) {
  const flow = useReactFlow();
  useEffect(() => {
    if (big && at) flow.setCenter(at.x + FN_W / 2, at.y + FN_H / 2, { zoom: Math.max(flow.getZoom(), 0.9), duration: 250 });
  }, [at?.x, at?.y, big]);
  return null;
}

const clip = (s: string, n = 48) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** One line of the transcript: what happened at a step, as a debugger would say it. */
function describe(s: Step): string {
  const name = s.qualname === "<module>" ? `${s.module ?? "module"} (module body)` : shortName(s.qualname);
  if (s.kind === "call" && s.qualname === "<module>") return `▶ ${name}`;  // the program's entry
  if (s.kind === "call") return `${s.callback ? "↩" : "→"} ${name}(${s.values.filter((v) => v.kind === "arg").map((v) => `${v.name}=${clip(v.preview, 32)}`).join(", ")})`;
  if (s.kind === "raise") return `✗ ${name} raised ${s.exception ? `${s.exception.type}: ${s.exception.message}` : ""}`;
  const ret = s.values.find((v) => v.kind === "return");
  return `← ${name}${ret ? ` returned ${clip(ret.preview, 60)}` : ""}`;
}

/** The short tag of a callback in the transcript. */
function callbackTag(cb: Callback): string {
  return cb.by ? `called back by ${cb.by}` : cb.via ? `callback · via ${cb.via}` : "callback · passed in";
}

/** Where the user moved nodes on this run's graph (per viewer, like the watches): id -> position. */
function readMoved(runId: string): Record<string, { x: number; y: number }> {
  try {
    return JSON.parse(localStorage.getItem(`vizcontrol.layout.${runId}`) ?? "{}");
  } catch {
    return {};
  }
}

function saveMoved(runId: string, nodes: Node[] | null) {
  try {
    if (nodes) localStorage.setItem(`vizcontrol.layout.${runId}`, JSON.stringify(Object.fromEntries(nodes.map((n) => [n.id, n.position]))));
    else localStorage.removeItem(`vizcontrol.layout.${runId}`);
  } catch {
    // a per-viewer convenience: without storage the layout simply isn't remembered
  }
}

function readWatches(): string[] {
  try {
    return JSON.parse(localStorage.getItem("vizcontrol.watches") ?? "[]");
  } catch {
    return [];
  }
}

/** The last value a watched variable had at or before `seq`, and the one before it. */
function at(list: Watched[], seq: number): Watched | undefined {
  let lo = 0, hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].seq <= seq) lo = mid + 1;
    else hi = mid;
  }
  return lo ? list[lo - 1] : undefined;
}

/**
 * The run as a recording: the call graph lights up as the program goes, a transcript says who called whom with
 * what and what came back, and watches follow variables, like stepping through a debugger after the fact.
 */
export function ReplayView(props: {
  runId: string;
  start: { step?: number; endOf?: number };  // the step to open on, or the call whose end (a raise) to open on
  onStep: (i: number) => void;
}) {
  const { runId, start, onStep } = props;
  const [opened, setOpened] = useState<Binding | null>(null);  // a value open in the side panel
  const [pages, setPages] = useState<Map<number, Step[]>>(new Map());
  const [total, setTotal] = useState(0);
  const [raises, setRaises] = useState<number[]>([]);
  const [pos, setPos] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(5);
  const [graph, setGraph] = useState<CallGraph | null>(null);
  const [positions, setPositions] = useState<Map<string, { x: number; y: number; width: number; height: number }> | null>(null);
  const [stack, setStack] = useState<CallNode[]>([]);
  const [watches, setWatches] = useState<string[]>(readWatches);
  const [watched, setWatched] = useState<Map<string, Watched[]>>(new Map());
  const [newWatch, setNewWatch] = useState("");
  const loading = useRef(new Set<number>());
  const transcript = useRef<HTMLDivElement>(null);

  // a run opens at its start, or where a given call ended (a failure: where it raised)
  useEffect(() => {
    let live = true;
    setPages(new Map());
    loading.current.clear();
    setWatched(new Map());
    setPos(null);
    api.timeline(runId, start.endOf != null ? { endOf: start.endOf } : { offset: start.step ?? 0 }, 1).then((p) => {
      if (!live) return;
      setTotal(p.total);
      setRaises(p.raises);
      setPos(Math.min(p.offset, Math.max(p.total - 1, 0)));
    });
    api.callgraph(runId).then((g) => live && setGraph(g));
    return () => {
      live = false;
    };
  }, [runId]);

  useEffect(() => {
    if (pos != null) onStep(pos);
  }, [pos]);

  const step = (i: number): Step | undefined => pages.get(Math.floor(i / PAGE))?.[i % PAGE];
  useEffect(() => {  // the page of the current step and its neighbours, for the transcript
    if (pos == null) return;
    for (const page of new Set([Math.floor(Math.max(pos - 12, 0) / PAGE), Math.floor(pos / PAGE), Math.floor(Math.min(pos + 12, total - 1) / PAGE)])) {
      if (pages.has(page) || loading.current.has(page) || page * PAGE >= total) continue;
      loading.current.add(page);
      api.timeline(runId, { offset: page * PAGE }, PAGE).then((p) => setPages((m) => new Map(m).set(page, p.steps)));
    }
  }, [pos, total, pages, runId]);

  const modules = useMemo(() => {  // module -> its functions: one box per file, so the architecture's layers show
    const m = new Map<string, CallGraph["nodes"]>();
    for (const n of graph?.nodes ?? []) {
      if (n.kind === "module" || n.kind === "class") continue;  // imports and class bodies: setup, not the flow
      const key = n.module ?? "?";
      if (!m.has(key)) m.set(key, []);
      m.get(key)!.push(n);
    }
    return m;
  }, [graph]);
  const flowEdges = useMemo(() => {  // the edges between the functions shown
    const shown = new Set([...modules.values()].flat().map((n) => n.code_id));
    return (graph?.edges ?? []).filter((e) => shown.has(e.source) && shown.has(e.target));
  }, [graph, modules]);
  useEffect(() => {
    if (!graph) return;
    const tree: TreeBox[] = [...modules].map(([m, fns]) => ({ id: `m:${m}`, children: fns.map((n) => ({ id: `c${n.code_id}`, width: FN_W, height: FN_H })) }));
    layoutTree(tree, flowEdges.map((e) => ({ id: `${e.source}>${e.target}`, source: `c${e.source}`, target: `c${e.target}` })), "RIGHT")
      .then(setPositions);
  }, [graph, modules, flowEdges]);

  const cur = pos != null ? step(pos) : undefined;
  const prevSeq = pos != null && pos > 0 ? step(pos - 1)?.seq ?? -1 : -1;

  useEffect(() => {  // the call stack at this step: who called whom to get here
    if (!cur) return;
    const t = setTimeout(() => api.path(runId, cur.call_id).then(setStack).catch(() => undefined), playing ? 120 : 0);
    return () => clearTimeout(t);
  }, [cur?.call_id, runId]);

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setPos((p) => {
      if (p == null || p >= total - 1) {
        setPlaying(false);
        return p;
      }
      return p + 1;
    }), 1000 / speed);
    return () => clearInterval(t);
  }, [playing, speed, total]);

  useEffect(() => {
    try {
      localStorage.setItem("vizcontrol.watches", JSON.stringify(watches));
    } catch {
      // per-viewer convenience only
    }
    for (const name of watches) {
      if (!watched.has(name)) api.watch(runId, name).then((list) => setWatched((m) => new Map(m).set(name, list)));
    }
  }, [watches, runId, watched]);

  const go = (i: number) => {
    setPlaying(false);
    setPos(Math.max(0, Math.min(total - 1, i)));
  };
  const jumpToSeq = (seq: number) => api.timeline(runId, { seq }, 1).then((p) => go(p.offset));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || pos == null) return;
      if (e.key === "ArrowRight") go(pos + 1);
      else if (e.key === "ArrowLeft") go(pos - 1);
      else if (e.key === " ") setPlaying((p) => !p);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {  // keep the current line in the middle of the transcript
    transcript.current?.querySelector(".line.now")?.scrollIntoView({ block: "center" });
  });

  const stackCodes = useMemo(() => stack.map((c) => c.code_id), [stack]);
  const shown = useRef<Step | undefined>(undefined);  // the last step drawn: kept while the next page loads
  if (cur) shown.current = cur;
  const now = shown.current;
  const laidOut = useMemo<Node[]>(() => {
    if (!graph || !positions) return [];
    const out: Node[] = [];
    for (const [m, fns] of modules) {  // a parent before its children, as React Flow wants
      const box = positions.get(`m:${m}`) ?? { x: 0, y: 0, width: FN_W, height: FN_H };
      out.push({ id: `m:${m}`, type: "file", position: { x: box.x, y: box.y }, width: box.width, height: box.height,
        data: { label: m, observed: fns.some((n) => n.observed) } });
      for (const n of fns) {
        const r = positions.get(`c${n.code_id}`) ?? { x: 0, y: 0 };
        // extent: a function moves inside its module's box, never out of it
        out.push({ id: `c${n.code_id}`, type: "fn", parentId: `m:${m}`, extent: "parent", position: { x: r.x, y: r.y }, width: FN_W, height: FN_H,
          data: { codeId: n.code_id, label: n.qualname, kind: n.kind, calls: n.calls, observed: n.observed } });
      }
    }
    return out;
  }, [graph, positions, modules]);
  // the nodes as shown: the layout, with what the user dragged (functions, or whole module boxes) where they left it.
  // They change only when the user moves or selects something, so stepping keeps them stable (see StepState).
  const [nodes, setNodes] = useState<Node[]>([]);
  useEffect(() => {
    const moved = readMoved(runId);
    setNodes(laidOut.map((n) => (moved[n.id] ? { ...n, position: moved[n.id] } : n)));
  }, [laidOut, runId]);
  const onNodesChange = (changes: NodeChange[]) => {
    setNodes((ns) => {
      const next = applyNodeChanges(changes, ns);
      if (changes.some((c) => c.type === "position" && !c.dragging)) saveMoved(runId, next);  // a drag ended
      return next;
    });
  };
  const resetLayout = () => {
    saveMoved(runId, null);
    setNodes(laidOut);
  };
  const absolute = (code: number) => {  // where a function sits on the canvas: its box's position plus its own
    const n = nodes.find((x) => x.id === `c${code}`);
    const box = n && nodes.find((x) => x.id === n.parentId);
    return n && box ? { x: box.position.x + n.position.x, y: box.position.y + n.position.y } : undefined;
  };
  const stateOf = useMemo(() => {
    const onStack = new Set(stackCodes);
    const first = new Map(graph?.nodes.map((n) => [n.code_id, n.first_seq]));
    return (code: number): FnState => !now ? "future" : code === now.code_id ? (now.kind === "raise" ? "failed" : "current")
      : onStack.has(code) ? "stack" : (first.get(code) ?? Infinity) <= now.seq ? "seen" : "future";
  }, [now, stackCodes, graph]);
  const edges = useMemo(() => {
    if (!graph || !now) return [] as Edge[];
    const cur = now;
    const active = new Set(stackCodes.slice(1).map((c, i) => `${stackCodes[i]}>${c}`));
    return flowEdges.map((e): Edge => {
      const id = `${e.source}>${e.target}`;
      const live = active.has(id);
      const seen = e.first_seq <= cur.seq;
      // a callback (a function the caller held, or a library calling back) is dashed, in the data-flow colour
      const color = live ? (cur.kind === "raise" ? "var(--raised)" : "var(--accent)") : !seen ? "var(--border)"
        : e.callback ? "var(--flow)" : "var(--muted)";
      const tag = e.callback ? (e.callback.by ? "callback" : e.callback.via ? `via ${e.callback.via}` : "callback") : "";
      const count = e.calls > 1 ? `×${e.calls}` : "";
      // the path to the current call is solid, thick and on top; bezier curves keep edges that share an end apart
      // (orthogonal ones ran along the same lanes and hid it); dashes stay for callbacks
      return { id, source: `c${e.source}`, target: `c${e.target}`, type: "default", zIndex: live ? 20 : 10,
        className: live ? "live" : undefined,
        label: seen && !live ? [tag, count].filter(Boolean).join(" ") || undefined : undefined,  // nothing over the path
        style: { stroke: color, strokeWidth: live ? 3.5 : 1.2, opacity: seen ? 1 : 0.35, strokeDasharray: e.callback && !live ? "6 4" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: live ? 18 : 14, height: live ? 18 : 14 } };
    });
  }, [graph, now, stackCodes, flowEdges]);

  if (pos == null || !graph) return <div className="empty">Loading the recording…</div>;
  if (!total) return <div className="empty">Nothing was traced in this run.</div>;

  const lines: Step[] = [];
  for (let i = Math.max(0, pos - 10); i <= Math.min(total - 1, pos + 10); i++) {
    const s = step(i);
    if (s) lines.push(s);
  }
  const addWatch = (name: string) => {
    const n = name.trim();
    if (n && !watches.includes(n)) setWatches([...watches, n]);
    setNewWatch("");
  };

  return (
    <div className="replay">
      <div className="replay-stage">
        <div className="replay-graph">
          {positions && (
            <StepState.Provider value={stateOf}>
            <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} key={runId} onNodesChange={onNodesChange}
              onNodeDoubleClick={(_, n) => {
                const g = graph.nodes.find((x) => `c${x.code_id}` === n.id);
                if (g) jumpToSeq(g.first_seq);
              }}
              multiSelectionKeyCode={["Meta", "Control"]} selectionKeyCode="Shift"
              nodesDraggable nodesConnectable={false} zoomOnDoubleClick={false} fitView
              fitViewOptions={{ padding: 0.15, maxZoom: 1.2 }} minZoom={0.1} proOptions={{ hideAttribution: true }}>
              <Background gap={24} size={1} color="var(--border)" />
              <Controls showInteractive={false} position="bottom-right" />
              <FollowCurrent at={now && absolute(now.code_id)} big={nodes.length > 40} />
            </ReactFlow>
            </StepState.Provider>
          )}
          <div className="replay-legend">drag to rearrange (Ctrl/⌘-click: several · Shift-drag: a box) · double-click a function to jump to its first call · ← → step · space play</div>
          <button className="reset-layout" onClick={resetLayout} title="Put every node back where the automatic layout had it">Reset layout</button>
        </div>
        <div className="transcript" role="log" aria-label="What happened, step by step" ref={transcript}>
          {lines.map((s) => (
            <button key={s.i} className={`line ${s.kind} ${s.i === pos ? "now" : ""} ${s.observed ? "" : "lib"} ${s.i > pos ? "later" : ""}`}
              style={{ paddingLeft: 8 + s.depth * 16 }} onClick={() => go(s.i)}>
              <span className="n">{s.i + 1}</span>
              <span className="what">{describe(s)}</span>
              {s.kind === "call" && s.callback && <span className="cb">{callbackTag(s.callback)}</span>}
            </button>
          ))}
        </div>
        <div className="scrubber">
          <button onClick={() => go(0)} title="First step">⏮</button>
          <button onClick={() => go(pos - 1)} title="Previous step (←)">◀</button>
          <button className="play" onClick={() => setPlaying((p) => !p)} title="Play / pause (space)">{playing ? "⏸" : "▶"}</button>
          <button onClick={() => go(pos + 1)} title="Next step (→)">▶|</button>
          <button onClick={() => go(total - 1)} title="Last step">⏭</button>
          <div className="track">
            <input type="range" min={0} max={total - 1} value={pos} onChange={(e) => go(Number(e.target.value))} aria-label="Step" />
            {raises.slice(0, 200).map((r) => (
              <span key={r} className="mark" style={{ left: `${(r / Math.max(total - 1, 1)) * 100}%` }} title={`raised at step ${r + 1}`} onClick={() => go(r)} />
            ))}
          </div>
          <span className="mono muted">{pos + 1} / {total}</span>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} title="Steps per second">
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </div>
      </div>

      <aside className="panel inspector watch">
        {opened ? (
          <ValueInspector run={runId} binding={opened} onBack={() => setOpened(null)}
            onOpenCall={(id) => api.path(runId, id).then((p) => { setOpened(null); jumpToSeq(p[p.length - 1].seq); })} />
        ) : <>
        <div className="section">
          <div className="label">Step {pos + 1}</div>
          {cur ? (
            <>
              <div className={`now-line ${cur.kind}`}>{describe(cur)}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {cur.file ? `${cur.file}:${cur.kind === "raise" && cur.exception?.line ? cur.exception.line : cur.first_line}` : "library, not observed inside"}
              </div>
              {cur.kind === "call" && cur.callback && <CallbackInfo cb={cur.callback} caller={stack[stack.length - 2]?.qualname} onJump={jumpToSeq} />}
              <Values values={cur.values} onWatch={addWatch}
                onOpen={(v) => setOpened({ ...v, call_id: cur.call_id, seq: cur.seq, line: null } as unknown as Binding)} />
            </>
          ) : <div className="muted">Loading…</div>}
        </div>

        <div className="section">
          <div className="label">Watch</div>
          <form onSubmit={(e) => { e.preventDefault(); addWatch(newWatch); }} className="watch-add">
            <input value={newWatch} onChange={(e) => setNewWatch(e.target.value)} placeholder="variable name, e.g. rows" aria-label="Watch a variable" />
            <button type="submit">+</button>
          </form>
          {watches.map((name) => {
            const list = watched.get(name);
            const w = cur && list ? at(list, cur.seq) : undefined;
            const changed = w != null && w.seq > prevSeq;
            return (
              <div key={name} className={`watch-row ${changed ? "changed" : ""}`}>
                <div className="head">
                  <span className="mono">{name}</span>
                  {changed && <span className="delta">Δ now</span>}
                  <button className="x" onClick={() => setWatches(watches.filter((x) => x !== name))} title="Stop watching">✕</button>
                </div>
                {!list ? <div className="muted">…</div>
                  : !w ? <div className="muted">{list.length ? "not set yet" : "never recorded in this run"}</div>
                  : (
                    <>
                      <div className="mono v" title={w.preview}>{w.preview}</div>
                      <div className="muted where">
                        {w.kind === "arg" ? "argument of" : w.kind === "raise" ? "local where it raised in" : w.kind === "self" ? "receiver after" : "local at return of"}{" "}
                        <button className="link" onClick={() => jumpToSeq(w.seq)}>{shortName(w.qualname)}</button>
                      </div>
                    </>
                  )}
              </div>
            );
          })}
          <div className="muted hint">Recorded: arguments when a function is called, its result, the receiver after a method, and the locals where an exception is raised. Trace with <span className="mono">--level state</span> to also get every function's locals when it returns.</div>
        </div>

        <div className="section">
          <div className="label">Call stack</div>
          {[...stack].reverse().map((c, i) => (
            <button key={c.id} className={`stack-row ${i === 0 ? "top" : ""}`} onClick={() => jumpToSeq(c.seq)} title="Jump to where this call started">
              <span className="mono">{shortName(c.qualname)}</span>
              <span className="muted">{c.call_line ? `line ${c.call_line}` : ""}</span>
            </button>
          ))}
        </div>
        </>}
      </aside>
    </div>
  );
}

function Values({ values, onWatch, onOpen }: { values: StepValue[]; onWatch: (name: string) => void; onOpen: (v: StepValue) => void }) {
  if (!values.length) return null;
  return (
    <div className="step-values">
      {values.map((v, i) => (
        <div key={i} className="kvrow">
          <span className="k">{v.kind === "return" ? "→ result" : v.name}</span>
          <button className="v mono" title={`${v.type}: ${v.preview}\nclick to inspect`} onClick={() => onOpen(v)}>{v.preview}</button>
          {v.name && v.kind !== "return" && <button className="pin" onClick={() => onWatch(v.name!)} title={`Watch ${v.name}`}>👁</button>}
        </div>
      ))}
    </div>
  );
}

/** Why this call was a callback: who ran it without naming it, where the function was, where it came from. */
function CallbackInfo({ cb, caller, onJump }: { cb: Callback; caller?: string; onJump: (seq: number) => void }) {
  const who = caller ? shortName(caller) : "the caller";
  return (
    <div className="callback-box">
      <div className="label">Callback · inferred</div>
      {cb.by ? (
        <div>Called back by <span className="mono">{cb.by}</span>, library code not traced inside.</div>
      ) : (
        <>
          <div>{who} didn't call it by name: <span className="mono">{cb.line}</span>{cb.at ? <span className="muted"> (line {cb.at})</span> : null}</div>
          {cb.via && <div>{who} had it in <span className="mono">{cb.via}</span>.</div>}
          {cb.registered && (
            <div>First seen {cb.registered.kind === "return" ? "returned by" : "passed to"}{" "}
              <button className="link" onClick={() => onJump(cb.registered!.seq)}>{shortName(cb.registered.qualname)}</button>
              {cb.registered.path ? <> as <span className="mono">{cb.registered.path}</span></> : null}.
            </div>
          )}
        </>
      )}
    </div>
  );
}
