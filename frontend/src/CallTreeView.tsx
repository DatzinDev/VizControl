import { Background, Controls, Edge, MarkerType, Node, ReactFlow, useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { layout } from "./layout";
import { CALL_H, CALL_W, CallNodeView, MORE_H } from "./nodes";
import { Key, useCallTree } from "./useCallTree";

const nodeTypes = { call: CallNodeView };

/** Levels 2–3: the call tree, expanded on demand. Children sit to the right of their caller. */
export function CallTreeView(props: {
  tree: ReturnType<typeof useCallTree>;
  selected: Key | null;
  errorPath: Set<Key>;
  focusError: boolean;
  hideOpaqueLeaves: boolean;
  centerOn: Key | null;
  onSelect: (key: Key) => void;
}) {
  const { tree, selected, errorPath, focusError, hideOpaqueLeaves, centerOn, onSelect } = props;
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());
  const flow = useReactFlow();
  const fitted = useRef(false);

  const shown = useMemo(
    () =>
      tree.visible.filter(
        ({ item }) => !(hideOpaqueLeaves && item.type === "call" && !item.observed && item.children === 0),
      ),
    [tree.visible, hideOpaqueLeaves],
  );
  const shape = shown.map((v) => v.key).join(",");

  useEffect(() => {
    const keys = new Set(shown.map((v) => v.key));
    layout(
      shown.map((v) => ({ id: v.key, width: CALL_W, height: v.item.type === "more" ? MORE_H : CALL_H })),
      shown.filter((v) => v.parent && keys.has(v.parent)).map((v) => ({ id: `${v.parent}>${v.key}`, source: v.parent!, target: v.key })),
    ).then(setPositions);
  }, [shape]);

  useEffect(() => {
    if (!positions.size) return;
    const p = centerOn && positions.get(centerOn);
    if (p && errorPath.has(centerOn!)) {
      // a failure: frame the last steps of the error path, not just the origin
      const tail = shown.filter((v) => errorPath.has(v.key) && positions.has(v.key)).slice(-3).map((v) => positions.get(v.key)!);
      const x = Math.min(...tail.map((t) => t.x)), y = Math.min(...tail.map((t) => t.y));
      const w = Math.max(...tail.map((t) => t.x)) + CALL_W - x, h = Math.max(...tail.map((t) => t.y)) + CALL_H - y;
      flow.fitBounds({ x, y, width: w, height: h }, { padding: 0.12, duration: 400 });
    } else if (p) {
      flow.setCenter(p.x + CALL_W / 2, p.y + CALL_H / 2, { zoom: Math.max(flow.getZoom(), 0.9), duration: 400 });
    } else if (!fitted.current) {
      flow.fitView({ padding: 0.2, maxZoom: 1 });
    }
    fitted.current = true;
  }, [positions, centerOn]);

  const nodes: Node[] = shown
    .filter((v) => positions.has(v.key))
    .map((v) => ({
      id: v.key,
      type: "call",
      position: positions.get(v.key)!,
      width: CALL_W,  // fixed size: see StageView
      height: v.item.type === "more" ? MORE_H : CALL_H,
      data: {
        item: v.item,
        selected: v.key === selected,
        onPath: errorPath.has(v.key),
        dim: focusError && errorPath.size > 0 && !errorPath.has(v.key) && v.key !== selected,
        expanded: tree.isExpanded(v.key),
        onToggle: () => tree.toggle(v.key),
      },
    }));
  const edges: Edge[] = shown
    .filter((v) => v.parent && positions.has(v.parent) && positions.has(v.key))
    .map((v) => {
      const err = errorPath.has(v.parent!) && errorPath.has(v.key);
      const opaque = v.item.type !== "more" && !v.item.observed;
      const color = err ? "var(--raised)" : opaque ? "var(--opaque)" : "var(--muted)";
      return {
        id: `${v.parent}>${v.key}`,
        source: v.parent!,
        target: v.key,
        type: "smoothstep",
        style: { stroke: color, strokeWidth: err ? 2 : 1, strokeDasharray: opaque || v.item.type === "group" ? "5 4" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
      };
    });

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => {
        const item = shown.find((v) => v.key === n.id)?.item;
        if (item?.type === "more") tree.loadMore(n.id);
        else onSelect(n.id);
      }}
      onNodeDoubleClick={(_, n) => tree.toggle(n.id)}
      nodesDraggable={false}
      nodesConnectable={false}
      zoomOnDoubleClick={false}
      minZoom={0.05}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1} color="var(--border)" />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  );
}
