/* eslint-disable @typescript-eslint/no-explicit-any */
let token: string | null = null;

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: any, public current?: any) {
    super(message);
  }
}

async function ensureToken() {
  if (!token) token = (await (await fetch("/api/session")).json()).token;
  return token!;
}

async function send<T = any>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["x-adw-token"] = await ensureToken();
  if (body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch("/api" + path, { method, headers, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error ?? res.statusText, data.detail, data.current);
  return data as T;
}

export const commandId = () => crypto.randomUUID();
/** Workbench command (writes). commandId is added automatically unless given. */
export const cmd = <T = any,>(name: string, args: Record<string, any> = {}) =>
  send<T>("POST", `/cmd/${name}`, "command_id" in args || NO_ID.has(name) ? args : { ...args, command_id: commandId() });
const NO_ID = new Set(["execute_batch", "expert_run", "backup", "restore", "set_publish_settings", "set_task_executor", "open_stage"]);
/** Workbench read model. */
export const q = <T = any,>(name: string, args: Record<string, any> = {}) =>
  send<T>("GET", `/q/${name}?args=${encodeURIComponent(JSON.stringify(args))}`);
export const upload = <T = any,>(kind: string, file: File, meta: Record<string, any>) => {
  const form = new FormData();
  form.append("file", file);
  form.append("meta", JSON.stringify(meta));
  return send<T>("POST", `/upload/${kind}`, undefined, form);
};
export const blob = (sha?: string | null) => (sha ? `/api/blob/${sha}` : undefined);
export const deliveryFile = (id: string, path: string) => `/api/delivery-file/${id}/${path}`;
