import { ReactFlowProvider } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Binding, CallNode, ErrorPath, ObjectFlow, ObjectSummary, RouteMap, RunDiff, RunSummary, StageDetail, StageGraph } from "./api";
import { CallTreeView } from "./CallTreeView";
import { ComponentView } from "./ComponentView";
import { DataFlowView, ObjectPicker } from "./DataFlowView";
import { DiffInspector, DiffPicker, DiffView } from "./DiffView";
import { ms, outcomeIcon, runLabel, shortName } from "./format";
import { CallInspector, ComponentInspector, ObjectInspector, RunInspector, StageInspector, ValueInspector } from "./Inspector";
import { ghostsOf, MapInspector, referenceFor } from "./MapView";
import { SearchPalette } from "./SearchPalette";
import { StageView } from "./StageView";
import { callKey, Item, Key, useCallTree } from "./useCallTree";

type View = "stages" | "stage" | "calls" | "flow" | "diff" | "map";
type Selection = { kind: "stage"; name: string } | { kind: "call"; id: number } | null;

type Wanted = { run: string | null; call: number | null; obj: number | null; stage: string | null; flow: boolean; vs: string | null; diff: boolean; map: boolean };
const nothing: Wanted = { run: null, call: null, obj: null, stage: null, flow: false, vs: null, diff: false, map: false };

function readHash(): Wanted {
  const p = new URLSearchParams(location.hash.slice(1));
  const num = (k: string) => (p.get(k) ? Number(p.get(k)) : null);
  return { run: p.get("run"), call: num("call"), obj: num("obj"), stage: p.get("stage"), flow: p.get("view") === "flow",
    vs: p.get("vs"), diff: p.get("view") === "diff", map: p.get("view") === "map" };
}

// Back/forward: a user navigation first duplicates the current history entry, then the hash effect replaces the
// new top entry with the new state. Automatic moves (opening a run on its failure, applying popstate) don't push.
const push = () => history.pushState(null, "", location.href);

