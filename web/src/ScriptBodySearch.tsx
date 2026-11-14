import { useEffect, useState } from "react";
import { q } from "./api";

export type ScriptRow = { title:string; path:string; sha256:string; category:string; kind:string; evidence?:string; matchOffset?:number; match?:{offset:number;text:string} };
export const scriptTypes:Record<string,string> = {method:"方法资料", reference:"参考资料", novel:"小说"};
export const isMaterial=(r:ScriptRow)=>r.path.split("/").at(-1)!=="来源清单.md";
type Cursor = {stamp:string; after:number};
type Issue = {path:string; reason:string};
type Batch = {rows:ScriptRow[]; issues:Issue[]; total:number; scanned:number; cursor:Cursor|null};
type Page = {rows:ScriptRow[]; issues:Issue[]; total:number; scanned:number; cursor:Cursor|null; done:boolean};
const empty:Page = {rows:[], issues:[], total:0, scanned:0, cursor:null, done:false};

export function ScriptBodySearch({query, category, kind, recommended, open}:{
  query:string; category:string; kind:string; recommended:boolean; open:(row:ScriptRow)=>void
}) {
  const [pages,setPages] = useState<Page[]>([]);
  const [index,setIndex] = useState(0);
  const [error,setError] = useState("");
  const [retry,setRetry] = useState(0);
  const previous = pages[index-1];
  const page = pages[index] || empty;
  useEffect(() => {
    if (!query.trim() || page.done) return;
    let active = true;
    let accumulated:Page = {...empty, scanned:previous?.scanned || 0};
    let cursor = previous?.cursor ?? null;
    setError("");
    async function load() {
      do {
        const batch = await q<Batch>("search_script_bodies", {query, category, kind, recommended, cursor, limit:40-accumulated.rows.length});
        if (!active) return;
        cursor = batch.cursor;
        const rows = [...accumulated.rows,...batch.rows.filter(isMaterial)];
        accumulated = {rows, issues:[...accumulated.issues,...batch.issues],
          scanned:accumulated.scanned+batch.scanned, total:batch.total, cursor,
          done:cursor===null || rows.length>=40};
        const updated = accumulated;
        setPages(all=>[...all.slice(0,index), updated, ...all.slice(index+1)]);
      } while (!accumulated.done);
    }
    load().catch(e=>{if(active) setError(String(e.message || e));});
    return () => {active=false;};
  }, [query, category, kind, recommended, index, page.done, previous, retry]);
  if (!query.trim()) return <p>输入要查找的原文字词。</p>;
  return <section aria-label="全库正文查找">
    <p role="status">{page.done ? "已查" : error ? "查找中断" : "正在查找"} {page.scanned} / {page.total} 份 · 本页命中 {page.rows.length} 份</p>
    {error && <p className="error">{error} <button className="btn" onClick={()=>setRetry(n=>n+1)}>重试本页</button></p>}
    {page.issues.length>0 && <details><summary>本页有 {page.issues.length} 份原文未能核实</summary>{page.issues.map(i=><p key={i.path}>{i.path} · {i.reason}</p>)}</details>}
    <div className="script-list">{page.rows.map(r=><article key={r.path}><button onClick={()=>open(r)}>
      <strong>{r.title}</strong><span>{r.category} · {scriptTypes[r.kind]}</span><small>{r.path}</small>
      <small>{r.evidence}</small></button></article>)}</div>
    <div className="row"><span>第 {index+1} 页</span><button className="btn" disabled={!index} onClick={()=>setIndex(i=>i-1)}>上一页</button>
      <button className="btn" disabled={!page.done||!page.cursor} onClick={()=>setIndex(i=>i+1)}>下一页</button></div>
  </section>;
}

export function MarkedExcerpt({text,offset,match}:{text:string;offset:number;match?:ScriptRow["match"]}) {
  if (!match) return <pre>{text}</pre>;
  const chars = Array.from(text), at = match.offset-offset, end = at+Array.from(match.text).length;
  if (at<0 || chars.slice(at,end).join("")!==match.text) return <pre>{text}</pre>;
  return <pre>{chars.slice(0,at).join("")}<mark>{match.text}</mark>{chars.slice(end).join("")}</pre>;
}
