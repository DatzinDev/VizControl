// Typed client for the `vizcontrol serve` JSON API (docs/components/server.md).

export type Outcome = "ok" | "raised" | "unfinished" | "running" | "assertion_failed" | "skipped";

export interface RunSummary {
  id: string;
  kind: string;
  target: string;
  args: string[];
  outcome: Outcome;
  exit_code: number | null;
  started: string;
  duration_ns: number | null;
  calls: number;
  truncated: string[];
  level: string;
  max_depth: number | null;
  cut_calls: number[];  // where max_calls stopped tracing
  process_gone: boolean;
  http_status: string | null;  // a run of one HTTP request: its response status
  regions: { id: number; name: string; call_id: number | null; seq_start: number; seq_end: number }[];  // nested trace() blocks  // never closed and its process no longer exists: killed (inferred)
  error?: string;
}

export interface CallNode {
  type: "call";
  id: number;
  parent_id: number | null;
  code_id: number;
  qualname: string;
  module: string | null;
  kind: string;
  observed: boolean;
  outcome: Outcome;
  start_ns: number;
  duration_ns: number | null;
  end_exact: boolean;
  children: number;
  depth: number;
  seq: number;
  call_line: number | null;
  exception_id: number | null;
  thread_id: number;
  file: string | null;
  first_line: number | null;
  stage: string | null;
}

export interface Binding {
  name: string | null;
  kind: string;
  line?: number | null;
  seq?: number;
  value_id: number;
  object_id: number | null;
  type: string;
  preview: string;
}

export type Enc = { t: string; [key: string]: any };

export interface Value {
  id: number;
  object_id: number | null;
  type: string;
  data: Enc;
  truncated: number;
  redacted: number;
}

export interface Diff {
  path: string;
  op: "added" | "removed" | "changed" | "unknown";
  before?: string;
  after?: string;
}

export interface Change {
  seq_before: number;
  seq_after: number;
  call_before: number;
  call_after: number;
  changes: Diff[];
  name?: string;
  object_id?: number;
}

export interface ErrorPath {
  exception: { id: number; type: string; message: string; raised_line: number; value_id: number | null };
  traceback: [string, number, string][];
  origin: CallNode;
  path: CallNode[];
  propagation: number[];
  locals: Binding[];
  recent_changes: Change[];
  chain: { id: number; type: string; message: string }[];
}

export interface Appearance {
  call_id: number;
  qualname: string;
  module: string | null;
  seq: number;
  role: string;
  name: string | null;
  path: string;  // where the object sits inside the bound value: "" (the value itself), ".customer", "[0]"
  value_id: number;
  type: string;
  preview: string;
  fields: [string, string][];
  changed: string[];  // fields that differ from the previous full appearance
}

export interface ObjectFlow {
  object_id: number;
  type: string | null;
  name: string | null;
  total: number;
  appearances: Appearance[];
  links: { from: number; to: number; seq: number; inferred: boolean }[];
  changes: Change[];
  became: Hop[];
  made_from: Hop[];
}

/** The next piece of data, in a call that took or returned the followed object (inferred). */
export interface Hop {
  call_id: number;
  qualname: string;
  name: string | null;
  object_id: number | null;
  type: string;
  preview: string;
  inferred: boolean;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.message || `${res.status} ${path}`);
  }
  return res.json();
}

const run = (id: string) => `/api/runs/${encodeURIComponent(id)}`;

export const api = {
  runs: () => get<RunSummary[]>("/api/runs"),
  path: (r: string, id: number) => get<CallNode[]>(`${run(r)}/calls/${id}/path`),
  value: (r: string, id: number) => get<Value>(`${run(r)}/values/${id}`),
  object: (r: string, oid: number) => get<ObjectFlow>(`${run(r)}/objects/${oid}`),
  error: (r: string) => get<ErrorPath | null>(`${run(r)}/error`),
  timeline: (r: string, at: { offset?: number; endOf?: number; seq?: number }, limit = 500) =>
    get<TimelinePage>(`${run(r)}/timeline?limit=${limit}&${at.endOf != null ? `end_of=${at.endOf}`
      : at.seq != null ? `seq=${at.seq}` : `offset=${at.offset ?? 0}`}`),
  callgraph: (r: string) => get<CallGraph>(`${run(r)}/callgraph`),
  watch: (r: string, name: string) => get<Watched[]>(`${run(r)}/watch?name=${encodeURIComponent(name)}`),
};

/** A value bound at one replay step: an argument at a call, the result at a return, the locals where it raised. */
export interface StepValue {
  name: string | null;
  kind: string;
  preview: string;
  object_id: number | null;
  value_id: number;
  type: string;
}

/** One step of a replay: a call starting, returning or raising. */
export interface Step {
  i: number;
  seq: number;
  kind: "call" | "return" | "raise";
  call_id: number;
  parent_id: number | null;
  depth: number;
  code_id: number;
  qualname: string;
  module: string | null;
  observed: boolean;
  line: number | null;
  file: string | null;
  first_line: number | null;
  values: StepValue[];
  callback?: Callback;  // not called by its name (inferred)
  exception?: { type: string; message: string; origin: boolean; line: number | null } | null;
}

export interface TimelinePage {
  total: number;
  offset: number;
  steps: Step[];
  raises: number[];  // the steps where a call raised
  start: number;  // the first call that is not an import or a class body: where the program's work begins
}

export interface CallGraph {
  nodes: { code_id: number; qualname: string; module: string | null; kind: string; observed: boolean; calls: number;
    first_seq: number; raised: number }[];
  edges: { source: number; target: number; calls: number; first_seq: number; callback: Pick<Callback, "by" | "via" | "line"> | null }[];
}

/** One value a watched variable had, and when. */
export interface Watched {
  seq: number;
  call_id: number;
  qualname: string;
  kind: string;
  preview: string;
  object_id: number | null;
  value_id: number;
  type: string;
}

/** How a call happened when it was not by its name: a library called it back (`by`), or the caller ran a function
 * it held (`line`, the caller's source line; `via`, where among the caller's arguments it was; `registered`, the
 * first value it was seen in). */
export interface Callback {
  by?: string;
  line?: string;
  at?: number;
  via?: string;
  registered?: { call_id: number; qualname: string; seq: number; kind: string; path: string | null };
  inferred: boolean;
}
