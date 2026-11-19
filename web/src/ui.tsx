/* eslint-disable @typescript-eslint/no-explicit-any */
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, q } from "./api";

export const go = (hash: string) => { location.hash = hash; };

export function useQuery<T = any>(name: string | null, args: Record<string, any> = {}, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = name ? name + JSON.stringify(args) : null;
  const current = useRef(key); current.current = key;
  const seq = useRef(0);
  const [loadedKey,setLoadedKey]=useState<string|null>(null);
  const load = useCallback(async () => {
    if (!name) return;
    const ticket=++seq.current;
    try { const next=await q<T>(name,args); if(current.current===key && ticket===seq.current){setData(next);setError(null);setLoadedKey(key);} }
    catch (e) { if(current.current===key && ticket===seq.current){setError(e instanceof ApiError?e.message:String(e));setData(null);setLoadedKey(key);} }
  }, [key]);
  useEffect(() => { load(); }, [load, ...deps]);
  return { data: name && loadedKey===key ? data : null, error: name && loadedKey===key ? error : null, reload:load, setData };
}

export function usePoll(active: boolean, fn: () => void | Promise<unknown>, ms = 1500) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    let pending=false;
    const t = setInterval(async () => {
      if (pending) return;
      pending=true;
      try { await ref.current(); }
      catch { /* Query/action callbacks surface their own errors. */ }
      finally { pending=false; }
    }, ms);
    return () => clearInterval(t);
  }, [active, ms]);
}

/** Wrap a command: shows the server's message (and detail list) instead of failing silently. */
export function useAction() {
  const [error, setError] = useState<{ message: string; detail?: any } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setError(null); setBusy(true);
    try { return await fn(); }
    catch (e) { setError(e instanceof ApiError ? { message: `${e.message}`, detail: e.detail } : { message: String(e) }); }
    finally { setBusy(false); }
  }, []);
  return { error, busy, run, clear: () => setError(null) };
}

export function ErrorLine({ error }: { error: { message: string; detail?: any } | null }) {
  if (!error) return null;
  const d = error.detail;
  return (
    <div className="error" style={{ marginTop: 8 }}>
      {error.message}
      {Array.isArray(d) && <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{d.map((x: any, i: number) => (
        <li key={i}>{typeof x === "string" ? x : `${x.stage ?? ""} ${x.segment ?? ""} ${x.file ?? ""}：${x.problem}`}</li>))}</ul>}
      {d && !Array.isArray(d) && d.rows && <div>需补齐第 {d.rows.join("、")} 行</div>}
    </div>
  );
}

export function Modal({ children, onClose, width }: { children: ReactNode; onClose: () => void; width?: number }) {
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" style={width ? { width } : undefined} onClick={(e) => e.stopPropagation()}>{children}</div>
    </div>
  );
}

export function Tag({ tone, children }: { tone?: string; children: ReactNode }) {
  return <span className={`tag ${tone ?? ""}`}>{children}</span>;
}

export function Pills<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return <div className="row">{options.map(([v, label]) => (
    <button key={v} className={`pill ${value === v ? "on" : ""}`} onClick={() => onChange(v)}>{label}</button>))}</div>;
}

import { FILM_STAGES } from "./workflow";
export const PRODUCTION_STEPS: [string,string][] = [...FILM_STAGES];
export const DELIVERY_STEPS: [string,string][] = [["delivery","交付核验"],["publish","发布文案·最后一步"],["package","H3 出包"]];
export const MATERIAL_TABS: [string,string][] = [["asset_design","素材清单"],["worksheet","备图工作单"],["shot_assets","按镜头补图"],["assets","全部图片"]];
export const SHOT_TABS: [string,string][] = [["plan","分镜表"],["review","分镜审核"]];
export const STAGES: [string,string][] = [...FILM_STAGES,...MATERIAL_TABS.slice(1),...DELIVERY_STEPS];

export const fmtMs = (ms: number) => `${String(Math.floor(ms / 60000)).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
export const rno = (n?: number) => `R${String(n ?? 0).padStart(2, "0")}`;

export function SegBar({ total, done, now }: { total: number; done: number; now?: boolean }) {
  return <div className="segbar">{Array.from({ length: total }, (_, i) => (
    <i key={i} className={i < done ? "done" : i === done && now ? "now" : ""} />))}</div>;
}

export const PROVIDER_LABEL: Record<string, string> = { codex: "Codex", claude: "Claude Code", antigravity: "Antigravity" };
// 实测 137：需要暂时隐藏某个执行器时写进此列表（曾隐藏 Claude，已按导演要求恢复）；当前值仍是隐藏项时保留显示，避免选择框显示错值。
export const HIDDEN_PROVIDERS: string[] = ["claude"];
export const pickable = (keys: string[], _current?: string) => keys.filter(k => !HIDDEN_PROVIDERS.includes(k));
export function Avatars({create, review}: {create?:string; review?:string}) {
  return <span className="tiny muted">{create ? "创作 " + (PROVIDER_LABEL[create] ?? create) : ""} {review ? "· 独审 " + (PROVIDER_LABEL[review] ?? review) : ""}</span>;
}
