import { Handle, NodeProps, Position } from "@xyflow/react";
import { CallNode, GroupNode, StageNode } from "./api";
import { ms, outcomeIcon, shortName } from "./format";

export const CALL_W = 220;
export const CALL_H = 58;
export const MORE_H = 30;
export const STAGE_W = 150;
export const STAGE_H = 92;

export const handles = (
  <>
    <Handle type="target" position={Position.Left} />
    <Handle type="source" position={Position.Right} />
  </>
);

export interface TreeNodeData extends Record<string, unknown> {
  item: CallNode | GroupNode | { type: "more"; remaining: number };
  selected: boolean;
  onPath: boolean;
  dim: boolean;
  expanded: boolean;
  onToggle: () => void;
}

function stateClass(item: CallNode | GroupNode): string {
  if (!item.observed) return "opaque";
  if (item.type === "group") return item.outcomes.raised ? "raised" : item.outcomes.unfinished ? "unfinished" : "ok";
  return item.outcome === "running" ? "unfinished" : item.outcome;
}

export function CallNodeView({ data }: NodeProps) {
  const { item, selected, onPath, dim, expanded, onToggle } = data as TreeNodeData;
  if (item.type === "more") {
    return (
      <div className="node more">
        {handles}+{item.remaining} more
      </div>
    );
  }
  const state = stateClass(item);
  const isGroup = item.type === "group";
  const expandable = isGroup || item.children > 0;
  const dur = isGroup ? item.total_ns : item.duration_ns;
  return (
    <div
      className={["node", state !== "ok" && state, isGroup && "group", selected && "selected", dim && "dim", onPath && "raised"].filter(Boolean).join(" ")}
      title={`${item.qualname}\n${item.module ?? ""}`}
    >
      {handles}
      <div className="title">
        {state !== "ok" && <span className={state === "opaque" ? "muted" : state}>{state === "opaque" ? "▒" : outcomeIcon[state]}</span>}
        <span className="q">{shortName(item.qualname)}</span>
        {isGroup && <span className="count">×{item.count}</span>}
      </div>
      <div className="meta">
        <span>{isGroup ? `${item.outcomes.ok} ok${item.outcomes.raised ? ` · ${item.outcomes.raised} raised` : ""}` : item.module}</span>
        <span className={`mono ${state === "raised" ? "raised" : ""}`}>{ms(dur, !isGroup && !item.end_exact)}</span>
        {!isGroup && item.children > 0 && <span className="mono" style={{ color: "var(--accent)" }}>+{item.children}</span>}
      </div>
      {expandable && (
        <button
          className="expand nodrag"
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? "‹" : "›"}
        </button>
      )}
    </div>
  );
}

export interface StageNodeData extends Record<string, unknown> {
  stage: StageNode;
  main: boolean;
  selected: boolean;
}

export function StageNodeView({ data }: NodeProps) {
  const { stage, main, selected } = data as StageNodeData;
  const raised = stage.raised > 0;
  return (
    <div
      className={["stage", main && "main", !stage.observed && "opaque", raised && "raised", selected && "selected", !main && "dim"].filter(Boolean).join(" ")}
      title={stage.modules.join("\n")}
    >
      {handles}
      <div className={`strip ${!stage.observed ? "opaque" : raised ? "raised" : ""}`} />
      <div className="name">{stage.name}</div>
      <div className="muted" style={{ fontSize: 11 }}>
        {stage.calls} calls{raised ? <span className="raised"> · {stage.raised} raised</span> : null}
      </div>
      <div className="mono muted" style={{ fontSize: 11 }}>{ms(stage.total_ns)}</div>
    </div>
  );
}
