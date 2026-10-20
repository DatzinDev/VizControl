import { Background, Controls, Edge, MarkerType, Node, ReactFlow } from "@xyflow/react";
import { useEffect, useState } from "react";
import { Route, StageGraph, StageNode } from "./api";

/** Stages another run reached and this one didn't, with the edges that lead to them: evidence from that run. */
export interface Ghosts {
  nodes: StageNode[];
  edges: [string, string][];
  seenIn: string;
}
import { layout } from "./layout";
import { STAGE_H, STAGE_W, StageNodeView } from "./nodes";

const nodeTypes = { stage: StageNodeView };

/** Level 1: stages (inferred) and the calls between them; the main path in accent, or a run's route on the map. */
export function StageView(props: {
  graph: StageGraph;
  selected: string | null;
  onSelect: (name: string) => void;
  onOpen: (name: string) => void;
  route?: Route;
  stopped?: string;  // the stage where the run failed
  ghosts?: Ghosts;  // stages another run reached and this one didn't
}) {
  const { graph, selected, onSelect, onOpen, route, stopped, ghosts } = props;
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }> | null>(null);

  const ghostEdges = ghosts?.edges ?? [];
  useEffect(() => {
    layout(
      [...graph.nodes, ...(ghosts?.nodes ?? [])].map((n) => ({ id: n.name, width: STAGE_W, height: STAGE_H })),
      [...graph.edges.map((e) => [e.from, e.to]), ...ghostEdges].map(([a, b]) => ({ id: `${a}->${b}`, source: a, target: b })),
      { layers: 44, nodes: 28 },
    ).then(setPositions);
  }, [graph, ghosts]);

  if (!positions) return null;
  const onMain = (a: string, b: string) => {
    if (route) return route.edges.some(([x, y]) => x === a && y === b);
    const i = graph.main_path.indexOf(a);
    return i >= 0 && graph.main_path[i + 1] === b;
  };
  const isMain = (name: string) => (route ? route.stages : graph.main_path).includes(name);
  const opaque = new Set(graph.nodes.filter((n) => !n.observed).map((n) => n.name));
  const nodes: Node[] = graph.nodes.map((n) => ({
    id: n.name,
    type: "stage",
    position: positions.get(n.name) ?? { x: 0, y: 0 },
    // fixed size: without it React Flow hides re-created nodes until measured, and a double-click falls through
    width: STAGE_W,
    height: STAGE_H,
    data: { stage: n, main: isMain(n.name), selected: selected === n.name, stopped: stopped === n.name },
  }));
  for (const n of ghosts?.nodes ?? []) {
    nodes.push({ id: n.name, type: "stage", position: positions.get(n.name) ?? { x: 0, y: 0 }, width: STAGE_W, height: STAGE_H,
      data: { stage: n, main: false, selected: selected === n.name, ghost: ghosts!.seenIn } });
  }
  const edges: Edge[] = graph.edges.map((e) => {
    const main = onMain(e.from, e.to);
    const color = main ? "var(--accent)" : opaque.has(e.to) ? "var(--opaque)" : "var(--muted)";
    return {
      id: `${e.from}->${e.to}`,
      source: e.from,
      target: e.to,
      label: e.runs ? `${e.runs.length} run${e.runs.length === 1 ? "" : "s"}` : String(e.calls),
      type: "smoothstep",
      style: { stroke: color, strokeWidth: main ? 2 : 1, strokeDasharray: opaque.has(e.to) ? "5 4" : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
    };
  });
  for (const [a, b] of ghostEdges) {
    edges.push({ id: `${a}->${b}`, source: a, target: b, type: "smoothstep",
      style: { stroke: "var(--muted)", strokeWidth: 1, strokeDasharray: "2 4", opacity: 0.6 } });
  }
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
