/* eslint-disable @typescript-eslint/no-explicit-any */
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, q } from "./api";

export const go = (hash: string) => { location.hash = hash; };

export function useQuery<T = any>(name: string | null, args: Record<string, any> = {}, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = name ? name + JSON.stringify(args) : null;
  const load = useCallback(async () => {
    if (!name) return;
    try { setData(await q<T>(name, args)); setError(null); }
    catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
  }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load, ...deps]);   // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, reload: load, setData };
}

export function usePoll(active: boolean, fn: () => void, ms = 1500) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => ref.current(), ms);
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

export const STAGES: [string, string][] = [
  ["story", "故事稿"], ["script", "剧本"], ["plan", "制作方案"], ["space", "空间锁定"], ["cast", "角色出镜"],
  ["assets", "素材与选图"], ["prompts", "提示词"], ["review", "两遍独审"], ["publish", "发布包"], ["delivery", "交付核验"],
  ["package", "H3 出包"],
];

export const fmtMs = (ms: number) => `${String(Math.floor(ms / 60000)).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
export const rno = (n?: number) => `R${String(n ?? 0).padStart(2, "0")}`;

export function SegBar({ total, done, now }: { total: number; done: number; now?: boolean }) {
  return <div className="segbar">{Array.from({ length: total }, (_, i) => (
    <i key={i} className={i < done ? "done" : i === done && now ? "now" : ""} />))}</div>;
}

export const PROVIDER_LABEL: Record<string, string> = { codex: "Codex", claude: "Claude Code", antigravity: "Antigravity" };
export function Avatars() {
  return <span className="avatars"><b style={{ background: "#fb923c" }}>Cl</b><b style={{ background: "#a78bfa" }}>Cx</b></span>;
}
