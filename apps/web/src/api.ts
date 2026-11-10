// 与后端通信的薄封装。后端没起时 probe() 返回 false，界面回落本地演练模式。
export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  ((window as Window & { desktop?: { isDesktop: boolean; apiBase: string } }).desktop?.isDesktop
    ? (window as Window & { desktop?: { apiBase: string } }).desktop!.apiBase : 'http://localhost:8000');

const json = async <T>(r: Response): Promise<T> => {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as T;
};

export async function probe(): Promise<boolean> {
  try {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), 1500);
    const r = await fetch(`${API_BASE}/health`, { signal: c.signal });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}

export type ApiAgent = {
  k: string; name: string; ch: string; title: string; tier: string; color: string;
  level: number; kpi: string[]; perm: string[]; duty: string[]; stance: string;
};

export type ApiEvent = { id: string; at: string; k: string; text: string; kind: 'seal' | 'ok' | null };

export type ApiEvidence = {
  summary: string; metrics: string[]; reasons: string[]; risks: string[]; rollback: string; sources: string[];
};

export type ApiApproval = {
  id: string; title: string; from: string; summary: string; impact: string; cost: number;
  risk: 'red' | 'mid' | 'low'; status: 'pending' | 'approved' | 'rejected';
  evidence: ApiEvidence; created_at: string; demo_run_id?: string | null; approver?: string; discussion?: { question: string; answer: string; source: string; at: string }[];
};

export type ApiDashboard = { gmv: number; margin: number; refund_rate: number; pending: number; cost: number };

export type AnalysisAction = { text: string; owner: string; why: string };
export type ApiAnalysis = {
  source: 'model' | 'rules'; model: string; headline: string;
  findings: string[]; actions: AnalysisAction[]; error?: string; run_id?: string;
};

export type ApiStep = { text: string; at: string; status: 'wait' | 'running' | 'done' };
export type ApiTask = {
  id: string; title: string; kind: string; from: string; approval_id: string; risk: string;
  status: 'running' | 'done' | 'failed'; demo_run_id?: string | null; steps: ApiStep[]; snapshot: Record<string, unknown> | null;
  created_at: string; finished_at: string | null;
};

export type ApiModelStatus = {
  enabled: boolean; connected: boolean; model: string; base_url: string; models: string[];
  error: string; has_key?: boolean; key_hint?: string;
};

export type ApiAct = { from: string; text: string; risk: string; due: string };
export type ApiOpt = { v: string; label: string; eff: string; rec?: boolean; act: ApiAct };
export type ApiAgenda = {
  t: string; q: string; says: [string, string][];
  clash: { by: string[]; text: string } | null; opts: ApiOpt[];
};
export type ApiMeeting = {
  id: string; template: string; title: string; goal: string; who: string[]; agenda: ApiAgenda[];
  stage: 'ready' | 'run' | 'done' | 'cancelled'; idx: number; shown: number; phase: 'say' | 'pick';
  mins: number; cost: number; notes: ApiNote[]; res: ApiDecision[]; dispatched: boolean;
};
export type ApiNote = { id: string; topic: string; text: string; at: string; replies: [string, string][] };
export type ApiDecision = { topic: string; v: string; label: string; eff: string; act: ApiAct };
export type ApiAction = { id: string; from: string; text: string; due: string; risk: string; topic: string; meeting: string };

const post = (path: string, body?: unknown) =>
  fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });

