import { useEffect, useState } from "react";
import { api, RunSummary } from "./api";
import { ms, outcomeIcon, runLabel } from "./format";
import { ReplayView } from "./ReplayView";

/** The URL keeps the run and the step: a link opens the same moment of the same run. */
function readHash(): { run: string | null; step: number | null } {
  const p = new URLSearchParams(location.hash.slice(1));
  return { run: p.get("run"), step: p.get("step") ? Number(p.get("step")) : null };
}

export function App() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [runId, setRunId] = useState<string | null>(() => readHash().run);
  const [start, setStart] = useState<{ step?: number; endOf?: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    api.runs().then((list) => {
      setRuns(list);
      if (!runId && list.length) setRunId(list[list.length - 1].id);
    }).catch((e) => setProblem(String(e)));
  }, []);

  // where the recording opens: the step in the URL, else where a failed run raised (FR-VIZ-8), else the start
  useEffect(() => {
    if (!runId) return;
    setStart(null);
    const wanted = readHash();
    if (wanted.run === runId && wanted.step != null) {
      setStart({ step: wanted.step - 1 });
      return;
    }
    let live = true;
    api.error(runId).then((err) => live && setStart(err ? { endOf: err.origin.id } : {}))
      .catch(() => live && setStart({}));
    return () => {
      live = false;
    };
  }, [runId]);

  const run = runs?.find((r) => r.id === runId);
  const onStep = (i: number) => history.replaceState(null, "", `#${new URLSearchParams({ run: runId!, step: String(i + 1) })}`);

  return (
    <div className="app">
      <header className="topbar">
        <div className="logo"><b>◉</b>VizControl</div>
        {run && (
          <div className="chip outline">
            <span className={run.outcome} style={{ fontSize: 10 }}>●</span>
            <span className="mono">{runLabel(run)}</span>
            <span className="muted">{run.outcome} · {ms(run.duration_ns)}</span>
          </div>
        )}
      </header>

      <div className="main">
        <aside className="panel">
          <div className="label">Runs</div>
          {runs?.map((r) => (
            <button key={r.id} className={`run-row ${r.id === runId ? "active" : ""}`} onClick={() => setRunId(r.id)} title={r.id}>
              <span className={r.error ? "muted" : r.outcome} style={{ fontSize: 10 }}>{r.error ? "?" : outcomeIcon[r.outcome]}</span>
              <span className="name">{runLabel(r)}</span>
              {r.http_status && <span className={Number(r.http_status) >= 500 ? "raised mono" : "muted mono"} style={{ fontSize: 11 }}>{r.http_status}</span>}
              <span className="muted" style={{ fontSize: 11 }}>{ms(r.duration_ns)}</span>
            </button>
          ))}
        </aside>

        {problem ? <section className="canvas"><div className="empty raised">{problem}</div></section>
          : runs && !runs.length ? (
            <section className="canvas">
              <div className="empty">
                <div className="first-run">
                  <div className="title-lg">No runs here yet</div>
                  <p className="muted">Trace your program, then reload this page:</p>
                  <pre className="mono">{"vizcontrol run app.py           # a script\nvizcontrol run -m package.cli   # a module\npytest --vizcontrol             # one run per test"}</pre>
                  <p className="muted">Runs are written to <span className="mono">.vizcontrol/runs</span> of the directory you run them from.</p>
                </div>
              </div>
            </section>
          )
          : runId && start ? <ReplayView key={runId} runId={runId} start={start} onStep={onStep} />
          : <section className="canvas"><div className="empty">Loading…</div></section>}
      </div>
    </div>
  );
}
