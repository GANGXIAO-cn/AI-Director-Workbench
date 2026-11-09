import { useState } from "react";
import { ScrollText } from "lucide-react";
import { Modal, useQuery } from "./ui";

type Receipt = {id:string;project:string;episode:string|null;batch:string|null;time:number|null;label:string;
  status:string;reason:string;needsReconcile:boolean;hasPrivateDetail:boolean};
const warning="请先核对通道回执。此时重发可能重复扣费。";
const date=(time:number|null)=>time===null ? "时间未记录" : new Date(time*1000).toLocaleString();

export function ActivityLog({projects,current}:{projects:{id:string;name:string}[];current?:string}) {
  const [open,setOpen]=useState(false);
  return <><button className="btn activity-open" onClick={()=>setOpen(true)}><ScrollText size={16} aria-hidden="true"/>执行日志</button>
    {open && <LogPanel projects={projects} initial={current||""} close={()=>setOpen(false)}/>}</>;
}

function LogPanel({projects,initial,close}:{projects:{id:string;name:string}[];initial:string;close:()=>void}) {
  const [project,setProject]=useState(initial);
  const [page,setPage]=useState(0);
  const [copied,setCopied]=useState(false);
  const [manual,setManual]=useState("");
  const log=useQuery<{items:Receipt[];hasMore:boolean}>("activity_log",{project_id:project||null,offset:page*40});
  function clearCopy() {setCopied(false);setManual("");}
  async function copy() {
    const value=(log.data?.items??[]).map(r=>[
      date(r.time),r.label,r.status,r.reason,"记录 "+r.id,r.hasPrivateDetail?"原始诊断已隐藏":"",
      r.needsReconcile?warning:"",
    ].filter(Boolean).join(" · ")).join("\n");
    clearCopy();
    try {await navigator.clipboard.writeText(value);setCopied(true);}
    catch {setManual(value);}
  }
  return <Modal width={820} onClose={close}><div className="spread"><h2>执行日志</h2><button className="btn" onClick={close}>关闭日志</button></div>
    <div className="row"><select aria-label="日志项目" value={project} onChange={e=>{setProject(e.target.value);setPage(0);clearCopy();}}>
      <option value="">全部项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <button className="btn" onClick={()=>{clearCopy();void log.reload();}}>刷新日志</button>
      <button className="btn" disabled={!log.data?.items.length} onClick={copy}>复制本页脱敏日志</button>{copied&&<span role="status">已复制</span>}</div>
    {manual&&<label>手动复制脱敏日志<textarea readOnly value={manual} onFocus={e=>e.target.select()}/></label>}
    {log.error && <p className="error">{log.error}</p>}
    {!log.data && !log.error && <p>正在读取日志…</p>}
    {log.data?.items.length===0 && <p>尚无执行记录。</p>}
    {log.data?.items.map(r=><article className="card activity-row" key={r.id}>
      <div className="spread"><b>{r.label} · {r.status}</b><small>{date(r.time)}</small></div>
      <p>{r.reason}</p>{r.needsReconcile&&<p className="amber">{warning}</p>}
      <div className="row muted tiny">{projects.find(p=>p.id===r.project)?.name} · 记录 {r.id.slice(0,8)}
        {r.hasPrivateDetail && <span>原始诊断已隐藏</span>}
        {r.batch&&r.episode&&<a className="btn" href={`#/e/${r.episode}/assets?batch=${r.batch}`} onClick={close}>查看批次回执</a>}</div>
    </article>)}
    {(page>0||log.data?.hasMore)&&<div className="row"><button className="btn" disabled={!page} onClick={()=>{setPage(page-1);clearCopy();}}>上一页</button>
      <span>第 {page+1} 页</span><button className="btn" disabled={!log.data?.hasMore} onClick={()=>{setPage(page+1);clearCopy();}}>下一页</button></div>}
  </Modal>;
}
