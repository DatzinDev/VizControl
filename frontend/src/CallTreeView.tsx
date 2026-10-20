import { Background, Controls, Edge, MarkerType, Node, ReactFlow, useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { CallNode } from "./api";
import { layout } from "./layout";
import { CALL_H, CALL_W, CallNodeView, MORE_H } from "./nodes";
import { Item, Key, useCallTree, Visible } from "./useCallTree";

const nodeTypes = { call: CallNodeView };

/** Levels 2–3: the call tree, expanded on demand. Children sit to the right of their caller. */
export function CallTreeView(props: {
  tree: ReturnType<typeof useCallTree>;
  selected: Key | null;
  errorPath: Set<Key>;
  focusError: boolean;
  hide: (item: Item) => boolean;
  centerOn: Key | null;
  onSelect: (key: Key) => void;
  cut: (call: CallNode) => string | null;
}) {
  const { tree, selected, errorPath, focusError, hide, centerOn, onSelect, cut } = props;
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());
  const flow = useReactFlow();
  const fitted = useRef(false);

  // Filtered calls (and everything expanded under them) fold into one "hidden (N)" stub per parent. The selection
  // and the error path are never hidden.
  const shown = useMemo(() => {
    const out: Visible[] = [];
    const gone = new Set<Key>();
    const stubs = new Map<Key | null, number>();
    for (const v of tree.visible) {
      if (v.parent && gone.has(v.parent)) {
        gone.add(v.key);
      } else if (v.key !== selected && !errorPath.has(v.key) && hide(v.item)) {
        gone.add(v.key);
        stubs.set(v.parent, (stubs.get(v.parent) ?? 0) + 1);
      } else {
        out.push(v);
      }
    }
    for (const [parent, count] of stubs) out.push({ key: `h${parent ?? "root"}`, parent, item: { type: "hidden", count } });
    return out;
  }, [tree.visible, hide, selected, errorPath]);
  const shape = shown.map((v) => v.key).join(",");

  useEffect(() => {
    const keys = new Set(shown.map((v) => v.key));
    layout(
      shown.map((v) => ({ id: v.key, width: CALL_W, height: v.item.type === "more" || v.item.type === "hidden" ? MORE_H : CALL_H })),
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
      height: v.item.type === "more" || v.item.type === "hidden" ? MORE_H : CALL_H,
      data: {
        item: v.item,
        selected: v.key === selected,
        onPath: errorPath.has(v.key),
        dim: focusError && errorPath.size > 0 && !errorPath.has(v.key) && v.key !== selected,
        expanded: tree.isExpanded(v.key),
        onToggle: () => tree.toggle(v.key),
        cut: v.item.type === "call" ? cut(v.item) : null,
      },
    }));
  const edges: Edge[] = shown
    .filter((v) => v.parent && positions.has(v.parent) && positions.has(v.key))
    .map((v) => {
      const err = errorPath.has(v.parent!) && errorPath.has(v.key);
      const opaque = v.item.type !== "more" && v.item.type !== "hidden" && !v.item.observed;
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
        else if (item?.type !== "hidden") onSelect(n.id);
      }}
      onNodeDoubleClick={(_, n) => !n.id.startsWith("h") && tree.toggle(n.id)}
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
