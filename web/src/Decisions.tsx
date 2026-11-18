import { useState, useEffect } from "react";
import { Drawer } from "./Drawer";
import { cmd, q } from "./api";
import { ErrorLine, useAction } from "./ui";
import { FILM_STAGES } from "./workflow";

const labels = Object.fromEntries(FILM_STAGES);
type Scope = {scopeKind:string;scopeValue:string[]};
const episodeScope = {scopeKind:"episode",scopeValue:[]};

function ScopePicker({scope, scenes, change}:{scope:Scope;scenes:string[];change:(s:Scope)=>void}) {
  return <><label>范围 <select value={scope.scopeKind} onChange={e=>change({scopeKind:e.target.value,scopeValue:[]})}>
    <option value="episode">整集</option><option value="scenes" disabled={!scenes.length}>指定场次</option></select></label>
    {scope.scopeKind==="scenes" && scenes.map(id=><label key={id}><input type="checkbox" checked={scope.scopeValue.includes(id)}
      onChange={e=>change({...scope,scopeValue:e.target.checked?[...scope.scopeValue,id]:scope.scopeValue.filter(s=>s!==id)})}/>{id}</label>)}</>;
}

export function Decisions({eid,stage,records,scenes,refresh,close}:any) {
  const [edit,setEdit]=useState<any>(null);
  const act=useAction();
  function card(d:any) {
    return <article key={d.id}><p><b>{d.code}</b> {d.text}</p>
      <small>{d.origin_kind==="keep"?"本步必须保留":d.origin_kind==="dialogue"?"阶段对话":"手工录入"} · {d.scope_kind==="episode"?"整集":d.scenes.join("、")}</small>
      {d.placements.map(n=><p key={JSON.stringify([n.stage,n.segmentId])}><a href={`#/e/${eid}/${n.stage}?v=${n.versionId}`} onClick={close}>{labels[n.stage]} · {n.where||"不适用"}</a> {n.quote||n.reason}</p>)}
      {d.state==="active" && <div><button className="btn" onClick={()=>setEdit({id:d.id,text:d.text,origin:d.origin_stage,scopeKind:d.scope_kind,scopeValue:d.scenes})}>修改</button>
        <button className="btn" onClick={()=>act.run(async()=>{await cmd("revoke_decision",{decision_id:d.id});await refresh();})}>撤销</button></div>}
    </article>;
  }
  return <Drawer label="导演决定清单" close={close}>
    {FILM_STAGES.map(([key,label])=>{
      const rows=records.filter(d=>d.origin_stage===key && d.state==="active");
      return rows.length>0 && <section key={key}><h3>{label}</h3>{rows.map(card)}</section>;
    })}
    <details><summary>已撤销</summary>{records.filter(d=>d.state==="revoked").map(card)}</details>
    <button className="btn" onClick={()=>setEdit({text:"",origin:labels[stage]?stage:"story",...episodeScope})}>新增决定</button>
    {edit && <div className="card"><label>导演原话<textarea value={edit.text} onChange={e=>setEdit({...edit,text:e.target.value})}/></label>
      <label>来源阶段<select disabled={!!edit.id} value={edit.origin} onChange={e=>setEdit({...edit,origin:e.target.value})}>{FILM_STAGES.map(([k,l])=><option key={k} value={k}>{l}</option>)}</select></label>
      <ScopePicker scope={edit} scenes={scenes} change={s=>setEdit({...edit,...s})}/>
      <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{
        const args={text:edit.text,scope_kind:edit.scopeKind,scope_value:edit.scopeValue};
        if(edit.id) await cmd("replace_decision",{...args,decision_id:edit.id});
        else await cmd("add_decision",{...args,episode_id:eid,origin_stage:edit.origin});
        setEdit(null);await refresh();
      })}>保存决定</button></div>}
    <ErrorLine error={act.error}/></Drawer>;
}

export function RecordMessageDecision({message,eid,reload}:any) {
  const act=useAction();
  const [recorded,setRecorded]=useState<string|null>(null);
  return (message.decisionCode || recorded) ? <small>已记为决定 {message.decisionCode || recorded}</small> :
    <><button className="btn ghost tiny" disabled={act.busy} onClick={()=>act.run(async()=>{
      const result=await cmd<any>("record_message_decision",{episode_id:eid,message_id:message.id});setRecorded(result.code);
      await reload();
    })}>记为决定</button><ErrorLine error={act.error}/></>;
}

