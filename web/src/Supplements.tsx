import { useState } from "react";
import { cmd } from "./api";
import { ErrorLine, rno, useAction, useQuery } from "./ui";
import { FILM_STAGES } from "./workflow";

export function Supplements({eid,refresh}:any) {
  const records=useQuery<any[]>("production_supplements",{episode_id:eid});
  const [open,setOpen]=useState(false);
  const [changes,change]=useState<Record<string,string>>({});
  const act=useAction();
  const rows=records.data??[];
  async function decide(id:string,action:string) {
    await cmd("decide_supplement",{supplement_id:id,action,statement:changes[id]??""});
  }
  async function reload() {await records.reload();await refresh();}
  return <section><h2>制作补充</h2><details onToggle={e=>setOpen(e.currentTarget.open)}><summary>查看制作补充 · {rows.length} 条</summary>
    {rows.map(s=><article className="card" key={s.id}><b>{s.code} · {s.subject}</b><p>{s.statement}</p>
      <small>无出处 · {s.reason} · {s.scenes.join("、")} · <a href={`#/e/${eid}/${s.origin_stage}?v=${s.origin_version_id}`}>{Object.fromEntries(FILM_STAGES)[s.origin_stage]} {rno(s.display_no)}</a> · {{confirmed:"已确认",proposed:"待确认",rejected:"已拒绝",superseded:"已替换"}[s.state]}</small>
      {["confirmed","proposed"].includes(s.state) && <div>{s.state==="proposed" && <button className="btn" onClick={()=>act.run(async()=>{await decide(s.id,"confirm");await reload();})}>确认</button>}
        <button className="btn" onClick={()=>act.run(async()=>{await decide(s.id,"reject");await reload();})}>拒绝</button>
        <input aria-label={s.code+" 替代说法"} value={changes[s.id]??""} onChange={e=>change({...changes,[s.id]:e.target.value})}/>
        <button className="btn" disabled={!changes[s.id]?.trim()} onClick={()=>act.run(async()=>{await decide(s.id,"replace");await reload();})}>替换</button></div>}
    </article>)}
    {open && rows.some(s=>s.state==="proposed") && <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{try {for(const s of rows.filter(s=>s.state==="proposed")) await decide(s.id,"confirm");} finally {await reload();}})}>全部确认</button>}
  </details><ErrorLine error={act.error}/></section>;
}

export function RoleStates({eid,assets,versionId}:any) {
  const matrix=useQuery("cast_matrix",{episode_id:eid},[versionId]);
  const [draft,set]=useState<Record<string,any>>({});
  const act=useAction();
  return <>{matrix.data?.roleStates.map(s=>{
    const k=s.designHash,d=draft[k]??{};
    if(s.textOnly)return <section className="card" key={k}><h4>{s.role} · {s.stateId}</h4><p>{s.description}</p><small>本段用文字描述此状态</small></section>;
    return <section className="card" key={k}><h4>{s.role} · {s.stateId}</h4><p>{s.description}</p>
      <p>{s.confirmed?"对图已确认 · "+s.observation:"待对图核对"}</p>
      <select aria-label="角色状态图" value={d.image??s.imageId??""} onChange={e=>set({...draft,[k]:{...d,image:e.target.value}})}><option value="">选择本集图片</option>
        {assets.filter(a=>a.kind==="image").map(a=><option value={a.id} key={a.id}>{a.name}</option>)}</select>
      {assets.find(a=>a.id===(d.image??s.imageId)) && <img style={{maxWidth:"100%",maxHeight:250}} alt="角色状态核对图" src={"/api/blob/"+assets.find(a=>a.id===(d.image??s.imageId)).sha256}/>}
      <input aria-label="对图观察" value={d.observation??""} onChange={e=>set({...draft,[k]:{...d,observation:e.target.value}})}/>
      <button className="btn" disabled={act.busy||!(d.image??s.imageId)||!d.observation?.trim()} onClick={()=>act.run(async()=>{
        await cmd("confirm_role_state",{episode_id:eid,role:s.role,state_id:s.stateId,design_hash:k,image_id:d.image??s.imageId,observation:d.observation});await matrix.reload();
      })}>确认此状态</button></section>;
  })}<ErrorLine error={act.error}/></>;
}
