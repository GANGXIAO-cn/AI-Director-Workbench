import { ModelPick } from "./Project";
import { useState } from "react";
import { ResourceBrowser, type Excerpt } from "./Resources";
import { cmd } from "./api";
import { useDirectorCommand } from "./DirectorFiles";
import { ErrorLine, Modal, PROVIDER_LABEL, useAction, usePoll, useQuery } from "./ui";

import { TopicRuntimeTag, type TopicRuntime } from "./TopicRuntime";

type Candidate = {runtime?:TopicRuntime;title:string;premise:string;mechanism:string;motivation:string;difference:string;source:string;quote:string;savedCode?:string};
type Run = {id:string;attempt:string;text:string;status:string;reason?:string;provider:string;candidates:Candidate[]};
const states:Record<string,string> = {queued:"排队中",running:"正在研究",failed:"需要重试",waiting_quota:"等待额度",stopped:"已停止",succeeded:"已完成"};

export type ReferenceDraft = {value:Excerpt|null;change:(value:Excerpt|null)=>void};
export function ResearchReference({reference,replace,clear}:{reference:Excerpt;replace:()=>void;clear:()=>void}) {
  return <div className="field research-reference">
      <div className="spread"><strong>已引用素材 · 《{reference.title}》</strong><div className="row">
        <button className="btn" onClick={replace}>更换片段</button>
        <button className="btn ghost" onClick={clear}>取消引用</button></div></div>
      <details><summary>查看所选原文 · 第 {reference.offset+1}–{reference.offset+Array.from(reference.text).length} 字</summary>
        <pre>{reference.text}</pre></details></div>;
}

