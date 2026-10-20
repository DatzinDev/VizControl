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
  error?: string;
}

export interface StageNode {
  name: string;
  calls: number;
  total_ns: number;
  self_ns: number;
  raised: number;
  unfinished: number;
  first_seq: number;
  first_call: number;
  observed: boolean;
  modules: string[];
}

export interface StageEdge {
  from: string;
  to: string;
  calls: number;
  total_ns: number;
  first_seq: number;
}

export interface StageGraph {
  nodes: StageNode[];
  edges: StageEdge[];
  main_path: string[];
}

export interface StageFunction {
  code_id: number;
  qualname: string;
  cls: string | null;
  module: string | null;
  file: string | null;
  kind: string;
  observed: boolean;
  calls: number;
  total_ns: number;
  self_ns: number;
  raised: number;
  unfinished: number;
  first_call: number;
  first_seq: number;
}

/** Level 2: one stage opened into module › class › function. */
export interface StageDetail {
  stage: string;
  observed: boolean;
  functions: StageFunction[];
  edges: { from: number; to: number; calls: number }[];
  incoming: { stage: string | null; from_code: number | null; from_qualname: string | null; to: number; calls: number }[];
  outgoing: { from: number; stage: string; calls: number }[];
  stats: { modules: number; classes: number; functions: number; calls: number; total_ns: number; self_ns: number };
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
  items?: TreeItem[];
}

export interface GroupNode {
  type: "group";
  parent_id: number | null;
  code_id: number;
  qualname: string;
  module: string | null;
  kind: string;
  observed: boolean;
  count: number;
  outcomes: { ok: number; raised: number; unfinished: number };
  total_ns: number;
  first_call_id: number;
  seq: number;
  stage: string | null;
}

export interface MoreNode {
  type: "more";
  parent_id: number | null;
  remaining: number;
}

export type TreeItem = CallNode | GroupNode | MoreNode;

export interface ExceptionRow {
  id: number;
  type: string;
  message: string;
  raised_call_id: number | null;
  raised_line: number | null;
  handled_call_id: number | null;
  traceback: string;
}

export interface CallDetail extends CallNode {
  file_id: number | null;
  caller: { id: number; qualname: string; file: string | null; line: number | null } | null;
  exception?: ExceptionRow;
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
}

export interface ObjectSummary {
  object_id: number;
  type: string;
  name: string | null;
  count: number;
  first_seq: number;
}

export interface Hit {
  kind: "function" | "variable" | "value";
  label: string;
  call_id: number;
  module?: string;
  count?: number;
  preview?: string;
  role?: string;
}

export interface Source {
  file_id: number;
  path: string;
  relpath: string | null;
  source: string | null;
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
  stages: (r: string) => get<StageGraph>(`${run(r)}/stages`),
  stage: (r: string, name: string) => get<StageDetail>(`${run(r)}/stage?name=${encodeURIComponent(name)}`),
  tree: (r: string, node: number | null) => get<TreeItem[]>(`${run(r)}/tree${node == null ? "" : `?node=${node}`}`),
  group: (r: string, node: number | null, code: number, offset: number, limit: number) =>
    get<CallNode[]>(`${run(r)}/tree?${node == null ? "" : `node=${node}&`}code=${code}&offset=${offset}&limit=${limit}`),
  call: (r: string, id: number) => get<CallDetail>(`${run(r)}/calls/${id}`),
  bindings: (r: string, id: number) => get<Binding[]>(`${run(r)}/calls/${id}/bindings`),
  path: (r: string, id: number) => get<CallNode[]>(`${run(r)}/calls/${id}/path`),
  value: (r: string, id: number) => get<Value>(`${run(r)}/values/${id}`),
  object: (r: string, oid: number) => get<ObjectFlow>(`${run(r)}/objects/${oid}`),
  objects: (r: string) => get<ObjectSummary[]>(`${run(r)}/objects`),
  error: (r: string) => get<ErrorPath | null>(`${run(r)}/error`),
  source: (r: string, fileId: number) => get<Source>(`${run(r)}/source/${fileId}`),
  search: (r: string, q: string) => get<Hit[]>(`${run(r)}/search?q=${encodeURIComponent(q)}`),
};
