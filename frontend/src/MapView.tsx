import { RouteMap, RunSummary, StageNode } from "./api";
import { calls, ms, outcomeIcon, runLabel } from "./format";
import { KV, Section } from "./Inspector";

type Open = (run: string, stage: string) => void;

function RunLink({ run, onOpen, stage }: { run?: RunSummary; onOpen: Open; stage: string }) {
  if (!run) return null;
  return (
    <button className="binding" onClick={() => onOpen(run.id, stage)} title={`Open ${stage} in ${run.id}`}>
      <span className={run.outcome}>{outcomeIcon[run.outcome]}</span>
      <span className="v">{runLabel(run)}</span>
      <span className="k">{ms(run.duration_ns)}</span>
    </button>
  );
}

/** The route map: which runs went through a stage, and which never reached it. */
export function MapInspector({ map, runs, current, selected, onOpen }: {
  map: RouteMap;
  runs: RunSummary[];
  current: string | null;
  selected: StageNode | null;
  onOpen: Open;
}) {
  const byId = new Map(runs.map((r) => [r.id, r]));
  const route = map.routes.find((r) => r.run === current);
  if (!selected) {
    return (
      <>
        <div className="title-lg">Route map</div>
        <Section title="All runs">
          <KV rows={[["runs", String(map.routes.length)], ["stages", String(map.nodes.length)],
            ["raised", String(map.routes.filter((r) => r.outcome === "raised").length)]]} />
        </Section>
        {route && (
          <Section title="Route in accent">
            <div className="mono" style={{ fontSize: 12 }}>{runLabel(byId.get(route.run) ?? { target: route.run })}</div>
            <div className="muted" style={{ fontSize: 12 }}>{route.stages.length} of {map.nodes.length} stages · pick another run on the left</div>
          </Section>
        )}
        <div className="box dashed muted" style={{ fontSize: 12 }}>
          ┄ Every stage and call between stages that any served run went through, with how many runs did. Stages are
          inferred the same way for all runs. Click a stage to see which runs reached it.
        </div>
      </>
    );
  }
  const passed = new Set(selected.runs ?? []);
  const missed = map.routes.filter((r) => !passed.has(r.run));
  return (
    <>
      <div className="title-lg">{selected.name}</div>
      <Section title="Stage">
        <KV rows={[["runs", `${passed.size} of ${map.routes.length}`], ["calls", calls(selected.calls)], ["total", ms(selected.total_ns)],
          ["raised", String(selected.raised)]]} />
      </Section>
      <Section title={`Went through it · ${passed.size}`}>
        {[...passed].map((id) => <RunLink key={id} run={byId.get(id)} onOpen={onOpen} stage={selected.name} />)}
      </Section>
      {missed.length > 0 && (
        <Section title={`Never reached it · ${missed.length}`}>
          {missed.map((r) => <div key={r.run} className="mono muted" style={{ fontSize: 12 }}>{outcomeIcon[r.outcome ?? ""] ?? "?"} {runLabel(byId.get(r.run) ?? { target: r.run })}</div>)}
        </Section>
      )}
    </>
  );
}