export function DecisionMessageSummary({art,eid,reload}:any) {
  const [open,setOpen]=useState(false);
  if(!labels[art.stage]) return null;
  const tasks=art.tasks.filter((task:any)=>!task.internal);
  const count=tasks.reduce((n:number,t:any)=>n+(t.userMessageCount ?? t.messages.filter((m:any)=>m.role==="user"&&m.kind==="text").length),0);
  const decided=tasks.reduce((n:number,t:any)=>n+(t.decisionMessageCount ?? t.messages.filter((m:any)=>m.role==="user"&&m.kind==="text"&&m.decisionCode).length),0);
  return <><button className="btn ghost" onClick={()=>setOpen(true)}>本步你发过 {count} 条消息，已记为决定 {decided} 条。</button>
    {open && <Drawer label="本步历史消息" close={()=>setOpen(false)}><DecisionMessageHistory key={eid+art.stage} eid={eid} stage={art.stage} reload={reload}/></Drawer>}</>;
}

function DecisionMessageHistory({eid,stage,reload}:any) {
  const [page,setPage]=useState<any>(null);const [messages,setMessages]=useState<any[]>([]);
  const act=useAction();
  const load=(before?:string)=>act.run(async()=>{
    const result=await q<any>('episode_messages',{episode_id:eid,stage,user_only:true,limit:30,...(before?{before}:{})});
    setMessages(old=>before?[...result.messages,...old]:result.messages);setPage(result);
  });
  useEffect(()=>{void load();},[eid,stage]);
  return <div className="decision-message-history">
    {page?.hasMore && <button className="btn" disabled={act.busy} onClick={()=>load(page.before)}>加载更早消息</button>}
    {act.busy && <p>正在加载…</p>}<ErrorLine error={act.error}/>
    {page && !messages.length && <p className="muted">本阶段暂无用户消息。</p>}
    {messages.map(m=><article key={m.id}><p>{m.text}</p><RecordMessageDecision message={m} eid={eid} reload={reload}/></article>)}
  </div>;
}

export function DecisionAdoption({art,version,onDone,children,disabled=false,cont=false}:any) {
  const act=useAction();
  return <><button className={cont && art.stage==="asset_design" ? "btn asset-next" : "btn"} disabled={disabled||act.busy} onClick={()=>act.run(async()=>{
    const result=await cmd("adopt",{artifact_id:art.id,version_id:version.id,expected_adopted_version_id:art.adopted_version_id,and_continue:cont});
    await onDone(version.id,result);
  })}>{children}</button><ErrorLine error={act.error}/></>;
}

function NoteFields({note,update}:any) {
  return <div><label>落实情况<select value={note.status} onChange={e=>update({status:e.target.value})}><option value="applied">已落实</option><option value="not_applicable">本步不适用</option></select></label>
    {note.status==="applied"?<><input aria-label="落实位置" placeholder="场号、镜号或段号" value={note.where??""} onChange={e=>update({where:e.target.value})}/>
      <textarea aria-label="本稿逐字引文" placeholder="本稿逐字引文" value={note.quote??""} onChange={e=>update({quote:e.target.value})}/></>:
      <input aria-label="不适用理由" value={note.reason??""} onChange={e=>update({reason:e.target.value})}/>}</div>;
}

export function DecisionNotes({records,art,version,eid,onSave,frozen}:any) {
  const act=useAction();
  const [draft,change]=useState<Record<string,any>>({});
  const stageIndex = FILM_STAGES.findIndex(([k])=>k===art.stage);
  const rows=records.filter(d=>d.state==="active"&&FILM_STAGES.findIndex(([k])=>k===d.origin_stage)<=stageIndex);
  const entries=rows.flatMap(d=>{
    const notes=(version.content.decisionNotes??[]).filter(n=>n.decision===d.code);
    return (notes.length?notes:[{decision:d.code,status:"applied"}]).map(original=>{
      const key=JSON.stringify([version.id,d.code,original.segmentId]);
      return {d,key,note:draft[key]??original};
    });
  });
  if(!rows.length) return null;
  return <section><h4>本稿导演决定落点</h4>{entries.map(({d,key,note})=><div key={key}><p>{d.code} · {d.text} {note.segmentId}</p>
    {!frozen.has(d.code)&&<b>本稿生成后新增，尚未读取</b>}
    <NoteFields note={note} update={patch=>change({...draft,[key]:{...note,...patch}})}/></div>)}
    <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
      const r=await cmd("save_manual",{artifact_id:art.id,base_version_id:version.id,content:{...version.content,decisionNotes:entries.map(e=>e.note)},note:"填写决定落点"});
      await onSave(r.versionId);
    })}>保存落点为新稿</button><ErrorLine error={act.error}/></section>;
}

export function episodeScenes(view:any):string[] {
  const a=view.artifacts.find(x=>x.stage==="script");
  return a?.versions.find(v=>v.id===a.adopted_version_id)?.content.scenes?.map(s=>s.id)??[];
}
