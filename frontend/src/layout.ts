import ELK from "elkjs/lib/elk.bundled.js";

const elk = new ELK();

export interface Box {
  id: string;
  width: number;
  height: number;
}

/** Layered left-to-right layout; returns top-left positions by node id. */
export async function layout(
  nodes: Box[],
  edges: { id: string; source: string; target: string }[],
  spacing = { layers: 70, nodes: 18 },
): Promise<Map<string, { x: number; y: number }>> {
  const graph = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": String(spacing.layers),
      "elk.spacing.nodeNode": String(spacing.nodes),
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    },
    children: nodes.map((n) => ({ id: n.id, width: n.width, height: n.height })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  return new Map((graph.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]));
}
