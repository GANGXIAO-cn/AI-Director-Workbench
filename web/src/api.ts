/* eslint-disable @typescript-eslint/no-explicit-any */
let token: string | null = null;

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: any, public current?: any) {
    super(message);
  }
}

async function ensureToken() {
  if (!token) token = (await (await fetch("/api/session", {cache: "no-store"})).json()).token;
  return token!;
}

async function send<T = any>(method: string, path: string, body?: unknown, form?: FormData, canRefresh = true): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["x-adw-token"] = await ensureToken();
  if (body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch("/api" + path, { method, headers, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined) });
  const data = await res.json().catch(() => ({}));
  if (canRefresh && method !== "GET" && res.status === 403 && data.error === "missing session token") {
    if (token === headers["x-adw-token"]) token = null;
    return send<T>(method, path, body, form, false);
  }
  if (!res.ok) throw new ApiError(res.status, data.error ?? res.statusText, data.detail, data.current);
  return data as T;
}

export const commandId = () => crypto.randomUUID();
/** Workbench command (writes). commandId is added automatically unless given. */
export const cmd = <T = any,>(name: string, args: Record<string, any> = {}) =>
  send<T>("POST", `/cmd/${name}`, "command_id" in args || NO_ID.has(name) ? args : { ...args, command_id: commandId() });
const NO_ID = new Set(["visit_project", "execute_batch", "backup", "restore", "set_publish_settings", "set_task_executor", "open_stage", "save_canvas", "preview_acting","package_preview"]);
/** Workbench read model. */
export const q = <T = any,>(name: string, args: Record<string, any> = {}) =>
  send<T>("GET", `/q/${name}?args=${encodeURIComponent(JSON.stringify(args))}`);
export const upload = <T = any,>(kind: string, file: File, meta: Record<string, any>) => {
  const form = new FormData();
  form.append("file", file);
  form.append("meta", JSON.stringify(meta));
  return send<T>("POST", `/upload/${kind}`, undefined, form);
};
export function importManuscript(projectId:string, file:File, purpose:string, episodeId?:string, id?:string) {
  const form = new FormData();
  form.append("file",file); form.append("purpose",purpose);
  if (episodeId) { form.append("episodeId",episodeId); form.append("forStory","true"); }
  if (id) form.append("commandId",id);
  return send("POST","/projects/"+encodeURIComponent(projectId)+"/imports",undefined,form);
}
export const blob = (sha?: string | null) => (sha ? `/api/blob/${sha}` : undefined);
export const deliveryFile = (id: string, path: string) => `/api/delivery-file/${id}/${path}`;

export const setCliPath = (provider: string, path: string) => send("PUT", "/settings/executors/" + encodeURIComponent(provider), {path: path || null});

export const setProviderKey = (id: string, revision: number, key: string) =>
  send("PUT", "/settings/providers/" + encodeURIComponent(id) + "/key", {revision, key});

export const openProjectFolder = (folder:string,expected?:string) => send<{url:string;projectId:string}>("POST","/project-folder/open",{folder,expected});
export const projectFolders = () => send<{external:boolean;opened:{path:string;url:string;projectId:string}[];recent:{name:string;path:string;available:boolean;fingerprint:string}[]}>("GET","/project-folder");
export const closeProjectFolder = (folder:string) => send("POST","/project-folder/close",{folder});

export function commandWithFiles(name:string, args:Record<string,any>, files:File[]) {
  if (!files.length) return cmd(name,args);
  const form = new FormData();
  form.append("args",JSON.stringify({...args,command_id:args.command_id ?? commandId()}));
  for (const file of files) form.append("files",file);
  return send("POST","/with-files/"+encodeURIComponent(name),undefined,form);
}