export const api = {
  createMeeting: (template: string, who: string[], topic = '') =>
    post('/api/meetings', { template, who, topic }).then(json<ApiMeeting>),
  cancelMeeting: (id: string) => post(`/api/meetings/${id}/cancel`).then(json<ApiMeeting>),
  start: (id: string) => post(`/api/meetings/${id}/start`).then(json<ApiMeeting>),
  next: (id: string) =>
    post(`/api/meetings/${id}/next`).then(
      json<{ line: [string, string] | null; phase: 'say' | 'pick'; shown: number; idx: number; mins: number; cost: number }>
    ),
  say: (id: string, text: string) =>
    post(`/api/meetings/${id}/say`, { text }).then(json<{ note: ApiNote; mins: number; cost: number }>),
  decide: (id: string, v: string) =>
    post(`/api/meetings/${id}/decide`, { v }).then(
      json<{ decision: ApiDecision; idx: number; phase: 'say' | 'pick'; stage: 'run' | 'done'; cost: number }>
    ),
  finish: (id: string) =>
    post(`/api/meetings/${id}/finish`).then(json<{ actions: ApiAction[] }>),
  approvals: () => fetch(`${API_BASE}/api/approvals`).then(json<ApiApproval[]>),
  decideApproval: (id: string, approve: boolean, reason = '', actor = '主理人') =>
    post(`/api/approvals/${id}/decide`, { approve, reason, actor }).then(json<ApiApproval>),
  events: (limit = 50) => fetch(`${API_BASE}/api/events?limit=${limit}`).then(json<ApiEvent[]>),
  dashboard: () => fetch(`${API_BASE}/api/dashboard`).then(json<ApiDashboard>),
  model: (force = false) => fetch(`${API_BASE}/api/model?force=${force}`).then(json<ApiModelStatus>),
  tasks: () => fetch(`${API_BASE}/api/tasks`).then(json<ApiTask[]>),
  analyze: (focus = '', run_id?: string) => post('/api/analysis', { focus, run_id }).then(json<ApiAnalysis>),
  dispatchAnalysis: (a: AnalysisAction) =>
    post('/api/analysis/dispatch', { text: a.text, owner: a.owner, why: a.why }).then(json<ApiApproval>),
  rollbackTask: (id: string) => post(`/api/tasks/${id}/rollback`).then(json<ApiTask>),
  meetingHistory: () => fetch(`${API_BASE}/api/meetings`).then(json<ApiMeeting[]>),
  askApproval: (id: string, question: string) => post(`/api/approvals/${id}/ask`, { question }).then(json<ApiApproval>),
  audit: (limit = 100) => fetch(`${API_BASE}/api/audit?limit=${limit}`).then(json<{ seq: number; at: string; actor: string; action: string; target: string; hash: string }[]>),
  modelSettings: (force = false) =>
    fetch(`${API_BASE}/api/settings/model?force=${force}`).then(json<ApiModelStatus>),
  saveModelSettings: (b: { base_url?: string; api_key?: string; model?: string }) =>
    fetch(`${API_BASE}/api/settings/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(b)
    }).then(json<ApiModelStatus>),
  testModelSettings: (b: { base_url?: string; api_key?: string; model?: string }) =>
    post('/api/settings/model/test', b).then(json<ApiModelStatus>)
};

export type StaffConfig = { hired: boolean; owner: string; level: number };
export type MockShop = { id: string; name: string; platform: string; status: string; data_source: string };
export const organizationApi = {
  organization: () => fetch(`${API_BASE}/api/organization`).then(json<{ mode: 'solo' | 'team' }>),
  updateOrganization: (mode: 'solo' | 'team') => fetch(`${API_BASE}/api/organization`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) }).then(json<{ mode: 'solo' | 'team' }>),
  staff: () => fetch(`${API_BASE}/api/staff`).then(json<Record<string, StaffConfig>>),
  updateStaff: (id: string, value: StaffConfig) => fetch(`${API_BASE}/api/staff/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value)
  }).then(json<StaffConfig>),
  shops: () => fetch(`${API_BASE}/api/shops`).then(json<MockShop[]>),
  connect: (platform: string, name: string) => post('/api/shops', { platform, name }).then(json<MockShop>),
  disconnect: (id: string) => fetch(`${API_BASE}/api/shops/${id}`, { method: 'DELETE' }).then(json<{ ok: boolean }>)
};

export type Scenario = { id: string; title: string; description: string; metrics: string[] };
export const scenarioApi = {
  list: () => fetch(`${API_BASE}/api/scenarios`).then(json<Scenario[]>),
  analyze: (id: string) => post(`/api/scenarios/${id}/analyze`).then(json<ApiAnalysis>),
  dispatch: (id: string, a: AnalysisAction, result: ApiAnalysis) =>
    post(`/api/scenarios/${id}/dispatch`, { ...a, source: result.source, model: result.model }).then(json<ApiApproval>)
};