export function TopicResearch({projectId,poolChanged,initialBrowse=false,hidePicker=false,onOriginal,onPick,referenceDraft}:{referenceDraft?:ReferenceDraft;projectId:string;poolChanged:()=>Promise<void>;initialBrowse?:boolean;hidePicker?:boolean;onOriginal?:(part:Excerpt)=>Promise<void>;onPick?:(topic:{id:string;code:string;title?:string;premise:string;runtime?:TopicRuntime})=>void}) {
  const records=useQuery<{providers:string[];runs:Run[]}>("topic_researches",{project_id:projectId});
  const [browsing,setBrowsing]=useState(initialBrowse);
  const [localReference,setLocalReference]=useState<Excerpt|null>(null);
  const reference=referenceDraft ? referenceDraft.value : localReference;
  const setReference=referenceDraft?.change ?? setLocalReference;
  const [direction,setDirection]=useState("");
  const [countChoice,setCountChoice]=useState("");
  const [customCount,setCustomCount]=useState("");
  const modelCatalog=useQuery<any>("model_catalog");
  const [selection,setSelection]=useState<{model:string|null;effort:string|null}>({model:null,effort:null});
  const [provider,setProvider]=useState("");
  const [codes,setCodes]=useState<Record<string,string>>({});
  const [selected,setSelected]=useState("");
  const runs=records.data?.runs ?? [];
  const run=runs.find(item=>item.id===selected) ;
  const providers=(records.data?.providers ?? []).filter(name=>name!=="claude");
  const executor=providers.includes(provider) ? provider : providers[0] || "";
  const send=useDirectorCommand();
  const action=useAction();
  const pending=(r:Run)=>["queued","running"].includes(r.status);
  usePoll(runs.some(pending),records.reload,1000);
  return <section className="card line topic-research"><h2>选题研究</h2>
    {!hidePicker && !reference && <button className="btn" onClick={()=>setBrowsing(true)}>从知识库选参考</button>}
    {browsing&&<Modal width={960} onClose={()=>setBrowsing(false)}><button className="btn" onClick={()=>setBrowsing(false)}>返回选题研究</button>
      <ResourceBrowser kind="knowledge" storyPicker={!!onOriginal} onChoose={part=>{setReference(part);setBrowsing(false);}}/></Modal>}
    {!hidePicker && reference && <ResearchReference reference={reference} replace={()=>setBrowsing(true)} clear={()=>setReference(null)}/>}
    {reference&&onOriginal&&<button className="btn primary" disabled={action.busy} onClick={()=>action.run(()=>onOriginal(reference))}>按原文改编</button>}
    <label>你想拍什么？（选填）<textarea value={direction} onChange={e=>setDirection(e.target.value)} placeholder="例如：做 3 个长篇选题，每条 4 分钟，围绕一个误会展开"/></label>
    <div className="row"><label>选题数量 <select value={countChoice} onChange={e=>setCountChoice(e.target.value)}>
      <option value="">按文字要求（默认 3 个）</option>
      {[1,2,3,4,5,6,8,10].map(n=><option key={n} value={n}>{n} 个</option>)}
      <option value="custom">自定义</option></select></label>
      {countChoice==="custom" && <label>自定义数量 <input type="number" min="1" step="1" value={customCount} onChange={e=>setCustomCount(e.target.value)}/></label>}
      <ModelPick label="使用的 AI" catalog={modelCatalog.data} provider={executor} sel={selection} setSel={setSelection} setProvider={(p:string)=>{setProvider(p);setSelection({model:modelCatalog.data?.text[p]?.models[0]?.id??null,effort:null});}}/>
      <button className="btn primary" disabled={action.busy || !executor} onClick={()=>action.run(async()=>{
        const count=countChoice ? Number(countChoice==="custom" ? customCount : countChoice) : undefined;
        if (count!==undefined && (!Number.isSafeInteger(count) || count<1)) throw new Error("选题数量须为正整数");
        const result=await send("start_topic_research",{project_id:projectId,candidate_count:count,direction:direction.trim() || (reference ? "根据所选原文片段和项目设定提出备选题，说明借鉴之处。若片段只有书目信息，请说明依据不足。" : "结合项目已有资料，自主提出不同的选题供我挑选，逐题推荐合适的篇幅和成片时长。"),provider:executor,...selection,...(reference?{reference:{path:reference.path,sha256:reference.sha256,offset:reference.offset,spanLength:Array.from(reference.text).length,reason:reference.reason}}:{})},[]);
        setSelected(result.turnId);setDirection("");await records.reload();
      })}>{reference?"参考这段内容想新题":"生成选题"}</button></div>
    {runs.length>0&&<label className="topic-history">历史研究 <select value={selected} onChange={e=>setSelected(e.target.value)}>
      <option value="">选择历史研究</option>{runs.map(r=><option key={r.id} value={r.id}>{r.text.replace(/\s+/g," ").slice(0,40)}{r.text.length>40?"…":""} · {states[r.status]}</option>)}</select></label>}
    {run&&<><div className="msg user">{run.text}</div>
      <span role="status" className="research-status" data-chat-kind={pending(run)?"progress":run.status==="succeeded"?"status":"action"}>{states[run.status]}{run.status==="succeeded" && ` · ${run.candidates.length} 个选题`}</span>{run.reason && <p className="error">{run.reason.includes("引用")?"这次生成的选题引用了无法核对的资料，请重新研究。":run.reason}</p>}
      {run.status!=="succeeded" && <button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{
        const result=await cmd(pending(run) ? "stop_free_chat" : "retry_free_chat",{project_id:projectId,turn_id:run.id,expected_attempt:run.attempt});if(result.turnId)setSelected(result.turnId);
        await records.reload();
      })}>{pending(run) ? "停止研究" : "重新研究"}</button>}
      {run.candidates.map((card,index)=>{
        const key=run.id+"-"+index,code=codes[key] ?? "";
        return <article className="card" data-chat-kind={card.savedCode?"status":"choice"} key={key}><div className="spread"><h3>{card.title}</h3><TopicRuntimeTag target={card.runtime}/></div><p style={{whiteSpace:"pre-line"}}>{card.premise}</p>
          <p>主角为什么这样做 · {card.motivation}<br/>事情怎样一步步变化 · {card.mechanism}<br/>与旧题的差异 · {card.difference}</p>
          <p>依据 · {card.source}<br/>“{card.quote}”</p>
          {card.savedCode ? <b>已保存到选题池 · {card.savedCode}</b> : <div className="row">
            <label>选题编号 <input placeholder="自动编号" value={code} onChange={e=>setCodes({...codes,[key]:e.target.value})}/></label>
            <button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{
              const saved=await send("accept_research_topic",{project_id:projectId,turn_id:run.id,index,code},[]);
              await records.reload();await poolChanged();
              onPick?.({id:saved.topicId,code:saved.code,title:card.title,premise:card.premise,runtime:card.runtime});
            })}>{onPick?"保存并选用":"保存到选题池"}</button></div>}</article>;
      })}</>}
      <ErrorLine error={action.error}/>{records.error && <p className="error">{records.error}</p>}
  </section>;
}
