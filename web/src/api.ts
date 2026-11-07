let token: string | null = null;

export class ApiError extends Error {
  constructor(public status: number, message: string, public current?: unknown) {
    super(message);
  }
}

async function ensureToken() {
  if (!token) token = (await (await fetch("/api/session")).json()).token;
  return token!;
}

async function call<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["x-adw-token"] = await ensureToken();
  if (body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch("/api" + path, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error ?? res.statusText, data.current);
  return data as T;
}

export const commandId = () => crypto.randomUUID();
export const get = <T,>(path: string) => call<T>("GET", path);
export const post = <T,>(path: string, body: unknown) => call<T>("POST", path, body);
export const put = <T,>(path: string, body: unknown) => call<T>("PUT", path, body);
export const upload = <T,>(path: string, form: FormData) => call<T>("POST", path, undefined, form);

export type Setting = { key: string; category: string; strength: "required" | "variable"; content: string };
export type Episode = {
  id: string; display_code: string; title: string; state: string; leftover_facts: string[];
  kickoff_id: string | null; revision: number;
};
export type Project = {
  id: string; name: string; summary: string; config_id: string; settings: Setting[];
  spec_preset: Record<string, unknown>; episodes: Episode[];
  imports: { id: string; episode_id: string | null; original_name: string; purpose: string; parse_status: string; diagnostic: string | null; sha256: string }[];
};
export type Issue = {
  id: string; ordinal: number; loc: { anchor: string; evidence: string }; category: string;
  setting_id: string | null; setting_content: string | null; reason: string; blocking: number; waiver_reason: string | null;
};
export type Review = {
  id: string; pass_no: number; original_decision: string; isolation_level: string; reviewer: string;
  manifest_hash: string; session_id: string | null; observations: { objectId: string; anchor: string; evidence: string }[];
  issues: Issue[];
};
export type Version = {
  id: string; display_no: number; base_id: string | null; origin: string; source_run_id: string | null;
  content: Record<string, string>; reviews: Review[];
  gate: { formatErrors: string[]; blocking: string[]; unresolved: string[]; adoptable: boolean; review: unknown; reviewRequired: boolean };
};
export type Run = {
  id: string; purpose: string; counted_round: number; provider: string; requested_model: string | null;
  status: string; reason: string | null; diagnostic: string | null; session_id: string | null; session_mode: string;
  isolation_level: string | null; quota_resets_at: string | null; input_hash: string; format_attempts: number;
};
export type Task = {
  id: string; goal: string; stage: string; used_rounds: number; max_rounds: number; state: string; auto_revise: number;
  messages: { id: string; seq: number; role: string; text: string }[]; runs: Run[];
};
export type Artifact = { id: string; stage: string; adopted_version_id: string | null; versions: Version[]; tasks: Task[] };
export type EpisodeView = {
  episode: Episode & { project_id: string };
  kickoff: null | {
    source_type: string; goal: string; relation: string; carried_facts: string[]; settings_revision: number;
    settings_snapshot: Setting[]; spec: Record<string, unknown>; executor_plan: Record<string, { provider: string }>; idea_text: string | null;
  };
  sourceImport?: { original_name: string; sha256: string; parsed_text: string };
  artifacts: Artifact[];
};
