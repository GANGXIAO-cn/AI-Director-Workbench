import { useState } from "react";
import { cmd } from "./api";
import { useDirectorCommand } from "./DirectorFiles";
import { ErrorLine, PROVIDER_LABEL, useAction, usePoll, useQuery } from "./ui";

type Candidate = {title:string;premise:string;mechanism:string;motivation:string;difference:string;source:string;quote:string;savedCode?:string};
type Run = {id:string;attempt:string;text:string;status:string;reason?:string;provider:string;candidates:Candidate[]};
const states:Record<string,string> = {queued:"排队中",running:"研究中",failed:"研究失败",waiting_quota:"等待额度",stopped:"已停止",succeeded:"已完成"};

export function TopicResearch({projectId,poolChanged}:{projectId:string;poolChanged:()=>Promise<void>}) {
  const records=useQuery<{providers:string[];runs:Run[]}>("topic_researches",{project_id:projectId});
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
    <label>研究方向<textarea value={direction} onChange={e=>setDirection(e.target.value)} placeholder="这次想找什么样的故事？"/></label>
    <div className="row"><label>研究执行器 <select value={executor} onChange={e=>setProvider(e.target.value)}>
      {providers.map(p=><option value={p} key={p}>{PROVIDER_LABEL[p] ?? p}</option>)}</select></label>
      <button className="btn primary" disabled={action.busy || !executor || !direction.trim()} onClick={()=>action.run(async()=>{
        const result=await send("start_topic_research",{project_id:projectId,direction,provider:executor},[]);
        setSelected(result.turnId);setDirection("");await records.reload();
      })}>研究备选题</button></div>
    {run && <><label>研究记录 <select value={run.id} onChange={e=>setSelected(e.target.value)}>
      {runs.map(r=><option key={r.id} value={r.id}>{r.text} · {states[r.status]}</option>)}</select></label>
      <span role="status">{states[run.status]}</span>{run.reason && <p className="error">{run.reason}</p>}
      {run.status!=="succeeded" && <button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{
        await cmd(pending(run) ? "stop_free_chat" : "retry_free_chat",{project_id:projectId,turn_id:run.id,expected_attempt:run.attempt});
        await records.reload();
      })}>{pending(run) ? "停止研究" : "按原资料重试"}</button>}
      {run.candidates.map((card,index)=>{
        const key=run.id+"-"+index,code=codes[key] ?? "";
        return <article className="card" key={key}><h3>{card.title}</h3><p>{card.premise}</p>
          <p>角色动机 · {card.motivation}<br/>推进机制 · {card.mechanism}<br/>与旧题的差异 · {card.difference}</p>
          <p>依据 · {card.source}<br/>“{card.quote}”</p>
          {card.savedCode ? <b>已入池 · {card.savedCode}</b> : <div className="row">
            <label>入池编号 <input value={code} onChange={e=>setCodes({...codes,[key]:e.target.value})}/></label>
            <button className="btn" disabled={!code.trim() || action.busy} onClick={()=>action.run(async()=>{
              await send("accept_research_topic",{project_id:projectId,turn_id:run.id,index,code},[]);
              await records.reload();await poolChanged();
            })}>选中并入池</button></div>}</article>;
      })}</>}
      <ErrorLine error={action.error}/>{records.error && <p className="error">{records.error}</p>}
  </section>;
}
