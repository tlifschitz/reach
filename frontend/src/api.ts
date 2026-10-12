// Types and helpers for the Reach HTTP API.

export interface Issue {
  severity: "error" | "warning";
  file: string;
  message: string;
  line: number | null;
  field: string | null;
}

export interface Placement {
  wall: string;
  offset_cm: number;
  width_cm: number;
  bottom_cm: number;
  top_cm: number;
}

export interface Wall {
  id: string;
  name: string;
  start: [number, number];
  end: [number, number];
  length: number;
}

export interface Poi {
  id: string;
  type: string;
  name: string;
  position: [number, number];
  placement: Placement | null;
}

export interface Zone extends Poi {
  size: [number, number] | null;
}

export interface CompartmentInfo {
  id: string;
  type: string;
  name: string;
  access_point: [number, number];
  placement: Placement;
  level: number;
  level_override: boolean;
  volume_l: number;
  capacity_l: number;
  max_weight_kg: number | null;
  tags: string[];
}

export interface ObstructionInfo {
  id: string;
  name: string;
  default_state: "open" | "closed";
  blocks_when: "open" | "closed";
  blocks: string[];
  wall_range: { wall: string; from_cm: number; to_cm: number } | null;
  clear: { time_s: number; openings: number };
  restore: { time_s: number; openings: number };
}

export interface Layout {
  name: string;
  outline: [number, number][];
  walls: Wall[];
  pois: Poi[];
  zones: Zone[];
  compartments: CompartmentInfo[];
  obstructions: ObstructionInfo[];
}

export interface ItemInfo {
  id: string;
  name: string;
  category: string;
  volume_l: number;
  quantity: number;
  weight_kg: number;
  estimated_frequency: number;
  derived_frequency: number;
  nest_group: string | null;
  tags: string[];
  comments: string;
  pinned: string | null;
  pin_source: "file" | "ui" | null;
}

export type StepYaml = Record<string, string>;

export interface RoutineYaml {
  id: string;
  name: string;
  description?: string;
  frequency_per_week: number;
  start: string | [number, number];
  initial_states?: Record<string, string>;
  steps: StepYaml[];
}

export interface Project {
  loaded: boolean;
  version: string;
  source?: "sample" | "upload";
  ok?: boolean;
  feasible?: boolean;
  infeasible?: string[];
  issues?: Issue[];
  files?: string[];
  layout?: Layout;
  items?: ItemInfo[];
  routines?: RoutineYaml[];
  constraints?: { hard: Record<string, unknown>[]; soft: Record<string, unknown>[] };
  has_current?: boolean;
  has_result?: boolean;
  ignored_files?: string[];
  llm: { available: boolean; reason: string };
}

export const METRICS = ["distance", "openings", "time", "effort", "extra_trips", "clearings"] as const;
export type Metric = (typeof METRICS)[number];
export type Metrics = Record<Metric, number>;

export const METRIC_LABEL: Record<Metric, string> = {
  distance: "Distance",
  openings: "Openings",
  time: "Time",
  effort: "Effort",
  extra_trips: "Extra trips",
  clearings: "Door clearings",
};
export const METRIC_UNIT: Record<Metric, string> = {
  distance: "m",
  openings: "",
  time: "min",
  effort: "",
  extra_trips: "",
  clearings: "",
};
const METRIC_SCALE: Partial<Record<Metric, number>> = { distance: 0.01, time: 1 / 60 };

