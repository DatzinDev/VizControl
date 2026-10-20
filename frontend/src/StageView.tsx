import { Background, Controls, Edge, MarkerType, Node, ReactFlow } from "@xyflow/react";
import { useEffect, useState } from "react";
import { StageGraph } from "./api";
import { layout } from "./layout";
import { STAGE_H, STAGE_W, StageNodeView } from "./nodes";

const nodeTypes = { stage: StageNodeView };

/** Level 1: stages (inferred from module paths) and the calls between them; the main path in accent. */
export function StageView(props: {
  graph: StageGraph;
  selected: string | null;
  onSelect: (name: string) => void;
  onOpen: (name: string) => void;
}) {
  const { graph, selected, onSelect, onOpen } = props;
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }> | null>(null);

  useEffect(() => {
    layout(
      graph.nodes.map((n) => ({ id: n.name, width: STAGE_W, height: STAGE_H })),
      graph.edges.map((e) => ({ id: `${e.from}->${e.to}`, source: e.from, target: e.to })),
      { layers: 44, nodes: 28 },
    ).then(setPositions);
  }, [graph]);

  if (!positions) return null;
  const onMain = (a: string, b: string) => {
    const i = graph.main_path.indexOf(a);
    return i >= 0 && graph.main_path[i + 1] === b;
  };
  const opaque = new Set(graph.nodes.filter((n) => !n.observed).map((n) => n.name));
  const nodes: Node[] = graph.nodes.map((n) => ({
    id: n.name,
    type: "stage",
    position: positions.get(n.name) ?? { x: 0, y: 0 },
    // fixed size: without it React Flow hides re-created nodes until measured, and a double-click falls through
    width: STAGE_W,
    height: STAGE_H,
    data: { stage: n, main: graph.main_path.includes(n.name), selected: selected === n.name },
  }));
  const edges: Edge[] = graph.edges.map((e) => {
    const main = onMain(e.from, e.to);
    const color = main ? "var(--accent)" : opaque.has(e.to) ? "var(--opaque)" : "var(--muted)";
    return {
      id: `${e.from}->${e.to}`,
      source: e.from,
      target: e.to,
      label: String(e.calls),
      type: "smoothstep",
      style: { stroke: color, strokeWidth: main ? 2 : 1, strokeDasharray: opaque.has(e.to) ? "5 4" : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
    };
  });
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => onSelect(n.id)}
      onNodeDoubleClick={(_, n) => onOpen(n.id)}
      nodesDraggable={false}
      nodesConnectable={false}
      zoomOnDoubleClick={false}
      fitView
      fitViewOptions={{ padding: 0.15, maxZoom: 1.4 }}
      minZoom={0.1}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1} color="var(--border)" />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  );
}
