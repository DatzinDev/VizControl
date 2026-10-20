import { useEffect, useState } from "react";
import { api, Hit } from "./api";

export function SearchPalette({ run, onPick, onClose }: { run: string; onPick: (callId: number) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => {
      if (!q.trim()) return setHits([]);
      api.search(run, q).then((h) => {
        setHits(h);
        setActive(0);
      });
    }, 150);
    return () => clearTimeout(t);
  }, [q, run]);

  const pick = (h: Hit | undefined) => {
    if (h) onPick(h.call_id);
    onClose();
  };

  return (
    <div className="palette-bg" onMouseDown={onClose}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <input
          autoFocus
          placeholder="Search functions, variables, values…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, hits.length - 1));
            if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
            if (e.key === "Enter") pick(hits[active]);
          }}
        />
        <ul>
          {hits.map((h, i) => (
            <li key={i}>
              <button className={i === active ? "active" : ""} onClick={() => pick(h)} onMouseEnter={() => setActive(i)}>
                <span className="label" style={{ width: 72, flexShrink: 0 }}>{h.kind}</span>
                <span className="mono">{h.label}</span>
                <span className="muted mono" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {h.kind === "function" ? `${h.module ?? ""} · ${h.count} call${h.count === 1 ? "" : "s"}` : h.preview}
                </span>
              </button>
            </li>
          ))}
          {q.trim() && !hits.length && <li className="muted" style={{ padding: 10 }}>No matches</li>}
        </ul>
      </div>
    </div>
  );
}
