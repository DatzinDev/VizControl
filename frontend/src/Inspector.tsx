import { useEffect, useRef, useState } from "react";
import { api, Binding, CallDetail, Change, Enc, ErrorPath, Hop, ObjectFlow, ObjectSummary, RunSummary, Source, StageDetail, StageNode, Value } from "./api";
import { fnLabel } from "./ComponentView";
import { changeText, ms, outcomeIcon, runLabel, shortName } from "./format";

function useLoad<T>(load: () => Promise<T>, deps: unknown[]): T | undefined {
  const [value, setValue] = useState<T>();
  useEffect(() => {
    let live = true;
    setValue(undefined);
    load().then((v) => live && setValue(v)).catch(() => live && setValue(undefined));
    return () => {
      live = false;
    };
  }, deps);
  return value;
}

function Outcome({ outcome, extra }: { outcome: string; extra?: string }) {
  return (
    <div className="chip" style={{ background: "none", padding: 0 }}>
      <span className={outcome}>{outcomeIcon[outcome] ?? "●"}</span>
      <span className={outcome} style={{ fontWeight: 600 }}>{outcome}</span>
      {extra && <span className="muted">· {extra}</span>}
    </div>
  );
}

export function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`section ${className ?? ""}`}>
      <div className="label">{title}</div>
      {children}
    </div>
  );
}

export function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <div className="kv">
      {rows.map(([k, v]) => [<span key={`k${k}`} className="k">{k}</span>, <span key={`v${k}`} className="v">{v}</span>])}
    </div>
  );
}

// -- run and stage ------------------------------------------------------------------------------------------------

export function RunInspector({ run, error, onOpenCall, reference }: {
  run: RunSummary;
  error: ErrorPath | null | undefined;
  onOpenCall: (id: number) => void;
  /** a run that got further, to compare with (failed runs only) */
  reference?: { label: string; notReached: number; onCompare: () => void } | null;
}) {
  return (
    <>
      <div className="title-lg">{runLabel(run)}</div>
      <Outcome outcome={run.outcome} extra={`${run.kind}${run.exit_code != null ? ` · exit ${run.exit_code}` : ""}`} />
      {run.process_gone && (
        <div className="box dashed unfinished" style={{ fontSize: 12 }}>
          ┄ The process that wrote this run is gone without closing it (killed?). Everything flushed before is here.
        </div>
      )}
      <Section title="Run">
        <KV rows={[["duration", ms(run.duration_ns)], ["calls", run.calls.toLocaleString()], ["level", run.level], ["started", run.started.replace("T", " ").slice(0, 19)],
          ["truncated", run.truncated.length ? <span className="unfinished">{run.truncated.join(", ")}</span> : <span className="ok">no</span>]]} />
      </Section>
      {run.regions.length > 0 && (
        <Section title="Regions (nested trace blocks)">
          {run.regions.map((g) => (
            <button key={g.id} className="binding" onClick={() => g.call_id != null && onOpenCall(g.call_id)} disabled={g.call_id == null}>
              <span className="v">{g.name}</span>
              <span className="k">seq {g.seq_start}–{g.seq_end}</span>
            </button>
          ))}
        </Section>
      )}
      {error && (
        <Section title="Stopped here" className="box warn">
          <div className="mono raised">{error.exception.type}: {error.exception.message}</div>
          <button className="link" onClick={() => onOpenCall(error.origin.id)}>
            {error.origin.qualname} · line {error.exception.raised_line}{error.origin.stage ? ` · stage ${error.origin.stage}` : ""}
          </button>
          {error.locals.length > 0 && (
            <KV rows={error.locals.slice(0, 6).map((b) => [b.name ?? b.kind, <span className="mono">{b.preview}</span>] as [string, React.ReactNode])} />
          )}
          {error.recent_changes.length > 0 && <ChangeList changes={error.recent_changes.slice(-3)} onOpenCall={onOpenCall} />}
        </Section>
      )}
      {error && reference && (
        <Section title="Compared with a run that got further">
          <div className="muted" style={{ fontSize: 12 }}>
            {reference.notReached ? `${reference.notReached === 1 ? "1 stage in grey was" : `${reference.notReached} stages in grey were`} reached by ` : "Nothing more was reached by "}
            <span className="mono">{reference.label}</span>.
          </div>
          <button className="link" onClick={reference.onCompare}>Compare with {reference.label} →</button>
        </Section>
      )}
      <div className="box dashed muted" style={{ fontSize: 12 }}>
        ┄ Stages are inferred from module paths (stage_depth), or from the functions the entry fans out to when the project has few modules; [tool.vizcontrol.stages] overrides both. Double-click a stage to open it.
      </div>
    </>
  );
}

