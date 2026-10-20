import ELK from "elkjs/lib/elk-api.js";
import workerUrl from "elkjs/lib/elk-worker.min.js?url";

// elk runs in a web worker: its 1.5 MB stays out of the page bundle and layout doesn't block the page
const elk = new ELK({ workerUrl });

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

export interface TreeBox {
  id: string;
  width?: number;  // leaves only: compound boxes are sized by their children
  height?: number;
  children?: TreeBox[];
}

/** Layered layout of nested boxes (module › function). Edges may cross levels.
 *  Positions are relative to the parent box, as React Flow sub-flows expect; compound boxes also get their size. */
export async function layoutTree(
  roots: TreeBox[],
  edges: { id: string; source: string; target: string }[],
  direction: "DOWN" | "RIGHT" = "DOWN",
): Promise<Map<string, { x: number; y: number; width: number; height: number }>> {
  // a compound box must not carry width/height keys at all: elk fails on `undefined` sizes
  const toElk = (b: TreeBox): object =>
    b.children
      ? { id: b.id, children: b.children.map(toElk), layoutOptions: {
        "elk.padding": "[top=34,left=14,bottom=14,right=14]",
        // spacing is not inherited: without it, calls inside one box sit a few pixels apart and their arrows vanish
        "elk.layered.spacing.nodeNodeBetweenLayers": "56", "elk.spacing.nodeNode": "22" } }
      : { id: b.id, width: b.width, height: b.height };
  const graph = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": direction,
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.layered.spacing.nodeNodeBetweenLayers": "56",
      "elk.spacing.nodeNode": "22",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    },
    children: roots.map(toElk),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  } as Parameters<typeof elk.layout>[0]);
  const out = new Map<string, { x: number; y: number; width: number; height: number }>();
  const walk = (ns: typeof graph.children) =>
    (ns ?? []).forEach((n) => {
      out.set(n.id, { x: n.x ?? 0, y: n.y ?? 0, width: n.width ?? 0, height: n.height ?? 0 });
      walk(n.children);
    });
  walk(graph.children);
  return out;
}
