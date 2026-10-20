import { Background, Controls, Edge, Handle, MarkerType, Node, NodeProps, Position, ReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";
import { StageDetail, StageFunction } from "./api";
import { calls, ms } from "./format";
import { layoutTree, TreeBox } from "./layout";
import { CALL_H, CALL_W } from "./nodes";

const handles = (  // top to bottom, like the layout
  <>
    <Handle type="target" position={Position.Top} />
    <Handle type="source" position={Position.Bottom} />
  </>
);

const NB_W = 150;
const NB_H = 46;

type Rect = { x: number; y: number; width: number; height: number };

/** What a reader calls the function: imports and class bodies are not functions, but they did run. */
export function fnLabel(f: StageFunction): string {
  if (f.kind === "module") return "import";
  if (f.kind === "class") return "class body";
  return f.qualname.split(".").pop()!;
}

function FnView({ data }: NodeProps) {
  const f = data.f as StageFunction;
  const state = !f.observed ? "opaque" : f.raised ? "raised" : f.unfinished ? "unfinished" : "";
  return (
    <div className={`node ${state} ${data.stopped ? "stopped" : ""}`} title={`${f.qualname}\nclick to open its first call`}>
      {handles}
      {data.stopped ? <div className="stopped-badge">⚠ stopped here</div> : null}
      <div className="title">
        {f.raised > 0 && <span className="raised">⚠</span>}
        <span className="q">{fnLabel(f)}</span>
        {f.calls > 1 && <span className="count">×{f.calls}</span>}
      </div>
      <div className="meta">
        <span>{f.cls ?? f.module}</span>
        <span className="mono">{ms(f.total_ns)}</span>
      </div>
    </div>
  );
}

function BoxView({ data }: NodeProps) {
  return <div className={`box-node ${data.kind}`}><span className="mono">{data.label as string}</span></div>;
}

function NeighbourView({ data }: NodeProps) {
  return (
    <div className="neighbour" title="Open this stage">
      {handles}
      <span className="mono">{data.name as string}</span>
      <span className="muted">stage · collapsed</span>
    </div>
  );
}

function ModuleView({ data }: NodeProps) {
  const m = data as { label: string; functions: number; calls: number; total_ns: number; raised: number; stopped: boolean };
  return (
    <div className={`node module ${m.raised ? "raised" : ""} ${m.stopped ? "stopped" : ""}`} title="click to open this module">
      {handles}
      {m.stopped ? <div className="stopped-badge">⚠ stopped here</div> : null}
      <div className="title"><span className="q">{m.label}</span></div>
      <div className="meta">
        <span>{m.functions} functions · {calls(m.calls)}</span>
        <span className="mono">{ms(m.total_ns)}</span>
      </div>
    </div>
  );
}

const nodeTypes = { fn: FnView, box: BoxView, neighbour: NeighbourView, module: ModuleView };

/** More functions than this and a stage opens on its modules, each opened on a click. */
const MAX_FUNCTIONS = 40;

/** Level 2 (S2): the opened stage as nested module › class boxes of functions; neighbouring stages stay collapsed. */
export function ComponentView(props: {
  detail: StageDetail;
  onOpenStage: (name: string) => void;
  onOpenCall: (id: number) => void;
  stopped?: number;  // code id of the function where the run failed
  hidden?: Set<string>;  // neighbouring stages left out (the standard library)
}) {
  const { detail, onOpenStage, onOpenCall, stopped, hidden } = props;
  const [laid, setLaid] = useState<{ key: string; rects: Map<string, Rect> } | null>(null);
  const many = detail.functions.length > MAX_FUNCTIONS;
  const [opened, setOpened] = useState<Set<string>>(() => new Set(
    detail.functions.filter((f) => !many || f.code_id === stopped).map((f) => f.module ?? "?")));

  // the nesting: stage › module › class (if any) › function
  const modules = new Map<string, Map<string | null, StageFunction[]>>();
  for (const f of detail.functions) {
    const m = f.module ?? "?";
    if (!modules.has(m)) modules.set(m, new Map());
    const classes = modules.get(m)!;
    if (!classes.has(f.cls)) classes.set(f.cls, []);
    classes.get(f.cls)!.push(f);
  }
  const neighbours = [...new Set([...detail.incoming.map((i) => i.stage), ...detail.outgoing.map((o) => o.stage)])]
    .filter((s): s is string => s != null && s !== detail.stage && !hidden?.has(s));
  // where a function shows: its own node, or its module's when the module is closed
  const byCode = new Map(detail.functions.map((f) => [f.code_id, f]));
  const at_ = (code: number) => {
    const m = byCode.get(code)?.module ?? "?";
    return opened.has(m) ? `f${code}` : `m:${m}`;
  };
  const edgeList = useMemo(() => {
    const edges = new Map<string, { id: string; source: string; target: string; calls: number; kind: string }>();
    const add = (source: string, target: string, calls: number, kind: string) => {
      if (source === target) return;
      const id = `${source}->${target}`;
      edges.set(id, { id, source, target, kind, calls: (edges.get(id)?.calls ?? 0) + calls });
    };
    for (const e of detail.edges) add(at_(e.from), at_(e.to), e.calls, "inner");
    for (const i of detail.incoming) {
      if (i.stage != null && i.stage !== detail.stage && !hidden?.has(i.stage)) add(`n:${i.stage}`, at_(i.to), i.calls, "in");
    }
    for (const o of detail.outgoing) if (!hidden?.has(o.stage)) add(at_(o.from), `n:${o.stage}`, o.calls, "out");
    return [...edges.values()];
  }, [detail, opened, hidden]);

  const key = [...opened].sort().join("\n") + "\n" + neighbours.join("\n");
  useEffect(() => {
    const fn = (f: StageFunction): TreeBox => ({ id: `f${f.code_id}`, width: CALL_W, height: CALL_H });
    const tree: TreeBox[] = [
      {
        id: "stage",
        children: [...modules].map(([m, classes]) => opened.has(m) ? {
          id: `m:${m}`,
          children: [...classes].flatMap(([cls, fs]) => (cls ? [{ id: `c:${m}:${cls}`, children: fs.map(fn) }] : fs.map(fn))),
        } : { id: `m:${m}`, width: CALL_W, height: CALL_H }),
      },
      ...neighbours.map((n) => ({ id: `n:${n}`, width: NB_W, height: NB_H })),
    ];
    layoutTree(tree, edgeList).then((rects) => setLaid({ key, rects }));
  }, [detail, key]);

  if (laid?.key !== key) return null;
  const rects = laid.rects;
  const at = (id: string, parentId?: string, extra: Partial<Node> = {}): Node => {
    const r = rects.get(id)!;
    return { id, position: { x: r.x, y: r.y }, width: r.width, height: r.height, parentId, ...extra } as Node;
  };
  const nodes: Node[] = [
    at("stage", undefined, { type: "box", selectable: false, data: { kind: "stage", label: `${detail.stage} · ${calls(detail.stats.calls)} · ${ms(detail.stats.total_ns)}` } }),
  ];
  for (const [m, classes] of modules) {
    const file = [...classes.values()][0][0].file;
    if (!opened.has(m)) {
      const fs = [...classes.values()].flat();
      nodes.push(at(`m:${m}`, "stage", { type: "module", data: { module: m, label: file ?? m, functions: fs.length,
        calls: fs.reduce((n, f) => n + f.calls, 0), total_ns: Math.max(...fs.map((f) => f.total_ns)),
        raised: fs.reduce((n, f) => n + f.raised, 0), stopped: fs.some((f) => f.code_id === stopped) } }));
      continue;
    }
    nodes.push(at(`m:${m}`, "stage", { type: "box", selectable: false, data: { kind: "module", label: many ? `${file ?? m} · click to close` : file ?? m, module: m } }));
    for (const [cls, fs] of classes) {
      const parent = cls ? `c:${m}:${cls}` : `m:${m}`;
      if (cls) nodes.push(at(parent, `m:${m}`, { type: "box", selectable: false, data: { kind: "class", label: `class ${cls}` } }));
      for (const f of fs) nodes.push(at(`f${f.code_id}`, parent, { type: "fn", data: { f, stopped: f.code_id === stopped } }));
    }
  }
  for (const n of neighbours) nodes.push(at(`n:${n}`, undefined, { type: "neighbour", data: { name: n } }));
  const edges: Edge[] = edgeList.map((e) => {
    const color = e.kind === "in" ? "var(--accent)" : "var(--muted)";
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      zIndex: 10,  // above the module and class boxes
      label: e.calls > 1 ? `×${e.calls}` : undefined,
      style: { stroke: color, strokeWidth: e.kind === "in" ? 1.5 : 1 },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
    };
  });
  return (
    <ReactFlow
      key={`${detail.stage}\n${key}`}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => {
        if (n.type === "fn") onOpenCall((n.data.f as StageFunction).first_call);
        if (n.type === "neighbour") onOpenStage(n.data.name as string);
        const m = n.data.module as string | undefined;
        if (m !== undefined && (n.type === "module" || many)) {  // open a closed module, close an open one
          setOpened((o) => { const next = new Set(o); if (next.has(m)) next.delete(m); else next.add(m); return next; });
        }
      }}
      nodesDraggable={false}
      nodesConnectable={false}
      zoomOnDoubleClick={false}
      fitView
      fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
      minZoom={0.1}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1} color="var(--border)" />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  );
}