export type DemoMetrics = { stock: number; coverage_days: number; lead_gap_days: number; budget_total: number; acquisition_budget: number; retention_budget: number; estimated_roi: number; ticket_count: number; damaged_orders: number; refund_rate: number; spent: number };
export type DemoAction = { tool: string; arguments: Record<string, number>; why: string };
export type DemoTrace = { seq: number; at: string; node: string; actor: string; message: string; detail: unknown; kind: string };
export type DemoRun = {
  id: string; created_at: string; mode: 'live' | 'rehearsal'; model: string; harness: string; status: string; node: string;
  before: BusinessData; current: BusinessData; shop_id?: string; preset?: string; parent_id?: string; followup_run_id?: string; version?: number;
  versions?: { version: number; at: string; reason: string; error?: string; plan: DemoRun["plan"]; review: DemoRun["review"] }[];
  feedback?: { at: string; source: string; input: FeedbackInput; metrics: DemoMetrics };
  auto_approve: boolean; before_metrics: DemoMetrics; current_metrics: DemoMetrics;
  plan: null | { headline: string; findings: string[]; actions: DemoAction[] };
  review: null | { safe: boolean; summary: string; concerns: string[] };
  trace: DemoTrace[]; model_calls: { actor: string; model: string; elapsed_seconds: number; usage?: { total_tokens?: number }; output: unknown }[];
  preflight?: { name: string; passed: boolean; detail: string }[];
  checks: { name: string; passed: boolean; detail: string }[];
  receipts: { tool: string; arguments: Record<string, number>; output: unknown; at: string }[];
  memory: { run_id: string; summary: string }[]; input_hash: string; elapsed_seconds?: number; error: string; rollback_verified?: boolean;
};
export type BusinessData = {
  inventory: { sku: string; stock: number; daily_sales: number; lead_days: number; backup_stock: number; transfer_days: number; transfer_unit_cost: number };
  ads: { acquisition_budget: number; retention_budget: number; acquisition_roi: number; retention_roi: number; breakeven_roi: number };
  service: { orders: number; refund_orders: number; previous_refund_rate: number; damaged_orders: number; tickets: { id: string; reason: string; status: string }[] };
  finance: { available_cash: number; spent: number };
};
export type FeedbackInput = { fulfilled_orders: number; next_daily_sales: number; acquisition_spend: number; acquisition_gmv: number; retention_spend: number; retention_gmv: number; resolved_tickets: number };
export type DemoHistory = { id: string; mode: string; status: string; created_at: string; shop_id: string; version: number; title: string };
export type DemoTemplate = { data: BusinessData; presets: { id: string; title: string; data: BusinessData }[]; title: string; metrics: DemoMetrics; tools: { name: string; owner: string; description: string }[]; model: string; harness: string };
export const demoApi = {
  template: () => fetch(`${API_BASE}/api/demo/template`).then(json<DemoTemplate>),
  start: (mode: 'live' | 'rehearsal', auto_approve: boolean, extra: { data?: BusinessData; preset?: string; shop_id?: string; parent_id?: string } = {}) => post('/api/demo/runs', { mode, auto_approve, ...extra }).then(json<DemoRun>),
  read: (id: string) => fetch(`${API_BASE}/api/demo/runs/${id}`).then(json<DemoRun>),
  approve: (id: string) => post(`/api/demo/runs/${id}/approve`).then(json<DemoRun>),
  rollback: (id: string) => post(`/api/demo/runs/${id}/rollback`).then(json<DemoRun>),
  history: () => fetch(`${API_BASE}/api/demo/runs`).then(json<DemoHistory[]>),
  revise: (id: string, reason: string, data?: BusinessData) => post(`/api/demo/runs/${id}/revise`, { reason, data }).then(json<DemoRun>),
  resume: (id: string) => post(`/api/demo/runs/${id}/resume`).then(json<DemoRun>),
  feedback: (id: string, input: FeedbackInput) => post(`/api/demo/runs/${id}/feedback`, input).then(json<DemoRun>),
  export: (id: string) => fetch(`${API_BASE}/api/demo/runs/${id}/export`).then(json<unknown>)
};

export type EvaluationReport = { id: string; created_at: string; status: string; mode: string; model: string; dataset: string; scope: string; progress: string; summary: { arm: string; cases: number; correct_outcomes: number; invalid_proposals: number; errors: number; mean_seconds: number; model_responses: number; reported_tokens: number }[]; results: { case: string; case_title: string; arm: string; correct_outcome: boolean; executable: boolean; error: string; elapsed_seconds: number; checks: { name: string; passed: boolean; detail: string }[] }[] };
export const evaluationApi = {
  list: () => fetch(`${API_BASE}/api/evaluation`).then(json<EvaluationReport[]>),
  start: (mode: 'rules' | 'live') => post('/api/evaluation', { mode }).then(json<EvaluationReport>),
  cancel: (id: string) => post(`/api/evaluation/${id}/cancel`).then(json<EvaluationReport>)
};
