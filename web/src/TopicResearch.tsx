import { useState } from "react";
import { ResourceBrowser, type Excerpt } from "./Resources";
import { cmd } from "./api";
import { useDirectorCommand } from "./DirectorFiles";
import { ErrorLine, Modal, PROVIDER_LABEL, useAction, usePoll, useQuery } from "./ui";

type Candidate = {title:string;premise:string;mechanism:string;motivation:string;difference:string;source:string;quote:string;savedCode?:string};
type Run = {id:string;attempt:string;text:string;status:string;reason?:string;provider:string;candidates:Candidate[]};
const states:Record<string,string> = {queued:"排队中",running:"研究中",failed:"研究失败",waiting_quota:"等待额度",stopped:"已停止",succeeded:"已完成"};

export type ReferenceDraft = {value:Excerpt|null;change:(value:Excerpt|null)=>void};
export function ResearchReference({reference,replace,clear}:{reference:Excerpt;replace:()=>void;clear:()=>void}) {
  return <div className="field research-reference">
      <div className="spread"><strong>已引用素材 · 《{reference.title}》</strong><div className="row">
        <button className="btn" onClick={replace}>更换片段</button>
        <button className="btn ghost" onClick={clear}>取消引用</button></div></div>
      <details><summary>核对引用原文 · 第 {reference.offset+1}–{reference.offset+Array.from(reference.text).length} 字</summary>
        <pre>{reference.text}</pre></details></div>;
}

export function TopicResearch({projectId,poolChanged,initialBrowse=false,hidePicker=false,onOriginal,onPick,referenceDraft}:{referenceDraft?:ReferenceDraft;projectId:string;poolChanged:()=>Promise<void>;initialBrowse?:boolean;hidePicker?:boolean;onOriginal?:(part:Excerpt)=>Promise<void>;onPick?:(topic:{id:string;code:string;premise:string})=>void}) {
  const records=useQuery<{providers:string[];runs:Run[]}>("topic_researches",{project_id:projectId});
  const [browsing,setBrowsing]=useState(initialBrowse);
  const [localReference,setLocalReference]=useState<Excerpt|null>(null);
  const reference=referenceDraft ? referenceDraft.value : localReference;
  const setReference=referenceDraft?.change ?? setLocalReference;
  const [direction,setDirection]=useState("");
  const [provider,setProvider]=useState("");
  const [codes,setCodes]=useState<Record<string,string>>({});
  const [selected,setSelected]=useState("");
  const runs=records.data?.runs ?? [];
  const run=runs.find(item=>item.id===selected) ?? runs[0];
  const providers=records.data?.providers ?? [];
  const executor=provider || providers[0] || "";
  const send=useDirectorCommand();
  const action=useAction();
  const pending=(r:Run)=>["queued","running"].includes(r.status);
  usePoll(runs.some(pending),records.reload,1000);
  return <section className="card line"><h2>选题研究</h2>
    {!hidePicker && !reference && <button className="btn" onClick={()=>setBrowsing(true)}>从共享知识库选素材</button>}
    {browsing&&<Modal width={960} onClose={()=>setBrowsing(false)}><button className="btn" onClick={()=>setBrowsing(false)}>返回选题研究</button>
      <ResourceBrowser kind="knowledge" storyPicker={!!onOriginal} onChoose={part=>{setReference(part);setBrowsing(false);}}/></Modal>}
    {!hidePicker && reference && <ResearchReference reference={reference} replace={()=>setBrowsing(true)} clear={()=>setReference(null)}/>}
    {reference&&onOriginal&&<button className="btn primary" disabled={action.busy} onClick={()=>action.run(()=>onOriginal(reference))}>按这段原文拍</button>}
    <label>研究方向<textarea value={direction} onChange={e=>setDirection(e.target.value)} placeholder="可选，例如：围绕一个误会展开的短喜剧"/></label>
    <div className="row"><label>研究执行器 <select value={executor} onChange={e=>setProvider(e.target.value)}>
      {providers.map(p=><option value={p} key={p}>{PROVIDER_LABEL[p] ?? p}</option>)}</select></label>
      <button className="btn primary" disabled={action.busy || !executor} onClick={()=>action.run(async()=>{
        const result=await send("start_topic_research",{project_id:projectId,direction:direction.trim() || (reference ? "根据所选原文片段和项目设定提出备选题，说明借鉴之处。若片段只有书目信息，请说明依据不足。" : "结合项目已有资料，提出三个不同的短剧选题供我挑选。"),provider:executor,...(reference?{reference:{path:reference.path,sha256:reference.sha256,offset:reference.offset,spanLength:Array.from(reference.text).length,reason:reference.reason}}:{})},[]);
        setSelected(result.turnId);setDirection("");await records.reload();
      })}>{reference?"用这段素材生成备选题":"研究备选题"}</button></div>
    {run && <><label>研究记录 <select value={run.id} onChange={e=>setSelected(e.target.value)}>
      {runs.map(r=><option key={r.id} value={r.id}>{r.text} · {states[r.status]}</option>)}</select></label>
      <div className="msg user">{run.text}</div>
      <span role="status" className="research-status" data-chat-kind={pending(run)?"progress":run.status==="succeeded"?"status":"action"}>{states[run.status]}</span>{run.reason && <p className="error">{run.reason}</p>}
      {run.status!=="succeeded" && <button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{
        await cmd(pending(run) ? "stop_free_chat" : "retry_free_chat",{project_id:projectId,turn_id:run.id,expected_attempt:run.attempt});
        await records.reload();
      })}>{pending(run) ? "停止研究" : "按原资料重试"}</button>}
      {run.candidates.map((card,index)=>{
        const key=run.id+"-"+index,code=codes[key] ?? "";
        return <article className="card" data-chat-kind={card.savedCode?"status":"choice"} key={key}><h3>{card.title}</h3><p>{card.premise}</p>
          <p>角色动机 · {card.motivation}<br/>推进机制 · {card.mechanism}<br/>与旧题的差异 · {card.difference}</p>
          <p>依据 · {card.source}<br/>“{card.quote}”</p>
          {card.savedCode ? <b>已入池 · {card.savedCode}</b> : <div className="row">
            <label>入池编号 <input placeholder="自动编号" value={code} onChange={e=>setCodes({...codes,[key]:e.target.value})}/></label>
            <button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{
              const saved=await send("accept_research_topic",{project_id:projectId,turn_id:run.id,index,code},[]);
              await records.reload();await poolChanged();
              onPick?.({id:saved.topicId,code:saved.code,premise:card.premise});
            })}>{onPick?"选用到本集并入池":"选中并入池"}</button></div>}</article>;
      })}</>}
      <ErrorLine error={action.error}/>{records.error && <p className="error">{records.error}</p>}
  </section>;
}