export function StageInspector({ stage, onOpenCall, onOpenStage }: { stage: StageNode; onOpenCall: (id: number) => void; onOpenStage: (name: string) => void }) {
  return (
    <>
      <div className="title-lg">{stage.name}</div>
      <Outcome outcome={stage.raised ? "raised" : "ok"} extra={stage.observed ? "stage (inferred)" : "library · not observed inside"} />
      <Section title="Stage">
        <KV rows={[["calls", stage.calls.toLocaleString()], ["total", ms(stage.total_ns)], ["self", ms(stage.self_ns)], ["raised", String(stage.raised)]]} />
      </Section>
      <Section title="Modules">
        {stage.modules.map((m) => <div key={m} className="mono" style={{ fontSize: 12 }}>{m}</div>)}
      </Section>
      <button className="link" onClick={() => onOpenStage(stage.name)}>Open stage →</button>
      <button className="link" onClick={() => onOpenCall(stage.first_call)}>Open first call →</button>
    </>
  );
}

/** Level 2: the opened stage's numbers, where it is entered from, what it calls and its files. */
export function ComponentInspector(props: { detail: StageDetail; onOpenCall: (id: number) => void; onOpenStage: (name: string) => void }) {
  const { detail, onOpenCall, onOpenStage } = props;
  const st = detail.stats;
  const fn = new Map(detail.functions.map((f) => [f.code_id, f]));
  const raised = detail.functions.reduce((n, f) => n + f.raised, 0);
  const out = new Map<string, number>();
  for (const o of detail.outgoing) out.set(o.stage, (out.get(o.stage) ?? 0) + o.calls);
  const files = [...new Set(detail.functions.map((f) => f.file).filter(Boolean))] as string[];
  return (
    <>
      <div className="title-lg">{detail.stage}</div>
      <Outcome outcome={raised ? "raised" : "ok"} extra={detail.observed ? "stage (inferred)" : "library · not observed inside"} />
      <Section title="Stage">
        <KV rows={[["modules", String(st.modules)], ["classes", String(st.classes)], ["functions", String(st.functions)],
          ["calls", st.calls.toLocaleString()], ["total", ms(st.total_ns)], ["self", ms(st.self_ns)]]} />
      </Section>
      {detail.incoming.length > 0 && (
        <Section title="Entered from">
          {detail.incoming.slice(0, 10).map((i, k) => {
            const to = fn.get(i.to)!;
            return (
              <button key={k} className="binding" onClick={() => onOpenCall(to.first_call)}>
                <span className="k">{i.stage ?? "run"}</span>
                <span className="v">{i.from_qualname ?? "entry"} → {to.kind === "method" || to.kind === "function" ? to.qualname : fnLabel(to)}{i.calls > 1 ? ` ×${i.calls}` : ""}</span>
              </button>
            );
          })}
        </Section>
      )}
      {out.size > 0 && (
        <Section title="Calls out to">
          {[...out].sort((a, b) => b[1] - a[1]).map(([stage, n]) => (
            <button key={stage} className="binding" onClick={() => onOpenStage(stage)}>
              <span className="v">{stage}</span>
              <span className="k">×{n}</span>
            </button>
          ))}
        </Section>
      )}
      <Section title="Files">
        {files.map((f) => <div key={f} className="mono" style={{ fontSize: 12 }}>{f}</div>)}
      </Section>
      <div className="box dashed muted" style={{ fontSize: 12 }}>
        ┄ One node per function, its calls added up. Click a function to open its first call, or a collapsed stage to open it.
      </div>
    </>
  );
}

// -- call ---------------------------------------------------------------------------------------------------------