export function App() {
  const initial = useMemo(readHash, []);
  const pending = useRef<Wanted | null>(initial);  // what the URL asks for, once its run has loaded
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoaded, setRunsLoaded] = useState(false);
  const [runId, setRunId] = useState<string | null>(initial.run);
  const [loadedRun, setLoadedRun] = useState<string | null>(null);  // the URL follows the state only once a run has loaded
  const [view, setView] = useState<View>("stages");
  const [stages, setStages] = useState<StageGraph | null>(null);
  const [error, setError] = useState<ErrorPath | null | undefined>(undefined);
  const [selection, setSelection] = useState<Selection>(null);
  const [value, setValue] = useState<Binding | null>(null);
  const [openStage, setOpenStage] = useState<string | null>(null);
  const [detail, setDetail] = useState<StageDetail | null>(null);
  const [followed, setFollowed] = useState<number | null>(null);
  const [compareWith, setCompareWith] = useState<string | null>(null);
  const [diff, setDiff] = useState<RunDiff | null>(null);
  const [routeMap, setRouteMap] = useState<RouteMap | null>(null);
  const [flow, setFlow] = useState<ObjectFlow | null>(null);
  const [objects, setObjects] = useState<ObjectSummary[] | undefined>(undefined);
  const [centerOn, setCenterOn] = useState<Key | null>(null);
  const [searching, setSearching] = useState(false);
  const [focusError, setFocusError] = useState(true);
  const [hideOpaque, setHideOpaque] = useState(false);
  const [showStdlib, setShowStdlib] = useState(false);
  const [hiddenStages, setHiddenStages] = useState<Set<string>>(new Set());
  const [minMs, setMinMs] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const tree = useCallTree(runId);
  const run = runs.find((r) => r.id === runId);

  useEffect(() => {
    api.runs().then((list) => {
      setRuns(list);
      setRunsLoaded(true);
      if (!runId && list.length) setRunId(list[list.length - 1].id);
    }).catch((e) => setProblem(String(e)));
  }, []);

  const goCall = useCallback(
    async (id: number) => {
      setView("calls");
      setValue(null);
      setSelection({ kind: "call", id });
      const key = await tree.reveal(id);
      setCenterOn(key);
    },
    [tree.reveal],
  );

  const goStage = useCallback((name: string) => {
    setOpenStage(name);
    setSelection(null);
    setValue(null);
    setView("stage");
  }, []);

  useEffect(() => {
    setDetail(null);
    if (!runId || openStage == null) return;
    let live = true;
    api.stage(runId, openStage).then((d) => live && setDetail(d)).catch((e) => setProblem(String(e)));
    return () => {
      live = false;
    };
  }, [runId, openStage]);

  const goFollow = useCallback((oid: number | null) => {
    setFollowed(oid);
    setValue(null);
    setView("flow");
  }, []);

  const goDiff = useCallback((vs: string | null) => {
    setCompareWith(vs);
    setValue(null);
    setView("diff");
  }, []);

  useEffect(() => {
    setDiff(null);
    if (!runId || !compareWith || view !== "diff") return;
    let live = true;
    api.compare(runId, compareWith).then((d) => live && setDiff(d)).catch((e) => setProblem(String(e)));
    return () => {
      live = false;
    };
  }, [runId, compareWith, view === "diff"]);

  useEffect(() => {  // a failed run is shown against a run that got further: the map says which
    if (!error || view === "map") return;
    let live = true;
    api.map().then((m) => live && setRouteMap(m)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [error]);

  const goMap = useCallback(() => {
    setView("map");
    setSelection(null);
    setValue(null);
  }, []);

  useEffect(() => {
    if (view !== "map") return;
    let live = true;
    api.map().then((m) => live && setRouteMap(m)).catch((e) => setProblem(String(e)));
    return () => {
      live = false;
    };
  }, [view === "map"]);

  const goOverview = useCallback(() => {
    setView("stages");
    setSelection(null);
    setValue(null);
    setOpenStage(null);
  }, []);

  const openCall = useCallback((id: number) => { push(); goCall(id); }, [goCall]);
  const showStage = useCallback((name: string) => { push(); goStage(name); }, [goStage]);
  const follow = useCallback((oid: number | null) => { push(); goFollow(oid); }, [goFollow]);
  const toOverview = () => { push(); goOverview(); };
  /** Open a call of the compared runs: A is the current run; B becomes the current run (Back returns to the diff). */
  const openIn = (side: "a" | "b", id: number) => {
    if (side === "a" || !compareWith) return openCall(id);
    push();
    pending.current = { ...nothing, run: compareWith, call: id };
    setRunId(compareWith);
  };
  /** Open a stage from the map, in the current run if it went through the stage. */
  const openFromMap = (id: string, stage: string) => {
    if (id === runId) return showStage(stage);
    push();
    pending.current = { ...nothing, run: id, stage };
    setRunId(id);
  };

  /** Open what the URL asked for; without anything, a run opens on its failure (FR-VIZ-8) or its overview, or on
   * its one stage of the project's own (a package staged as one: its modules say more than a single box). */
  const applyPending = useCallback((err: ErrorPath | null, g?: StageGraph) => {
    const own = g?.nodes.filter((n) => n.observed) ?? [];
    const h = pending.current;
    pending.current = null;
    if (h?.map) goMap();
    else if (h?.vs || h?.diff) goDiff(h.vs);
    else if (h?.obj != null || h?.flow) goFollow(h.obj);
    else if (h?.stage) goStage(h.stage);
    else if (h?.call != null) goCall(h.call);
    else if (err) goCall(err.origin.id);
    else if (own.length === 1) goStage(own[0].name);
    else goOverview();
  }, [goMap, goDiff, goFollow, goStage, goCall, goOverview]);

  useEffect(() => {
    setFlow(null);
    if (!runId || followed == null) return;
    let live = true;
    api.object(runId, followed).then((f) => live && setFlow(f)).catch((e) => setProblem(String(e)));
    return () => {
      live = false;
    };
  }, [runId, followed]);

  useEffect(() => {
    if (runId && view === "flow" && objects === undefined) api.objects(runId).then(setObjects).catch((e) => setProblem(String(e)));
  }, [runId, view, objects]);

  useEffect(() => {
    if (!runId) return;
    tree.reset();
    setStages(null);
    setError(undefined);
    setSelection(null);
    setValue(null);
    setCenterOn(null);
    setFollowed(null);
    setObjects(undefined);
    setOpenStage(null);
    setCompareWith(null);
    let live = true;
    Promise.all([api.stages(runId), api.error(runId)]).then(([g, err]) => {
      if (!live) return;
      setStages(g);
      setError(err);
      setLoadedRun(runId);
      applyPending(err, g);
    }).catch((e) => setProblem(String(e)));
    return () => {
      live = false;
    };
  }, [runId]);

  useEffect(() => {
    // while a run loads, the state still describes the previous one: writing it would mix the two in the URL
    if (loadedRun !== runId) return;
    const p = new URLSearchParams();
    if (runId) p.set("run", runId);
    if (view === "map") p.set("view", "map");
    else if (view === "diff") p.set(compareWith ? "vs" : "view", compareWith ?? "diff");
    else if (view === "flow") p.set(followed != null ? "obj" : "view", followed != null ? String(followed) : "flow");
    else if (view === "stage" && openStage) p.set("stage", openStage);
    else if (selection?.kind === "call") p.set("call", String(selection.id));
    history.replaceState(null, "", `#${p}`);
  }, [runId, loadedRun, selection, view, followed, openStage, compareWith]);

  useEffect(() => {
    const onPop = () => {
      const h = readHash();
      pending.current = h;
      if (h.run && h.run !== runId) setRunId(h.run);  // the run's effect applies the rest once it has loaded
      else applyPending(null);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [runId, applyPending]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearching(true);
      }
      if (e.key === "Escape" && !searching) {
        if (value) setValue(null);
        else if (view !== "stages") { push(); setView("stages"); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searching, value, view]);

  const cut = useCallback((c: CallNode): string | null => {
    if (!run) return null;
    if (run.cut_calls.includes(c.id)) return "max_calls reached here: tracing stopped, later calls were not recorded";
    if (run.truncated.includes("limit_depth") && run.max_depth != null && c.depth >= run.max_depth)
      return `max_depth (${run.max_depth}) reached: calls below this one, if any, were not recorded`;
    return null;
  }, [run]);

  const hide = useCallback((item: Item): boolean => {
    if (item.type !== "call" && item.type !== "group") return false;
    if (hideOpaque && item.type === "call" && !item.observed && item.children === 0) return true;
    if (item.stage && hiddenStages.has(item.stage)) return true;
    const ns = item.type === "call" ? item.duration_ns : item.total_ns;
    return minMs > 0 && ns != null && ns < minMs * 1e6;
  }, [hideOpaque, hiddenStages, minMs]);
  const toggleStage = (name: string) => setHiddenStages((h) => {
    const next = new Set(h);
    if (!next.delete(name)) next.add(name);
    return next;
  });

  const reference = run && error && routeMap ? referenceFor(run, runs, routeMap) : null;
  const refRun = runs.find((r) => r.id === reference);
  const ghosts = useMemo(() => (runId && refRun && routeMap ? ghostsOf(runId, refRun.id, routeMap, runLabel(refRun)) : undefined),
    [runId, refRun, routeMap]);
  const errorPath = useMemo(() => new Set<Key>(error ? error.path.map((c) => callKey(c.id)) : []), [error]);
  const selectedCall: CallNode | undefined =
    selection?.kind === "call" ? tree.callItem(callKey(selection.id)) ?? error?.path.find((c) => c.id === selection.id) : undefined;
  const isFailureView = view === "calls" && selection?.kind === "call" && error?.origin.id === selection.id;
  const level = view === "map" ? "Map · all runs" : view === "diff" ? "Diff" : view === "flow" ? "Data Flow" : view === "stage" ? "L2 · Components" : value ? "L4 · Value" : view === "stages" ? "L1 · Overview" : isFailureView ? "L3 · Failure" : selection ? "L3 · Function" : "L3 · Calls";

  const crumbs: { label: string; onClick?: () => void }[] = [{ label: "Run", onClick: toOverview }];
  if (run) crumbs.push({ label: runLabel(run), onClick: toOverview });
  if (view === "stage" && openStage) crumbs.push({ label: openStage });
  const runB = runs.find((r) => r.id === compareWith);
  if (view === "map") {
    crumbs.splice(0, crumbs.length, { label: "Map", onClick: () => setSelection(null) });
    if (selection?.kind === "stage") crumbs.push({ label: selection.name });
  } else if (view === "diff") {
    crumbs.splice(0, 1, { label: "Compare", onClick: () => { push(); goDiff(null); } });
    if (runB) crumbs.push({ label: runLabel(runB) });
  } else if (view === "flow") {
    crumbs.push({ label: "Data Flow", onClick: () => follow(null) });
    if (flow) crumbs.push({ label: `${flow.name ?? shortName(flow.type ?? "object")} #${flow.object_id}` });
  } else if (selection?.kind === "stage") crumbs.push({ label: selection.name });
  if (view !== "flow" && selectedCall) {
    if (selectedCall.stage) crumbs.push({ label: selectedCall.stage, onClick: () => showStage(selectedCall.stage!) });
    crumbs.push({ label: shortName(selectedCall.qualname), onClick: () => setValue(null) });
  }
  if (view !== "flow" && value) crumbs.push({ label: `${value.name ?? value.kind}${value.object_id != null ? ` #${value.object_id}` : ""}` });

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
          <button className={view !== "flow" && view !== "diff" && view !== "map" ? "active" : ""} onClick={() => { push(); setView(selection?.kind === "call" ? "calls" : openStage ? "stage" : "stages"); }}>Execution Flow</button>
          <button className={view === "flow" ? "active" : ""} onClick={() => runId && follow(value?.object_id ?? followed)}
            title="Follow one object across calls">Data Flow</button>
          <button className={view === "diff" ? "active" : ""} onClick={() => { if (runId) { push(); goDiff(compareWith); } }} title="Compare this run with another">Diff</button>
          <button className={view === "map" ? "active" : ""} onClick={() => { push(); goMap(); }} title="All runs on one map, each run's route on it">Map</button>
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
            <button key={r.id} className={`run-row ${r.id === runId ? "active" : ""}`} onClick={() => { push(); pending.current = view === "map" ? { ...nothing, run: r.id, map: true } : null; setRunId(r.id); }} title={r.id}>
              <span className={r.error ? "muted" : r.outcome} style={{ fontSize: 10 }}>{r.error ? "?" : outcomeIcon[r.outcome]}</span>
              <span className="name">{runLabel(r)}</span>
              {r.http_status && <span className={Number(r.http_status) >= 500 ? "raised mono" : "muted mono"} style={{ fontSize: 11 }}>{r.http_status}</span>}
              <span className="muted" style={{ fontSize: 11 }}>{ms(r.duration_ns)}</span>
            </button>
          ))}
          {!runs.length && <div className="muted">No runs. Try <span className="mono">vizcontrol run app.py</span></div>}
          {stages && (
            <>
              <div className="label" style={{ marginTop: 8 }}>Stages</div>
              {stages.nodes.filter((s) => showStdlib || !s.stdlib || s.raised).map((s) => (
                <div key={s.name} className="stage-row">
                  <input type="checkbox" checked={!hiddenStages.has(s.name)} onChange={() => toggleStage(s.name)}
                    title="Show this stage's calls in the call tree" aria-label={`Show ${s.name} calls`} />
                  <button onClick={() => showStage(s.name)} title="Open this stage">
                    <span className={s.raised ? "raised" : s.observed ? "ok" : "muted"}>{s.raised ? "⚠" : s.observed ? "●" : "▒"}</span>
                    <span style={{ flex: 1 }}>{s.name}</span>
                    <span className="muted">{s.calls}</span>
                  </button>
                </div>
              ))}
            </>
          )}
          <div className="label" style={{ marginTop: 8 }}>Filters</div>
          {error && (
            <label className="toggle"><input type="checkbox" checked={focusError} onChange={(e) => setFocusError(e.target.checked)} />Dim calls off the error path</label>
          )}
          <label className="toggle"><input type="checkbox" checked={hideOpaque} onChange={(e) => setHideOpaque(e.target.checked)} />Hide library calls without callbacks</label>
          <label className="toggle"><input type="checkbox" checked={showStdlib} onChange={(e) => setShowStdlib(e.target.checked)} />Show standard library stages</label>
          <label className="toggle">
            Min duration
            <select value={minMs} onChange={(e) => setMinMs(Number(e.target.value))}>
              {[0, 0.01, 0.1, 1, 10, 100].map((v) => <option key={v} value={v}>{v ? `≥ ${v} ms` : "any"}</option>)}
            </select>
          </label>
          <div className="muted" style={{ fontSize: 11 }}>Filtered calls fold into "hidden (N)". The selection and the error path always show.</div>
        </aside>

        <section className="canvas">
          {problem && <div className="empty raised">{problem}</div>}
          {!problem && runsLoaded && !runs.length && (
            <div className="empty">
              <div className="first-run">
                <div className="title-lg">No runs here yet</div>
                <p className="muted">Trace your program, then reload this page:</p>
                <pre className="mono">{"vizcontrol run app.py           # a script\nvizcontrol run -m package.cli   # a module\npytest --vizcontrol             # one run per test"}</pre>
                <p className="muted">Runs are written to <span className="mono">.vizcontrol/runs</span> of the directory you run them from.</p>
              </div>
            </div>
          )}
          {!problem && view === "stages" && stages && (
            <>
              <div className="hint">
                {error ? <>Failed in <span className="raised">{error.origin.stage}</span>{ghosts ? ` · grey: not reached, seen in ${ghosts.seenIn}` : ""}</>
                  : "Main path in accent"} · double-click a stage to open it
              </div>
              <CopyMermaid runId={runId!} />
              <StageView
                graph={stages}
                stopped={error?.origin.stage ?? undefined}
                ghosts={ghosts}
                showStdlib={showStdlib}
                selected={selection?.kind === "stage" ? selection.name : null}
                onSelect={(name) => { setSelection({ kind: "stage", name }); setValue(null); }}
                onOpen={(name) => (refRun && ghosts?.nodes.some((n) => n.name === name) ? openFromMap(refRun.id, name) : showStage(name))}
              />
            </>
          )}
          {!problem && view === "map" && (
            routeMap ? (
              <>
                <div className="hint">Every run on one map · the selected run's route in accent · double-click a stage to open it</div>
                <StageView
                  graph={routeMap}
                  showStdlib={showStdlib}
                  route={routeMap.routes.find((r) => r.run === runId)}
                  selected={selection?.kind === "stage" ? selection.name : null}
                  onSelect={(name) => setSelection({ kind: "stage", name })}
                  onOpen={(name) => {
                    const ids = routeMap.nodes.find((n) => n.name === name)?.runs ?? [];
                    if (ids.length) openFromMap(runId && ids.includes(runId) ? runId : ids[0], name);
                  }}
                />
              </>
            ) : <div className="empty">Loading…</div>
          )}
          {!problem && view === "calls" && (
            <>
              {error ? (
                <div className="banner">
                  ⚠ Run failed · {error.exception.type} in {shortName(error.origin.qualname)} · error path in red
                  {refRun && <> · <button className="link" onClick={() => { push(); goDiff(refRun.id); }}>compare with {runLabel(refRun)}</button></>}
                </div>
              ) : (
                <div className="hint">Click a call to inspect it · › expands · Esc back to stages</div>
              )}
              <ReactFlowProvider>
                <CallTreeView
                  tree={tree}
                  selected={selection?.kind === "call" ? callKey(selection.id) : null}
                  errorPath={errorPath}
                  focusError={focusError}
                  hide={hide}
                  centerOn={centerOn}
                  cut={cut}
                  onSelect={(key) => { if (key.startsWith("c")) { push(); setSelection({ kind: "call", id: Number(key.slice(1)) }); setValue(null); } else tree.toggle(key); }}
                />
              </ReactFlowProvider>
            </>
          )}
          {!problem && view === "stage" && (
            detail ? (
              <>
                <div className="hint">Stage {detail.stage} opened · modules › classes › functions · click a function to open its first call</div>
                <CopyMermaid runId={runId!} stage={detail.stage} />
                <ComponentView key={detail.stage} detail={detail} onOpenStage={showStage} onOpenCall={openCall} stopped={error?.origin.code_id}
                  hidden={showStdlib ? undefined : new Set(stages?.nodes.filter((s) => s.stdlib && !s.raised).map((s) => s.name))} />
              </>
            ) : <div className="empty">Loading…</div>
          )}
          {!problem && view === "diff" && run && (
            !compareWith ? <DiffPicker runs={runs} current={run} onPick={(id) => { push(); goDiff(id); }} />
              : diff && runB ? <DiffView diff={diff} a={run} b={runB} onOpen={openIn} />
              : <div className="empty">Comparing…</div>
          )}
          {!problem && view === "flow" && (
            flow ? (
              <>
                <div className="hint">Following <span className="flow mono">{flow.name ?? shortName(flow.type ?? "object")} #{flow.object_id}</span> · Δ field changed since the previous step · click a step to open the call</div>
                <DataFlowView flow={flow} onOpenCall={openCall} />
              </>
            ) : followed == null ? <ObjectPicker objects={objects} onFollow={follow} /> : <div className="empty">Loading…</div>
          )}
          {view !== "flow" && view !== "diff" && <div className="legend">
            <span><span className="ok">●</span> ok</span>
            <span><span className="raised">⚠</span> raised</span>
            <span><span className="unfinished">◌</span> unfinished</span>
            <span>▒ not observed</span>
            <span>┄ inferred</span>
            {run?.truncated.length ? <span><span className="unfinished">✂</span> cut by a limit</span> : null}
          </div>}
        </section>

        <aside className="panel inspector">
          {runId && view === "flow" && flow && <ObjectInspector run={runId} flow={flow} onOpenCall={openCall} onFollow={follow} />}
          {view === "diff" && diff && <DiffInspector diff={diff} onOpen={openIn} />}
          {view === "diff" && !diff && run && <RunInspector run={run} error={error} onOpenCall={openCall} />}
          {view === "flow" && !flow && run && <RunInspector run={run} error={error} onOpenCall={openCall} />}
          {view === "map" && routeMap && (
            <MapInspector map={routeMap} runs={runs} current={runId} onOpen={openFromMap}
              selected={selection?.kind === "stage" ? routeMap.nodes.find((n) => n.name === selection.name) ?? null : null} />
          )}
          {view === "stage" && detail && <ComponentInspector detail={detail} onOpenCall={openCall} onOpenStage={showStage} />}
          {view !== "flow" && view !== "stage" && view !== "diff" && view !== "map" && <>
          {runId && value && <ValueInspector run={runId} binding={value} onBack={() => setValue(null)} onOpenCall={openCall} onFollow={follow} />}
          {runId && !value && selection?.kind === "call" && (
            <CallInspector run={runId} id={selection.id} error={error} onOpenCall={openCall} onOpenValue={(b) => { push(); setValue(b); }}
              cut={selectedCall ? cut(selectedCall) : null} />
          )}
          {!value && selection?.kind === "stage" && stages?.nodes.some((n) => n.name === selection.name) && (
            <StageInspector stage={stages.nodes.find((n) => n.name === selection.name)!} onOpenCall={openCall} onOpenStage={showStage} />
          )}
          {!value && selection?.kind === "stage" && refRun && ghosts?.nodes.some((n) => n.name === selection.name) && (
            <>
              <div className="title-lg">{selection.name}</div>
              <div className="box dashed muted" style={{ fontSize: 12 }}>┄ Not reached by this run. <span className="mono">{ghosts.seenIn}</span> went through it.</div>
              <button className="link" onClick={() => openFromMap(refRun.id, selection.name)}>Open it in {ghosts.seenIn} →</button>
              <button className="link" onClick={() => { push(); goDiff(refRun.id); }}>Compare the two runs →</button>
            </>
          )}
          {!value && !selection && run && (
            <RunInspector run={run} error={error} onOpenCall={openCall}
              reference={refRun && { label: runLabel(refRun), notReached: ghosts?.nodes.length ?? 0, onCompare: () => { push(); goDiff(refRun.id); } }} />
          )}
          </>}
        </aside>
      </div>

      {searching && runId && <SearchPalette run={runId} onPick={openCall} onClose={() => setSearching(false)} />}
    </div>
  );
}

/** The diagram on screen as Mermaid text (`vizcontrol diagram`), for a README, docs or a pull request. */
function CopyMermaid({ runId, stage }: { runId: string; stage?: string }) {
  const [state, setState] = useState<"" | "copied" | "failed">("");
  const copy = () =>
    api.mermaid(runId, stage)
      .then((text) => navigator.clipboard.writeText(text))
      .then(() => setState("copied"), () => setState("failed"))
      .finally(() => setTimeout(() => setState(""), 1500));
  return (
    <button className="chip outline copy-mermaid" onClick={copy} title="Copy this diagram as a Mermaid flowchart">
      {state === "copied" ? "✓ Copied" : state === "failed" ? "Copy failed" : "Copy as Mermaid"}
    </button>
  );
}
