export function ms(ns: number | null | undefined, approx = false): string {
  if (ns == null) return "—";
  const v = ns / 1e6;
  const s = v >= 1000 ? `${(v / 1000).toFixed(2)} s` : v >= 10 ? `${v.toFixed(1)} ms` : v >= 0.01 ? `${v.toFixed(2)} ms` : `${(ns / 1e3).toFixed(1)} µs`;
  return approx ? `~${s}` : s;
}

/** The last segment of a qualname, keeping the class: "OrderService.place_order" stays, "a.<locals>.f" -> "f". */
export function shortName(qualname: string): string {
  const parts = qualname.split(".").filter((p) => p !== "<locals>");
  return parts.slice(-2).join(".");
}

export const outcomeIcon: Record<string, string> = {
  ok: "●",
  raised: "⚠",
  unfinished: "◌",
  running: "◌",
  assertion_failed: "⚠",
};

/** "checkout.py vip": what was run, as typed. */
export function runLabel(run: { target: string; args?: string[] }): string {
  return [run.target, ...(run.args ?? [])].join(" ");
}

/** One field-level change as a line: "+ x = 1", "− x", "x 1 → 2". */
export function changeText(name: string, d: { path: string; op: string; before?: string; after?: string }): string {
  const field = `${name}${d.path === "." ? "" : d.path}`;
  if (d.op === "added") return `+ ${field} = ${d.after}`;
  if (d.op === "removed") return `− ${field} (was ${d.before})`;
  if (d.op === "unknown") return `? ${field} (not fully captured)`;
  return `${field} ${d.before} → ${d.after}`;
}
