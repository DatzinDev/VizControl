import { DiffCall, RunDiff, RunSummary } from "./api";
import { changeText, ms, outcomeIcon, runLabel, shortName } from "./format";
import { KV, Section } from "./Inspector";

type Open = (side: "a" | "b", callId: number) => void;

function Card({ c, side, only, onOpen }: { c: DiffCall; side: "a" | "b"; only?: boolean; onOpen: Open }) {
  const raised = c.outcome === "raised";
  return (
    <button className={`diff-card ${raised ? "raised" : ""}`} onClick={() => onOpen(side, c.id)} title={`${c.qualname}\nopen this call in run ${side.toUpperCase()}`}>
      <span className="q">{raised && <span className="raised">⚠ </span>}{shortName(c.qualname)}</span>
      <span className="muted">
        {only ? `${side === "a" ? "− only in A" : "+ only in B"}${c.call_line ? ` · line ${c.call_line}` : ""} · ${ms(c.duration_ns)}` : c.module}
      </span>
    </button>
  );
}

/** Diff (S7): run A and run B side by side, their common path down to where they split, and the split. */
export function DiffView({ diff, a, b, onOpen }: { diff: RunDiff; a: RunSummary; b: RunSummary; onOpen: Open }) {
  const d = diff.divergence;
  return (
    <div className="diff-view">
      <div className="diff-row head">
        <span className="chip outline"><b className="accent">A</b><span className="mono">{runLabel(a)}</span><span className={a.outcome}>{a.outcome}</span></span>
        <span />
        <span className="chip outline"><b className="raised">B</b><span className="mono">{runLabel(b)}</span><span className={b.outcome}>{b.outcome}</span></span>
      </div>
      {!d && <div className="muted">The two runs made the same calls{diff.summary.truncated ? " (as far as compared)" : ""}.</div>}
      {d && d.path.map((p, i) => (
        <div key={i} className="diff-row" style={{ "--d": p.a.depth } as React.CSSProperties}>
          <Card c={p.a} side="a" onOpen={onOpen} />
          <span className="muted mono">=</span>
          <Card c={p.b} side="b" onOpen={onOpen} />
        </div>
      ))}
      {d && (
        <>
          <div className="diff-split" style={{ "--d": (d.path[d.path.length - 1]?.a.depth ?? -1) + 1 } as React.CSSProperties}>
            <div className="col">
              {d.only_a.map((c) => <Card key={c.id} c={c} side="a" only onOpen={onOpen} />)}
              {!d.only_a.length && <span className="muted">no further calls here</span>}
            </div>
            <span />
            <div className="col">
              {d.only_b.map((c) => <Card key={c.id} c={c} side="b" only onOpen={onOpen} />)}
              {!d.only_b.length && <span className="muted">no further calls here</span>}
            </div>
          </div>
          <div className="diff-badge">◆ First divergence · children of {d.parent ? shortName(d.parent.qualname) : "the run"}</div>
        </>
      )}
      {diff.returns.length > 0 && (
        <>
          <div className="label" style={{ marginTop: 14 }}>Same call, different result · {diff.summary.returns_differ} · in the order they returned</div>
          {diff.returns.map((r) => (
            <div key={r.a} className="diff-row">
              <button className="diff-card" onClick={() => onOpen("a", r.a)} title={`${r.qualname}\nopen this call in run A`}>
                <span className="q">{shortName(r.qualname)}</span><span className="mono">{r.a_value}</span>
              </button>
              <span className="muted mono">≠</span>
              <button className="diff-card" onClick={() => onOpen("b", r.b)} title={`${r.qualname}\nopen this call in run B`}>
                <span className="q">{shortName(r.qualname)}</span><span className="mono">{r.b_value}</span>
              </button>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/** Which run to compare the current one with: same target first. */
export function DiffPicker({ runs, current, onPick }: { runs: RunSummary[]; current: RunSummary; onPick: (id: string) => void }) {
  const others = runs.filter((r) => r.id !== current.id && !r.error)
    .sort((x, y) => Number(y.target === current.target) - Number(x.target === current.target));
  return (
    <div className="picker">
      <div className="label">Compare {runLabel(current)} (A) with…</div>
      {others.map((r) => (
        <button key={r.id} className="binding" onClick={() => onPick(r.id)} title={r.id}>
          <span className={r.outcome}>{outcomeIcon[r.outcome]}</span>
          <span className="v">{runLabel(r)}</span>
          <span className="k">{r.target === current.target ? "same target" : r.target}</span>
        </button>
      ))}
      {!others.length && <div className="muted">No other run to compare with.</div>}
    </div>
  );
}

export function DiffInspector({ diff, onOpen }: { diff: RunDiff; onOpen: Open }) {
  const s = diff.summary;
  const d = diff.divergence;
  const calls = (cs: { qualname: string; call_line?: number | null }[]) =>
    cs.length ? cs.map((c) => `${shortName(c.qualname)}${c.call_line ? ` (line ${c.call_line})` : ""}`).join(", ") : "nothing more";
  return (
    <>
      <div className="title-lg">A ⇄ B</div>
      <Section title="Summary">
        <KV rows={[["matched", `${s.matched.toLocaleString()} calls`], ["only in A", `${s.only_a.toLocaleString()} calls`],
          ["only in B", `${s.only_b.toLocaleString()} calls`], ["different result", `${s.returns_differ.toLocaleString()} calls`],
          ["outcome", <span><span className={s.outcome_a}>{s.outcome_a}</span> ⇄ <span className={s.outcome_b}>{s.outcome_b}</span></span>]]} />
        {s.truncated && <div className="muted" style={{ fontSize: 12 }}>Counts cover the first 5,000 calls after the divergence.</div>}
      </Section>
      {d && (
        <Section title="First divergence" className="box warn">
          <KV rows={[
            ["parent", d.parent_a != null && d.parent ? <button className="link" onClick={() => onOpen("a", d.parent_a!)}>{d.parent.qualname}{d.parent.file ? ` · ${d.parent.file}:${d.parent.line}` : ""}</button> : "the run itself"],
            ["A calls", calls(d.only_a)],
            ["B calls", calls(d.only_b)],
          ]} />
        </Section>
      )}
      {d && d.args.length > 0 && (
        <Section title={`Argument diff of ${d.parent ? shortName(d.parent.qualname) : "parent"}`}>
          <div className="diff mono" style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 3 }}>
            {d.args.map((x) =>
              x.changes.length
                ? x.changes.map((c, i) => <div key={`${x.name}${i}`} className={c.op}>~ {changeText(x.name, c)}</div>)
                : <div key={x.name} className="muted">  {x.name} {x.same ? `= ${x.a}` : `${x.a ?? "—"} → ${x.b ?? "—"}`}</div>,
            )}
          </div>
        </Section>
      )}
      {diff.returns.length > 0 && (
        <Section title={`First different result · ${shortName(diff.returns[0].qualname)}`}>
          <div className="diff mono" style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 3 }}>
            {diff.returns[0].changes.map((c, i) => <div key={i} className={c.op}>~ {changeText("return", c)}</div>)}
          </div>
          <button className="link" onClick={() => onOpen("a", diff.returns[0].a)}>Open it in A →</button>
        </Section>
      )}
      {d?.branch && (
        <Section title={`Branch · ${d.branch.file}`} className="box">
          <pre className="source">
            {d.branch.lines.map((text, i) => {
              const n = d.branch!.first + i;
              const mark = d.branch!.b_lines.includes(n) ? "# B" : d.branch!.a_lines.includes(n) ? "# A" : "";
              return (
                <span key={n} className={mark ? "hl" : undefined}>
                  <span className="ln">{n}</span>{text}{mark && <span className="muted">   {mark}</span>}{"\n"}
                </span>
              );
            })}
          </pre>
        </Section>
      )}
      <div className="box dashed muted" style={{ fontSize: 12 }}>
        ┄ How it was found: both runs' calls in order, as (depth, module, function), aligned; the first block that differs
        is the divergence. Outcomes are not compared: a failing run's callers all raise and are still the same calls.
        Results are compared on the aligned calls, object ids aside.
      </div>
    </>
  );
}