export function fmtMetric(metric: Metric, value: number, digits = 1): string {
  const v = value * (METRIC_SCALE[metric] ?? 1);
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function fmtPct(change: number | null | undefined): string {
  if (change === null || change === undefined) return "–";
  const pct = Math.round(change * 100);
  return `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct)}%`;
}

export interface RoutineEval {
  id: string;
  name: string;
  frequency: number;
  metrics: Metrics;
  weekly: Metrics;
  cost: number;
}

export interface LocationRow {
  id: string;
  name: string;
  kind: string;
  level: number;
  assignable: boolean;
  items: { id: string; name: string; quantity: number; pinned: boolean }[];
  volume: number;
  capacity_volume: number | null;
  fill: number | null;
  weight: number;
  capacity_weight: number | null;
}

export interface Violation {
  kind: string;
  message: string;
  item: string | null;
  location: string | null;
  constraint: string | null;
}

export interface Report {
  assignment: Record<string, string>;
  evaluation: {
    cost: number;
    soft_total: number;
    soft: Record<string, number>;
    totals: Metrics;
    routine_totals: Metrics;
    occasional: { weekly: Metrics; cost: number; items: number; share: number };
    normalizers: Metrics;
    routines: RoutineEval[];
  };
  locations: LocationRow[];
  violations: Violation[];
  source?: string;
}

export interface Comparison {
  cost_before: number;
  cost_after: number;
  cost_change: number | null;
  totals: Record<Metric, { before: number; after: number; change: number | null }>;
  occasional: {
    items: number;
    before: Metrics;
    after: Metrics;
    cost_before: number;
    cost_after: number;
    cost_change: number | null;
    share: number;
  };
  routines: {
    id: string;
    name: string;
    frequency: number;
    before: Metrics;
    after: Metrics;
    cost_before: number;
    cost_after: number;
    cost_change: number | null;
  }[];
  moved: { item: string; from: string; to: string }[];
}

export interface Result extends Partial<Report> {
  present: boolean;
  cost?: number;
  greedy_cost?: number;
  iterations?: number;
  elapsed_s?: number;
  stopped_early?: boolean;
  seed?: number;
  stale?: boolean;
  history?: [number, number][];
  vs_greedy?: Comparison;
  vs_current?: Comparison;
  current_violations?: Violation[];
}

export interface JobSnapshot {
  id: string;
  kind: string;
  status: "running" | "done" | "stopped" | "error";
  progress: {
    iteration?: number;
    total_iterations?: number | null;
    elapsed_s?: number;
    fraction?: number;
    current_cost?: number;
    best_cost?: number;
    initial_cost?: number;
    done?: number;
    total?: number;
  };
  history: [number, number][];
  error: string | null;
  version: number;
}

export interface TraceEvent {
  step: number;
  kind: "start" | "move" | "open" | "handle" | "clear" | "restore" | "extra_trip";
  x: number;
  y: number;
  t: number;
  location?: string;
  obstruction?: string;
  label?: string;
}

export interface Trace {
  routine: string;
  name: string;
  steps: string[];
  metrics: Metrics;
  events: TraceEvent[];
}

export interface Shares {
  exact: number;
  area: number;
  modal_location: string;
  modal_share: number;
  locations: Record<string, number>;
}

export interface Stability {
  item: string;
  name: string;
  baseline_location: string;
  baseline_area: string;
  label: "robust" | "noisy" | "sensitive" | "unused" | "pinned";
  noise: Shares | null;
  perturbed: Shares | null;
}

export interface RunSet {
  runs: number;
  costs: number[];
  summary: Record<string, number>;
  exact_stability: number;
  area_stability: number;
}

export interface Sensitivity {
  present: boolean;
  perturbation?: number;
  iterations?: number;
  restarts?: number;
  robust_threshold?: number;
  baseline_cost?: number;
  noise?: RunSet | null;
  perturbed?: RunSet | null;
  baseline_costs?: number[];
  items?: Stability[];
  counts?: Record<string, number>;
}

export interface Coverage {
  covered: number;
  total: number;
  unused_items: { id: string; name: string; category: string }[];
  findings: { routine: string; severity: string; message: string; item: string | null }[];
  frequencies: { item: string; name: string; estimated: number; derived: number; flag: string; ratio: number | null }[];
}

export interface RoutineProposal {
  routine: RoutineYaml;
  yaml: string;
  issues: string[];
}

export interface RoutinesProposal {
  routines: RoutineProposal[];
  missing_items: { name: string; reason: string }[];
  notes: string;
}

export interface DiaryProposal {
  recorded_days: number;
  activities: {
    routine_id: string;
    name: string;
    existing: boolean;
    occurrences: number;
    days: string[];
    frequency_per_week: number;
    previous_frequency: number | null;
    entries: string[];
    proposal: RoutineProposal | null;
  }[];
  unclear: { line: number; text: string; reason: string; date?: string | null }[];
  missing_items: { name: string; reason: string }[];
}

export interface ConstraintsProposal {
  constraints: { hard: Record<string, unknown>[]; soft: Record<string, unknown>[] };
  yaml: string;
  rationale: Record<string, string>;
  rejected: { constraint: Record<string, unknown>; problems: string[]; rationale: string }[];
  ignored_comments: string[];
}

export class ApiError extends Error {
  issues: string[];
  constructor(message: string, issues: string[] = []) {
    super(message);
    this.issues = issues;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError("Cannot reach the Reach server. Is it running?");
  }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let issues: string[] = [];
    try {
      const detail = (await response.json()).detail;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail === "object") {
        message = detail.message ?? message;
        issues = detail.issues ?? detail.reasons ?? [];
      }
    } catch {
      // keep the status text
    }
    throw new ApiError(message, issues);
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
  project: () => request<Project>("/api/project"),
  loadSample: () => post<Project>("/api/project/sample"),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    return request<Project>("/api/project/upload", { method: "POST", body: form });
  },
  close: () => request<Project>("/api/project", { method: "DELETE" }),
  evaluate: (source: string) => request<Report>(`/api/evaluate?source=${source}`),
  trace: (routine: string, source: string) =>
    request<Trace>(`/api/trace?routine=${encodeURIComponent(routine)}&source=${source}`),
  result: () => request<Result>("/api/result"),
  optimize: (body: { seed: number; iterations?: number; time_limit_s?: number | null; restarts?: number; start: string }) =>
    post<JobSnapshot>("/api/optimize", body),
  stop: (id: string) => post<JobSnapshot>(`/api/jobs/${id}/stop`),
  pin: (item: string, location: string | null) => post<Project>("/api/pins", { item, location }),
  resetPins: () => request<Project>("/api/pins", { method: "DELETE" }),
  startSensitivity: (body: { runs: number; noise_runs: number; perturbation: number; seed: number; iterations: number; restarts: number }) =>
    post<JobSnapshot>("/api/sensitivity", body),
  sensitivity: () => request<Sensitivity>("/api/sensitivity"),
  coverage: () => request<Coverage>("/api/coverage"),
  validateRoutine: (yaml: string) =>
    post<{ ok: boolean; issues: string[]; routine: RoutineYaml | null; yaml: string | null }>(
      "/api/routines/validate",
      { yaml },
    ),
  saveRoutine: (yaml: string) => post<Project>("/api/routines", { yaml }),
  deleteRoutine: (id: string) => request<Project>(`/api/routines/${id}`, { method: "DELETE" }),
  setFrequencies: (frequencies: Record<string, number>) =>
    post<Project>("/api/routines/frequencies", { frequencies }),
  addConstraints: (body: { hard: unknown[]; soft: unknown[] }) => post<Project>("/api/constraints", body),
  llmRoutine: (text: string, url: string) => post<RoutinesProposal>("/api/llm/routine", { text, url: url || null }),
  llmCover: () => post<RoutinesProposal>("/api/llm/cover"),
  llmDiary: (text: string) => post<DiaryProposal>("/api/llm/diary", { text }),
  llmConstraints: () => post<ConstraintsProposal>("/api/llm/constraints"),
  parseDiary: (text: string) =>
    post<{ days: string[]; entries: { id: string; date: string | null; text: string }[]; problems: unknown[] }>(
      "/api/diary/parse",
      { text },
    ),
};

