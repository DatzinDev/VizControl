import { ReactFlowProvider } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Binding, CallNode, ErrorPath, RunSummary, StageGraph } from "./api";
import { CallTreeView } from "./CallTreeView";
import { ms, outcomeIcon, runLabel, shortName } from "./format";
import { CallInspector, RunInspector, StageInspector, ValueInspector } from "./Inspector";
import { SearchPalette } from "./SearchPalette";
import { StageView } from "./StageView";
import { callKey, Key, useCallTree } from "./useCallTree";

type View = "stages" | "calls";
type Selection = { kind: "stage"; name: string } | { kind: "call"; id: number } | null;

function readHash(): { run: string | null; call: number | null } {
  const p = new URLSearchParams(location.hash.slice(1));
  return { run: p.get("run"), call: p.get("call") ? Number(p.get("call")) : null };
}

export function App() {
  const initial = useMemo(readHash, []);
  const pendingCall = useRef(initial.call);  // the URL's call, opened once its run has loaded
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string | null>(initial.run);
  const [view, setView] = useState<View>("stages");
  const [stages, setStages] = useState<StageGraph | null>(null);
  const [error, setError] = useState<ErrorPath | null | undefined>(undefined);
  const [selection, setSelection] = useState<Selection>(null);
  const [value, setValue] = useState<Binding | null>(null);
  const [centerOn, setCenterOn] = useState<Key | null>(null);
  const [searching, setSearching] = useState(false);
  const [focusError, setFocusError] = useState(true);
  const [hideOpaque, setHideOpaque] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const tree = useCallTree(runId);
  const run = runs.find((r) => r.id === runId);

  useEffect(() => {
    api.runs().then((list) => {
      setRuns(list);
      if (!runId && list.length) setRunId(list[list.length - 1].id);
    }).catch((e) => setProblem(String(e)));
  }, []);

  const openCall = useCallback(
    async (id: number) => {
      setView("calls");
      setValue(null);
      setSelection({ kind: "call", id });
      const key = await tree.reveal(id);
      setCenterOn(key);
    },
    [tree.reveal],
  );

  // A run opens on its failure if it has one (FR-VIZ-8), else on the stage overview.
  useEffect(() => {
    if (!runId) return;
    tree.reset();
    setStages(null);
    setError(undefined);
    setSelection(null);
    setValue(null);
    setCenterOn(null);
    let live = true;
    Promise.all([api.stages(runId), api.error(runId)]).then(([g, err]) => {
      if (!live) return;
      setStages(g);
      setError(err);
      const pending = runId === initial.run ? pendingCall.current : null;
      pendingCall.current = null;
      if (pending) openCall(pending);
      else if (err) openCall(err.origin.id);
      else setView("stages");
    }).catch((e) => setProblem(String(e)));
    return () => {
      live = false;
    };
  }, [runId]);

  useEffect(() => {
    const p = new URLSearchParams();
    if (runId) p.set("run", runId);
    if (selection?.kind === "call") p.set("call", String(selection.id));
    history.replaceState(null, "", `#${p}`);
  }, [runId, selection]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearching(true);
      }
      if (e.key === "Escape" && !searching) {
        if (value) setValue(null);
        else if (view === "calls") setView("stages");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searching, value, view]);

  const errorPath = useMemo(() => new Set<Key>(error ? error.path.map((c) => callKey(c.id)) : []), [error]);
  const selectedCall: CallNode | undefined =
    selection?.kind === "call" ? tree.callItem(callKey(selection.id)) ?? error?.path.find((c) => c.id === selection.id) : undefined;
  const isFailureView = view === "calls" && selection?.kind === "call" && error?.origin.id === selection.id;
  const level = value ? "L4 · Value" : view === "stages" ? "L1 · Overview" : isFailureView ? "L3 · Failure" : selection ? "L3 · Function" : "L2 · Calls";

  const crumbs: { label: string; onClick?: () => void }[] = [{ label: "Run", onClick: () => { setView("stages"); setSelection(null); setValue(null); } }];
  if (run) crumbs.push({ label: runLabel(run), onClick: () => { setView("stages"); setSelection(null); setValue(null); } });
  if (selection?.kind === "stage") crumbs.push({ label: selection.name });
  if (selectedCall) {
    if (selectedCall.stage) crumbs.push({ label: selectedCall.stage });
    crumbs.push({ label: shortName(selectedCall.qualname), onClick: () => setValue(null) });
  }
  if (value) crumbs.push({ label: `${value.name ?? value.kind}${value.object_id != null ? ` #${value.object_id}` : ""}` });

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
        <div className="tabs" role="tablist">
          <button className="active">Execution Flow</button>
          <button disabled title="Coming next: object lifecycles across calls">Data Flow</button>
          <button disabled title="Coming next: compare two runs">Diff</button>
        </div>
        <div className="spacer" />
        <button className="chip search-btn" onClick={() => runId && setSearching(true)}>
          <span>⌕ Search calls, values, modules…</span>
          <span className="mono" style={{ fontSize: 11 }}>⌘K</span>
        </button>
      </header>

      <nav className="crumbs">
        {crumbs.map((c, i) => (
          <span key={i} style={{ display: "contents" }}>
            {i > 0 && <span className="muted">›</span>}
            <button className={i === crumbs.length - 1 ? "current" : ""} onClick={c.onClick} disabled={!c.onClick}>{c.label}</button>
          </span>
        ))}
        <span className="spacer" />
        <span className="level">{level}</span>
      </nav>

      <div className="main">
        <aside className="panel">
          <div className="label">Runs</div>
          {runs.map((r) => (
            <button key={r.id} className={`run-row ${r.id === runId ? "active" : ""}`} onClick={() => setRunId(r.id)} title={r.id}>
              <span className={r.error ? "muted" : r.outcome} style={{ fontSize: 10 }}>{r.error ? "?" : outcomeIcon[r.outcome]}</span>
              <span className="name">{runLabel(r)}</span>
              <span className="muted" style={{ fontSize: 11 }}>{ms(r.duration_ns)}</span>
            </button>
          ))}
          {!runs.length && <div className="muted">No runs. Try <span className="mono">vizcontrol run app.py</span></div>}
          {stages && (
            <>
              <div className="label" style={{ marginTop: 8 }}>Stages</div>
              {stages.nodes.map((s) => (
                <button key={s.name} className="stage-row" onClick={() => openCall(s.first_call)} title="Open the first call of this stage">
                  <span className={s.raised ? "raised" : s.observed ? "ok" : "muted"}>{s.raised ? "⚠" : s.observed ? "●" : "▒"}</span>
                  <span style={{ flex: 1 }}>{s.name}</span>
                  <span className="muted">{s.calls}</span>
                </button>
              ))}
            </>
          )}
          <div className="label" style={{ marginTop: 8 }}>Filters</div>
          {error && (
            <label className="toggle"><input type="checkbox" checked={focusError} onChange={(e) => setFocusError(e.target.checked)} />Dim calls off the error path</label>
          )}
          <label className="toggle"><input type="checkbox" checked={hideOpaque} onChange={(e) => setHideOpaque(e.target.checked)} />Hide library calls without callbacks</label>
        </aside>

        <section className="canvas">
          {problem && <div className="empty raised">{problem}</div>}
          {!problem && view === "stages" && stages && (
            <>
              <div className="hint">Main path in accent · double-click a stage to open its calls</div>
              <StageView
                graph={stages}
                selected={selection?.kind === "stage" ? selection.name : null}
                onSelect={(name) => { setSelection({ kind: "stage", name }); setValue(null); }}
                onOpen={(name) => { const s = stages.nodes.find((n) => n.name === name); if (s) openCall(s.first_call); }}
              />
            </>
          )}
          {!problem && view === "calls" && (
            <>
              {error ? (
                <div className="banner">⚠ Run failed · {error.exception.type} in {shortName(error.origin.qualname)} · error path in red</div>
              ) : (
                <div className="hint">Click a call to inspect it · › expands · Esc back to stages</div>
              )}
              <ReactFlowProvider>
                <CallTreeView
                  tree={tree}
                  selected={selection?.kind === "call" ? callKey(selection.id) : null}
                  errorPath={errorPath}
                  focusError={focusError}
                  hideOpaqueLeaves={hideOpaque}
                  centerOn={centerOn}
                  onSelect={(key) => { if (key.startsWith("c")) { setSelection({ kind: "call", id: Number(key.slice(1)) }); setValue(null); } else tree.toggle(key); }}
                />
              </ReactFlowProvider>
            </>
          )}
          <div className="legend">
            <span><span className="ok">●</span> ok</span>
            <span><span className="raised">⚠</span> raised</span>
            <span><span className="unfinished">◌</span> unfinished</span>
            <span>▒ not observed</span>
            <span>┄ inferred</span>
          </div>
        </section>

        <aside className="panel inspector">
          {runId && value && <ValueInspector run={runId} binding={value} onBack={() => setValue(null)} onOpenCall={openCall} />}
          {runId && !value && selection?.kind === "call" && (
            <CallInspector run={runId} id={selection.id} error={error} onOpenCall={openCall} onOpenValue={setValue} />
          )}
          {!value && selection?.kind === "stage" && stages && (
            <StageInspector stage={stages.nodes.find((n) => n.name === selection.name)!} onOpenCall={openCall} />
          )}
          {!value && !selection && run && <RunInspector run={run} error={error} onOpenCall={openCall} />}
        </aside>
      </div>

      {searching && runId && <SearchPalette run={runId} onPick={openCall} onClose={() => setSearching(false)} />}
    </div>
  );
}
