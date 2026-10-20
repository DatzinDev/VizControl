import { Background, Controls, Edge, MarkerType, Node, NodeProps, ReactFlow } from "@xyflow/react";
import { Appearance, ObjectFlow, ObjectSummary } from "./api";
import { shortName } from "./format";
import { handles } from "./nodes";

const STEP_W = 190;
const COL = 240;
const MAX_FIELDS = 6;
const LANE_PAD = 34;  // room for the lane's module label
const stepH = (a: Appearance) => 50 + Math.min(a.fields.length, MAX_FIELDS) * 17 + (a.fields.length > MAX_FIELDS ? 17 : 0);

interface StepData extends Record<string, unknown> {
  a: Appearance;
  first: boolean;
}

function StepView({ data }: NodeProps) {
  const { a, first } = data as StepData;
  const shown = a.fields.slice(0, MAX_FIELDS);
  return (
    <div className={`step ${a.changed.length ? "changed" : ""}`} title={`${a.qualname}\nclick to open the call`}>
      {handles}
      <div className="head flow">
        seq {a.seq} · {first ? "first seen" : a.role}
        {a.path && <span className="muted"> · in {a.name ?? a.role}{a.path}</span>}
      </div>
      <div className="q">{shortName(a.qualname)}</div>
      {shown.map(([k, v]) => {
        const changed = a.changed.includes(k);
        return (
          <div key={k} className={`field ${changed ? "unfinished" : ""}`}>
            <span className="d">{changed ? "Δ" : ""}</span>
            <span className="k">{k}</span>
            <span className="v">{v}</span>
          </div>
        );
      })}
      {a.fields.length > MAX_FIELDS && <div className="field muted"><span className="d" />… {a.fields.length - MAX_FIELDS} more</div>}
    </div>
  );
}

function LaneView({ data }: NodeProps) {
  return <div className="lane"><span className="mono muted">{(data as { module: string }).module}</span></div>;
}

const nodeTypes = { step: StepView, lane: LaneView };

/** Data Flow (S6): one object's appearances left → right in seq order, one lane per module. */
export function DataFlowView({ flow, onOpenCall }: { flow: ObjectFlow; onOpenCall: (id: number) => void }) {
  const steps = flow.appearances;
  const lanes: string[] = [];
  for (const a of steps) if (!lanes.includes(a.module ?? "?")) lanes.push(a.module ?? "?");
  const width = steps.length * COL + 24;
  const nodes: Node[] = [];
  let y = 0;
  const laneY = new Map<string, number>();
  for (const lane of lanes) {
    const h = Math.max(...steps.filter((a) => (a.module ?? "?") === lane).map(stepH)) + LANE_PAD + 16;
    laneY.set(lane, y);
    nodes.push({ id: `lane:${lane}`, type: "lane", position: { x: -24, y }, width, height: h, zIndex: -1,
      selectable: false, data: { module: lane } });
    y += h + 12;
  }
  steps.forEach((a, i) => nodes.push({
    id: `s${i}`,
    type: "step",
    position: { x: i * COL, y: laneY.get(a.module ?? "?")! + LANE_PAD },
    width: STEP_W,
    height: stepH(a),
    data: { a, first: i === 0 },
  }));
  const edges: Edge[] = steps.slice(1).map((_, i) => ({
    id: `e${i}`,
    source: `s${i}`,
    target: `s${i + 1}`,
    type: "smoothstep",
    style: { stroke: "var(--flow)", strokeWidth: 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--flow)", width: 14, height: 14 },
  }));
  return (
    <ReactFlow
      key={flow.object_id}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => n.type === "step" && onOpenCall((n.data as StepData).a.call_id)}
      nodesDraggable={false}
      nodesConnectable={false}
      zoomOnDoubleClick={false}
      defaultViewport={{ x: 48, y: 56, zoom: 0.9 }}  // readable from the first step; fitting a long flow shrinks the text
      minZoom={0.1}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1} color="var(--border)" />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  );
}

/** What to follow when no object is picked yet: the run's most seen objects. */
export function ObjectPicker({ objects, onFollow }: { objects: ObjectSummary[] | undefined; onFollow: (oid: number) => void }) {
  if (!objects) return <div className="empty">Loading…</div>;
  if (!objects.length) return <div className="empty">No objects captured: trace with level values or state.</div>;
  return (
    <div className="picker">
      <div className="label">Pick an object to follow</div>
      {objects.map((o) => (
        <button key={o.object_id} className="binding" onClick={() => onFollow(o.object_id)}>
          <span className="oid">#{o.object_id}</span>
          <span className="v">{o.name ?? shortName(o.type)} <span className="muted">{o.type}</span></span>
          <span className="k">{o.count} bindings</span>
        </button>
      ))}
    </div>
  );
}
