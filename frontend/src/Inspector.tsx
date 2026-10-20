import { useEffect, useState } from "react";
import { api, Binding, Change, Enc, ObjectFlow, Value } from "./api";
import { changeText } from "./format";

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

export function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`section ${className ?? ""}`}>
      <div className="label">{title}</div>
      {children}
    </div>
  );
}

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

function ValueNode({ label, d, depth }: { label: string; d: Enc; depth: number }) {
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
        {d.oid != null && <span className="oid" title="The same object everywhere it appears">#{d.oid}</span>}
      </div>
      {open && kids.map(([k, v], i) => <ValueNode key={i} label={k} d={v} depth={depth + 1} />)}
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

/** A value opened from a replay step: its full tree, and the object's history across the run (each entry jumps
 * to that call). */
export function ValueInspector(props: { run: string; binding: Binding; onBack: () => void; onOpenCall: (id: number) => void }) {
  const { run, binding, onBack, onOpenCall } = props;
  const value = useLoad<Value>(() => api.value(run, binding.value_id), [run, binding.value_id]);
  const flow = useLoad<ObjectFlow | undefined>(
    async () => (binding.object_id != null ? api.object(run, binding.object_id) : undefined),
    [run, binding.object_id],
  );
  return (
    <>
      <button className="back" onClick={onBack}>← back to the step</button>
      <div className="chip" style={{ background: "none", padding: 0, gap: 8 }}>
        <span className="title-lg">{binding.name ?? binding.kind}</span>
        {binding.object_id != null && <span className="oid">#{binding.object_id}</span>}
        <span className="mono muted" style={{ fontSize: 11 }}>{binding.type}</span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>{binding.kind} value{binding.seq != null ? ` · seq ${binding.seq}` : ""}</div>
      <Section title="Value" className="box">
        {value ? <div className="vtree"><ValueNode label={binding.name ?? "value"} d={value.data} depth={0} /></div> : <span className="muted">Loading…</span>}
      </Section>
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
