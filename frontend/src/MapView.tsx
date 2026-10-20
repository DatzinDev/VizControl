import { RouteMap, RunSummary, StageNode } from "./api";
import { Ghosts } from "./StageView";
import { calls, ms, outcomeIcon, runLabel } from "./format";
import { KV, Section } from "./Inspector";

type Open = (run: string, stage: string) => void;

/** The run a failed run is shown against: the latest ok run of the same target, else the ok run whose route shares
 * the most stages with it (another test, another request to the same code). */
export function referenceFor(run: RunSummary, runs: RunSummary[], map: RouteMap): string | null {
  const ok = runs.filter((r) => r.id !== run.id && r.outcome === "ok" && !r.error);
  const same = ok.filter((r) => r.target === run.target).sort((a, b) => b.started.localeCompare(a.started));
  if (same.length) return same[0].id;
  const mine = new Set(map.routes.find((r) => r.run === run.id)?.stages ?? []);
  const shared = (id: string) => (map.routes.find((r) => r.run === id)?.stages ?? []).filter((s) => mine.has(s)).length;
  const best = ok.map((r) => [shared(r.id), r] as const).filter(([n]) => n > 0).sort((a, b) => b[0] - a[0] || b[1].started.localeCompare(a[1].started));
  return best.length ? best[0][1].id : null;
}

/** What `ref` reached that `run` didn't, from the map: those stages and the edges into them. */
export function ghostsOf(run: string, ref: string, map: RouteMap, label: string): Ghosts | undefined {
  const mine = new Set(map.routes.find((r) => r.run === run)?.stages ?? []);
  const theirs = map.routes.find((r) => r.run === ref);
  if (!theirs) return undefined;
  const missing = new Set(theirs.stages.filter((s) => !mine.has(s)));
  if (!missing.size) return undefined;
  return {
    nodes: map.nodes.filter((n) => missing.has(n.name)).map((n) => ({ ...n, runs: undefined })),
    edges: theirs.edges.filter(([a, b]) => missing.has(a) || missing.has(b)),
    seenIn: label,
  };
}

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
