import { useState } from "react";
import { cmd } from "./api";
import { ErrorLine, PROVIDER_LABEL, rno, useAction } from "./ui";

const fields:Record<string,string>={synopsis:"故事梗概",cast:"角色表",handoff:"交接说明",notes:"备注",spine:"故事脊",timeline:"时间线",props:"规则与道具",causes:"因果核对表",adaptation:"改编依据"};
const states:Record<string,string>={queued:"已排队",running:"起草中",failed:"失败",waiting_quota:"等待额度",stopped:"已停止",succeeded:"已完成"};

function author(config:any) {
  return [PROVIDER_LABEL[config?.provider] || config?.provider, config?.model || "执行器默认型号",config?.effort].filter(Boolean).join(" · ");
}

export function StoryComparison({write,locked,art,sourceId,plan,open,select,reload}:any) {
  const [selectedRound, setRound]=useState<number|null>(null);
  const [request,setRequest]=useState("");
  const [choices, setChoices]=useState<Record<string,string>>({});
  const act=useAction();
  const versions=new Map<string,any>(art.versions.map((v:any)=>[v.id,v]));
  const source=versions.get(sourceId);
  const all=art.comparisons || [];
  const previous=all.filter((slot:any)=>slot.source_version_id===sourceId);
  const latest=previous[0]?.round_no || 0;
  const round=selectedRound ?? (latest || 1);
  const slots=previous.filter((slot:any)=>slot.round_no===round);
  function changeRound(value:number) {setRound(value);setChoices({});}
  if (!source) return <p>找不到用于比较的故事版本。</p>;
  const contents=slots.map((s:any)=>versions.get(s.version_id)?.content || {});
  const keys=Object.keys(fields).filter(k=>contents.some((c:any)=>Object.hasOwn(c,k)));
  const complete=slots.length===2 && slots.every((s:any)=>versions.has(s.version_id));
  const pick=(key:string)=>choices[key] || slots.find((s:any)=>Object.hasOwn(versions.get(s.version_id)?.content || {},key))?.version_id;
  return <section className="story-compare">
    <div className="row"><label>共同基稿 <select value={sourceId} onChange={e=>select(e.target.value)}>
      {art.versions.map((v:any)=><option key={v.id} value={v.id}>{rno(v.display_no)}{all.some((s:any)=>s.source_version_id===v.id)?" · 已发起比较":""}</option>)}</select></label>
      {!!latest && <label>比较轮次 <select value={round} onChange={e=>changeRound(Number(e.target.value))}>
        {previous.filter((s:any)=>s.side==="A").map((s:any)=><option key={s.round_no} value={s.round_no}>第 {s.round_no} 轮</option>)}
        <option value={latest+1}>按当前资料另起一轮</option></select></label>}</div>
    {!slots.length && <div className="card"><p>A 稿由 {author(plan.create)} 起草。B 稿由 {author(plan.review)} 起草。</p>
      <p>两次起草使用同一份基稿、本步要求和参考资料，各占用一次执行器调用。候选稿需单独送审。</p>
      <label>共同创作要求<textarea disabled={locked} value={request} maxLength={4000} onChange={e=>setRequest(e.target.value)}
        placeholder="例如，保留开场动作，让两个人在结尾有不同选择"/></label>
      <button className="btn primary" disabled={locked || act.busy} onClick={()=>act.run(async()=>{
        await write("start_story_comparison",{version_id:sourceId,after_round:round-1,request});reload();
      })}>提交两份候选稿</button></div>}
    {slots.length>0 && <p>本轮要求 · {slots[0].goal}</p>}
    {slots.length>0 && <div className="story-candidates">{slots.map((s:any)=>{
      const version=versions.get(s.version_id);
      return <article className="card" key={s.side}><h3>{s.side} 稿{version?" · "+rno(version.display_no):""}</h3>
        <p>{author({provider:s.provider,model:s.requested_model,effort:s.requested_effort})}</p>
        <small>{states[s.status] || s.status}{s.reason?" · "+s.reason:""}</small>
        {version && <><p className="muted small">服务报告型号 · {s.reported_model || "未报告"}</p>
          <button className="btn" onClick={()=>open(version.id)}>打开{s.side}稿</button></>}
        {["failed","waiting_quota","stopped"].includes(s.status) && <button className="btn" disabled={locked || act.busy}
          onClick={()=>act.run(async()=>{await cmd("retry_run",{run_id:s.run_id});reload();})}>重试{s.side}稿</button>}
      </article>;})}</div>}
    {slots.length>0 && keys.map(key=><div key={key}><h3>{fields[key]}</h3><div className="story-candidates">
      {slots.map((s:any,i:number)=><div className="card" key={s.side}>
        <label><input type="radio" name={sourceId+key} disabled={locked || !complete || !Object.hasOwn(contents[i],key)}
          checked={pick(key)===s.version_id && !!s.version_id} onChange={()=>setChoices({...choices,[key]:s.version_id})}/>取{s.side}稿{fields[key]}</label>
        <div className="field">{typeof contents[i][key]==="string"?contents[i][key]:JSON.stringify(contents[i][key],null,2) || "尚无内容"}</div></div>)}
    </div></div>)}
    {complete && <button className="btn" disabled={locked || act.busy || new Set(keys.map(pick)).size!==2}
      onClick={()=>act.run(async()=>{const res=await write("combine",{artifact_id:art.id,picks:Object.fromEntries(keys.map(k=>[k,pick(k)]))});
        reload();open(res.versionId);})}>组合为新的待审故事稿</button>}
    <ErrorLine error={act.error}/>
  </section>;
}
