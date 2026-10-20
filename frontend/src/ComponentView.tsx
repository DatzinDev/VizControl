import { Background, Controls, Edge, Handle, MarkerType, Node, NodeProps, Position, ReactFlow } from "@xyflow/react";
import { useEffect, useState } from "react";
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
    <div className={`node ${state}`} title={`${f.qualname}\nclick to open its first call`}>
      {handles}
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

const nodeTypes = { fn: FnView, box: BoxView, neighbour: NeighbourView };

/** Level 2 (S2): the opened stage as nested module › class boxes of functions; neighbouring stages stay collapsed. */
export function ComponentView(props: {
  detail: StageDetail;
  onOpenStage: (name: string) => void;
  onOpenCall: (id: number) => void;
}) {
  const { detail, onOpenStage, onOpenCall } = props;
  const [rects, setRects] = useState<Map<string, Rect> | null>(null);

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
    .filter((s): s is string => s != null && s !== detail.stage);
  const outgoing = new Map<string, { from: number; stage: string; calls: number }>();
  for (const o of detail.outgoing) {  // one edge per (function, stage)
    const k = `${o.from}->${o.stage}`;
    outgoing.set(k, { ...o, calls: (outgoing.get(k)?.calls ?? 0) + o.calls });
  }
  const incoming = new Map<string, { stage: string; to: number; calls: number }>();
  for (const i of detail.incoming) {
    if (i.stage == null || i.stage === detail.stage) continue;
    const k = `${i.stage}->${i.to}`;
    incoming.set(k, { stage: i.stage, to: i.to, calls: (incoming.get(k)?.calls ?? 0) + i.calls });
  }
  const edgeList = [
    ...detail.edges.filter((e) => e.from !== e.to).map((e) => ({ id: `f${e.from}->f${e.to}`, source: `f${e.from}`, target: `f${e.to}`, calls: e.calls, kind: "inner" })),
    ...[...incoming.values()].map((i) => ({ id: `in:${i.stage}->f${i.to}`, source: `n:${i.stage}`, target: `f${i.to}`, calls: i.calls, kind: "in" })),
    ...[...outgoing.values()].map((o) => ({ id: `out:f${o.from}->${o.stage}`, source: `f${o.from}`, target: `n:${o.stage}`, calls: o.calls, kind: "out" })),
  ];

  useEffect(() => {
    const fn = (f: StageFunction): TreeBox => ({ id: `f${f.code_id}`, width: CALL_W, height: CALL_H });
    const tree: TreeBox[] = [
      {
        id: "stage",
        children: [...modules].map(([m, classes]) => ({
          id: `m:${m}`,
          children: [...classes].flatMap(([cls, fs]) => (cls ? [{ id: `c:${m}:${cls}`, children: fs.map(fn) }] : fs.map(fn))),
        })),
      },
      ...neighbours.map((n) => ({ id: `n:${n}`, width: NB_W, height: NB_H })),
    ];
    layoutTree(tree, edgeList).then(setRects);
  }, [detail]);

  if (!rects) return null;
  const at = (id: string, parentId?: string, extra: Partial<Node> = {}): Node => {
    const r = rects.get(id)!;
    return { id, position: { x: r.x, y: r.y }, width: r.width, height: r.height, parentId, ...extra } as Node;
  };
  const nodes: Node[] = [
    at("stage", undefined, { type: "box", selectable: false, data: { kind: "stage", label: `${detail.stage} · ${calls(detail.stats.calls)} · ${ms(detail.stats.total_ns)}` } }),
  ];
  for (const [m, classes] of modules) {
    const file = [...classes.values()][0][0].file;
    nodes.push(at(`m:${m}`, "stage", { type: "box", selectable: false, data: { kind: "module", label: file ?? m } }));
    for (const [cls, fs] of classes) {
      const parent = cls ? `c:${m}:${cls}` : `m:${m}`;
      if (cls) nodes.push(at(parent, `m:${m}`, { type: "box", selectable: false, data: { kind: "class", label: `class ${cls}` } }));
      for (const f of fs) nodes.push(at(`f${f.code_id}`, parent, { type: "fn", data: { f } }));
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
      key={detail.stage}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => {
        if (n.type === "fn") onOpenCall((n.data.f as StageFunction).first_call);
        if (n.type === "neighbour") onOpenStage(n.data.name as string);
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