function BindingRow({ b, onOpen }: { b: Binding; onOpen: (b: Binding) => void }) {
  return (
    <button className="binding" onClick={() => onOpen(b)} title="Inspect value">
      <span className="k">{b.kind === "return" ? "→" : b.name}</span>
      <span className={`v ${b.preview === "‹redacted›" ? "unfinished" : ""}`}>{b.preview}</span>
      {b.object_id != null && <span className="oid">#{b.object_id}</span>}
    </button>
  );
}

/** The whole file, read-only, the line in view (FR-VIZ-9). Esc or a click outside closes it. */
function SourcePanel({ source, highlight, raised, onClose }: { source: Source; highlight: number | null; raised: boolean; onClose: () => void }) {
  const mark = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    mark.current?.scrollIntoView({ block: "center" });  // braces: newer browsers return a promise, not a cleanup
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return (
    <div className="palette-bg" onClick={onClose}>
      <div className="palette source-panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Source">
        <div className="head"><span className="mono">{source.relpath ?? source.path}</span><button className="back" onClick={onClose}>close ✕</button></div>
        <pre className="source">
          {source.source!.split("\n").map((text, i) => (
            <span key={i} ref={i + 1 === highlight ? mark : undefined} className={i + 1 === highlight ? `hl ${raised ? "raised" : ""}` : undefined}>
              <span className="ln">{i + 1}</span>
              {text}
              {"\n"}
            </span>
          ))}
        </pre>
      </div>
    </div>
  );
}

function SourceExcerpt({ source, first, highlight, raised }: { source: Source; first: number; highlight: number | null; raised: boolean }) {
  if (!source.source) return <div className="muted">Source not stored for {source.relpath ?? source.path}</div>;
  const lines = source.source.split("\n");
  const center = highlight ?? first;
  const start = Math.max(first, center - 6, 1);
  const end = Math.min(lines.length, Math.max(start + 10, center + 4));
  return (
    <pre className="source">
      {lines.slice(start - 1, end).map((text, i) => {
        const n = start + i;
        return (
          <span key={n} className={n === highlight ? `hl ${raised ? "raised" : ""}` : undefined}>
            <span className="ln">{n}</span>
            {text}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

export function CallInspector(props: {
  run: string;
  id: number;
  error: ErrorPath | null | undefined;
  onOpenCall: (id: number) => void;
  onOpenValue: (b: Binding) => void;
  cut?: string | null;
}) {
  const { run, id, error, onOpenCall, onOpenValue, cut } = props;
  const call = useLoad<CallDetail>(() => api.call(run, id), [run, id]);
  const bindings = useLoad<Binding[]>(() => api.bindings(run, id), [run, id]) ?? [];
  const source = useLoad<Source | undefined>(async () => (call?.file_id ? api.source(run, call.file_id) : undefined), [run, call?.file_id]);
  const [fullSource, setFullSource] = useState(false);
  if (!call) return <div className="muted">Loading…</div>;
  const isOrigin = error?.origin.id === id;
  const by = (kind: string) => bindings.filter((b) => b.kind === kind);
  const args = by("arg");
  const ret = [...by("return"), ...by("self")];
  const raiseLocals = by("raise");
  const highlight = isOrigin ? error!.exception.raised_line : call.first_line;
  return (
    <>
      <div className="title-lg">{call.qualname}</div>
      <Outcome outcome={call.observed ? call.outcome : "ok"} extra={`${call.observed ? "" : "not observed inside · "}${ms(call.duration_ns, !call.end_exact)}`} />
      {isOrigin && (
        <Section title="Exception" className="box" >
          <div className="mono raised" style={{ fontWeight: 600 }}>⚠ {error!.exception.type}</div>
          <div className="mono">{error!.exception.message}</div>
          <KV rows={[["at", `${call.file}:${error!.exception.raised_line}`], ["outcome", <span className="raised">escaped the run · not handled</span>]]} />
        </Section>
      )}
      {!isOrigin && call.exception && (
        <Section title="Exception">
          <div className="mono raised">{call.exception.type}: {call.exception.message}</div>
          {call.exception.raised_call_id != null && call.exception.raised_call_id !== id && (
            <button className="link" onClick={() => onOpenCall(call.exception!.raised_call_id!)}>raised in call #{call.exception.raised_call_id} →</button>
          )}
        </Section>
      )}
      <Section title="Location">
        <KV rows={[
          ["file", call.file ? `${call.file}:${call.first_line ?? ""}` : "—"],
          ["caller", call.caller ? <button className="link" onClick={() => onOpenCall(call.caller!.id)}>{call.caller.qualname}{call.caller.line ? ` (line ${call.caller.line})` : ""}</button>
            : call.started_by ? <button className="link" onClick={() => onOpenCall(call.started_by!.caller_id ?? call.started_by!.id)}
                title="Inferred: matched to the Thread.start call by time">┄ thread started by {call.started_by.caller ?? "Thread.start"}</button>
            : "— (root)"],
          ["stage", call.stage ?? "—"],
          ["thread", `${call.thread_id} · seq ${call.seq}`],
        ]} />
      </Section>
      {isOrigin && raiseLocals.length > 0 && (
        <Section title="Locals at raise">{raiseLocals.map((b, i) => <BindingRow key={i} b={b} onOpen={onOpenValue} />)}</Section>
      )}
      {isOrigin && error!.recent_changes.length > 0 && (
        <Section title="Last changes before raise" className="box warn">
          {error!.recent_changes.map((c, i) => (
            <div key={i} className="diff mono" style={{ fontSize: 12 }}>
              {c.changes.map((d, j) => (
                <div key={j}>
                  <span className={d.op}>{changeText(c.name ?? "", d)}</span>{" "}
                  <button className="link" onClick={() => onOpenCall(c.call_after)}>seq {c.seq_after}</button>
                </div>
              ))}
            </div>
          ))}
        </Section>
      )}
      {cut && <Section title="Not captured" className="box warn"><div style={{ fontSize: 12 }}>✂ {cut}</div></Section>}
      {args.length > 0 && <Section title="Arguments">{args.map((b, i) => <BindingRow key={i} b={b} onOpen={onOpenValue} />)}</Section>}
      {ret.length > 0 && <Section title="Return">{ret.map((b, i) => <BindingRow key={i} b={b} onOpen={onOpenValue} />)}</Section>}
      {by("local").length > 0 && <Section title="Locals at end">{by("local").map((b, i) => <BindingRow key={i} b={b} onOpen={onOpenValue} />)}</Section>}
      {isOrigin && (
        <Section title="Traceback">
          {error!.traceback.map(([file, line, name], i) => (
            <div key={i} className="mono" style={{ fontSize: 12 }}>
              <span className={i === error!.traceback.length - 1 ? "raised" : "muted"}>▸</span> {file.split("/").slice(-2).join("/")}:{line} <span className="muted">{name}</span>
            </div>
          ))}
        </Section>
      )}
      {source && (
        <Section title={`Source · ${source.relpath ?? source.path}`} className="box">
          <SourceExcerpt source={source} first={call.first_line ?? 1} highlight={highlight} raised={isOrigin} />
          {source.source && <button className="link" onClick={() => setFullSource(true)}>View file →</button>}
        </Section>
      )}
      {fullSource && source?.source && (
        <SourcePanel source={source} highlight={highlight} raised={isOrigin} onClose={() => setFullSource(false)} />
      )}
    </>
  );
}

// -- values (Level 4) ---------------------------------------------------------------------------------------------

export function previewEnc(d: Enc, budget = 80): string {
  const t = d.t;
  let s: string;
  if (t === "none") s = "None";
  else if (t === "bool" || t === "int" || t === "float") s = String(d.v);
  else if (t === "str") s = JSON.stringify(d.v) + ("len" in d ? "…" : "");
  else if (t === "repr" || t === "custom") s = String(typeof d.v === "string" ? d.v : JSON.stringify(d.v));
  else if (t === "list" || t === "tuple" || t === "set" || t === "frozenset") s = `${t}[${d.len ?? d.items.length}]`;
  else if (t === "dict") s = `dict{${d.len ?? d.items.length}}`;
  else if (t === "obj") s = `${String(d.type).split(".").pop()}{${Object.keys(d.attrs).length}}`;
  else if (t === "redacted") s = "‹redacted›";
  else if (t === "ref") s = `↺ #${d.oid}`;
  else if (t === "trunc") s = `… ${d.type ?? ""} (truncated)`;
  else s = `<${d.label ?? d.type ?? t}>`;
  return s.length > budget ? s.slice(0, budget - 1) + "…" : s;
}

function children(d: Enc): [string, Enc][] {
  if (d.t === "obj") return Object.entries(d.attrs as Record<string, Enc>);
  if (d.t === "dict") return (d.items as [Enc, Enc][]).map(([k, v]) => [previewEnc(k, 30), v]);
  if (["list", "tuple", "set", "frozenset"].includes(d.t)) return (d.items as Enc[]).map((v, i) => [`[${i}]`, v]);
  return [];
}

function ValueNode({ label, d, depth, onFollow }: { label: string; d: Enc; depth: number; onFollow: (oid: number) => void }) {
  const kids = children(d);
  const [open, setOpen] = useState(depth < 2);
  const more = d.len != null && d.len > kids.length ? d.len - kids.length : 0;
  return (
    <>
      <div className="row" style={{ "--d": depth } as React.CSSProperties}>
        {kids.length ? (
          <button className="k" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} {label}</button>
        ) : (
          <span className="k">{label}</span>
        )}
        <span className={d.t === "redacted" ? "unfinished" : d.t === "trunc" ? "muted" : undefined}>{previewEnc(d)}</span>
        {d.oid != null && <button className="oid" onClick={() => onFollow(d.oid as number)} title="Follow in Data Flow">#{d.oid}</button>}
      </div>
      {open && kids.map(([k, v], i) => <ValueNode key={i} label={k} d={v} depth={depth + 1} onFollow={onFollow} />)}
      {open && more > 0 && <div className="row muted" style={{ "--d": depth + 1 } as React.CSSProperties}>… {more} more not captured</div>}
    </>
  );
}

function ChangeList({ changes, onOpenCall }: { changes: Change[]; onOpenCall: (id: number) => void }) {
  return (
    <div className="diff mono" style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 4 }}>
      {changes.map((c, i) =>
        c.changes.map((d, j) => (
          <div key={`${i}-${j}`}>
            <span className={d.op}>{changeText("", d)}</span>{" "}
            <button className="link" onClick={() => onOpenCall(c.call_after)}>seq {c.seq_before}→{c.seq_after}</button>
          </div>
        )),
      )}
    </div>
  );
}

export function ValueInspector(props: { run: string; binding: Binding; onBack: () => void; onOpenCall: (id: number) => void; onFollow: (oid: number) => void }) {
  const { run, binding, onBack, onOpenCall, onFollow } = props;
  const value = useLoad<Value>(() => api.value(run, binding.value_id), [run, binding.value_id]);
  const flow = useLoad<ObjectFlow | undefined>(
    async () => (binding.object_id != null ? api.object(run, binding.object_id) : undefined),
    [run, binding.object_id],
  );
  return (
    <>
      <button className="back" onClick={onBack}>← back to call</button>
      <div className="chip" style={{ background: "none", padding: 0, gap: 8 }}>
        <span className="title-lg">{binding.name ?? binding.kind}</span>
        {binding.object_id != null && <span className="oid">#{binding.object_id}</span>}
        <span className="mono muted" style={{ fontSize: 11 }}>{binding.type}</span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>{binding.kind} value{binding.seq != null ? ` · seq ${binding.seq}` : ""}</div>
      <Section title="Value" className="box">
        {value ? <div className="vtree"><ValueNode label={binding.name ?? "value"} d={value.data} depth={0} onFollow={onFollow} /></div> : <span className="muted">Loading…</span>}
      </Section>
      {binding.object_id != null && (
        <button className="link" onClick={() => onFollow(binding.object_id!)}>Follow #{binding.object_id} in Data Flow →</button>
      )}
      {flow && (
        <Section title={`History · ${flow.total} appearances`}>
          {flow.appearances.map((a, i) => (
            <button key={i} className="binding" onClick={() => onOpenCall(a.call_id)}>
              <span className="k">{a.seq}</span>
              <span className="v">{a.path ? `in ${a.name ?? a.role}${a.path}` : a.role} {a.role === "return" ? "←" : "→"} {a.qualname}</span>
            </button>
          ))}
        </Section>
      )}
      {flow && flow.changes.length > 0 && (
        <Section title="Changes" className="box warn">
          <ChangeList changes={flow.changes} onOpenCall={onOpenCall} />
        </Section>
      )}
      {flow && (
        <div className="box dashed muted" style={{ fontSize: 12 }}>
          ┄ Appearances are observed bindings. A change is attributed to the calls between two appearances (inferred).
        </div>
      )}
    </>
  );
}

// -- data flow ----------------------------------------------------------------------------------------------------

/** The data before or after the followed object, one call away: what it was made from, what it became. */
function Hops({ title, verb, hops, onOpenCall, onFollow }: {
  title: string; verb: string; hops: Hop[]; onOpenCall: (id: number) => void; onFollow: (oid: number) => void;
}) {
  if (!hops.length) return null;
  return (
    <Section title={`${title} · inferred`}>
      {hops.slice(0, 8).map((h) => (
        <div key={`${h.call_id}-${h.object_id}`} className="hop">
          <button className="binding" disabled={h.object_id == null} onClick={() => h.object_id != null && onFollow(h.object_id)}
            title={h.object_id != null ? "Follow this object" : "A value without identity: nothing to follow"}>
            <span className="k">{h.name ?? shortName(h.type)}</span>
            <span className="v">{h.object_id != null && <span className="oid">#{h.object_id}</span>} {h.preview}</span>
          </button>
          <button className="link muted" onClick={() => onOpenCall(h.call_id)}>{verb} {shortName(h.qualname)}</button>
        </div>
      ))}
    </Section>
  );
}

export function ObjectInspector(props: { run: string; flow: ObjectFlow; onOpenCall: (id: number) => void; onFollow: (oid: number) => void }) {
  const { run, flow, onOpenCall, onFollow } = props;
  const others = useLoad<ObjectSummary[]>(() => api.objects(run), [run]);
  const steps = flow.appearances;
  const first = steps[0], last = steps[steps.length - 1];
  const calls = new Set(steps.map((a) => a.call_id)).size;
  const modules = new Set(steps.map((a) => a.module)).size;
  const fields = new Set(flow.changes.flatMap((c) => c.changes.map((d) => d.path))).size;
  const nested = [...new Set(steps.filter((a) => a.path).map((a) => `${a.name ?? a.role}${a.path}`))];
  return (
    <>
      <div className="chip" style={{ background: "none", padding: 0, gap: 8 }}>
        <span className="title-lg">{flow.name ?? shortName(flow.type ?? "object")}</span>
        <span className="oid">#{flow.object_id}</span>
        <span className="mono muted" style={{ fontSize: 11 }}>{flow.type}</span>
      </div>
      {first && (
        <Section title="Lifecycle">
          <KV rows={[
            ["first seen", <button className="link" onClick={() => onOpenCall(first.call_id)}>{shortName(first.qualname)} · seq {first.seq}</button>],
            ["appearances", `${flow.total} · ${calls} calls · ${modules} modules`],
            ["changes", flow.changes.length ? <span className="unfinished">{fields} fields in {flow.changes.length} steps</span> : "none observed"],
            ["last seen", <button className="link" onClick={() => onOpenCall(last.call_id)}>{last.role} of {shortName(last.qualname)}</button>],
            ...(nested.length ? [["nested in", nested.slice(0, 3).join(", ")] as [string, string]] : []),
          ]} />
          {flow.total > steps.length && <div className="muted" style={{ fontSize: 12 }}>Showing the first {steps.length}.</div>}
        </Section>
      )}
      {flow.changes.length > 0 && (
        <Section title="Changes" className="box warn">
          <ChangeList changes={flow.changes} onOpenCall={onOpenCall} />
        </Section>
      )}
      <Hops title="Made from" verb="passed to" hops={flow.made_from} onOpenCall={onOpenCall} onFollow={onFollow} />
      <Hops title="Became" verb="returned by" hops={flow.became} onOpenCall={onOpenCall} onFollow={onFollow} />
      {others && others.length > 1 && (
        <Section title="Other objects in this run">
          {others.filter((o) => o.object_id !== flow.object_id).slice(0, 8).map((o) => (
            <button key={o.object_id} className="binding" onClick={() => onFollow(o.object_id)}>
              <span className="k">{o.name ?? shortName(o.type)}</span>
              <span className="v"><span className="oid">#{o.object_id}</span> <span className="muted">{o.count} bindings</span></span>
            </button>
          ))}
        </Section>
      )}
      <div className="box dashed muted" style={{ fontSize: 12 }}>
        ┄ Steps are observed bindings, including the object nested inside another value. A change is attributed to
        the calls between two steps (inferred).
      </div>
    </>
  );
}