/** Follow a job over Server-Sent Events until it finishes. Returns a function that stops listening. */
export function watchJob(id: string, onUpdate: (job: JobSnapshot) => void): () => void {
  const source = new EventSource(`/api/jobs/${id}/events`);
  source.onmessage = (event) => {
    const job = JSON.parse(event.data) as JobSnapshot;
    onUpdate(job);
    if (job.status !== "running") source.close();
  };
  source.onerror = () => source.close();
  return () => source.close();
}

export function routineToYaml(r: RoutineYaml): string {
  const lines = [`- id: ${r.id}`, `  name: ${r.name}`];
  if (r.description) lines.push(`  description: ${r.description}`);
  lines.push(`  frequency_per_week: ${r.frequency_per_week}`);
  lines.push(`  start: ${typeof r.start === "string" ? r.start : `[${r.start.join(", ")}]`}`);
  if (r.initial_states && Object.keys(r.initial_states).length) {
    lines.push("  initial_states:");
    for (const [k, v] of Object.entries(r.initial_states)) lines.push(`    ${k}: ${v}`);
  }
  lines.push("  steps:");
  for (const step of r.steps) {
    const [kind, value] = Object.entries(step)[0];
    lines.push(`    - ${kind}: ${value}`);
  }
  return lines.join("\n") + "\n";
}

export function describeStep(step: StepYaml): string {
  const [kind, value] = Object.entries(step)[0];
  return `${kind.replace("_", " ")} ${value}`;
}
