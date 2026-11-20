import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useProjectRoute } from "./projectNavigation";
import { WorkbenchDialog } from "./Drawer";
import { videoApi } from "./api";
import { ErrorLine, useAction } from "./ui";

import {ReworkNotes,type Rework} from "./ReworkNotes";

type Timing = {startedAt?:number;endedAt?:number;observedOnly?:boolean;retryMessage?:string;nextCheck?:number};
function TaskElapsed({task,local=false}:{task?:Timing;local?:boolean}) {
  const [now,setNow]=useState(()=>Date.now()/1000);
  const ticking=Boolean(task?.startedAt && (!task.endedAt || task.retryMessage));
  useEffect(()=>{
    if(!ticking)return;
    const tick=()=>setNow(Date.now()/1000);
    tick();
    const id=window.setInterval(tick,1000);
    return ()=>window.clearInterval(id);
  },[ticking,task?.startedAt]);
  if(!task?.startedAt)return null;
  const seconds=Math.max(0,Math.floor((task.endedAt??now)-task.startedAt));
  const clock=Math.floor(seconds/60)+":"+String(seconds%60).padStart(2,"0");
  const label=local?"精剪用时":task.observedOnly?"自首次查询起":"接单后用时";
  const retryIn=Math.max(0,Math.ceil((task.nextCheck??now)-now));
  return <div className="small muted"><span role="timer" aria-live="off" className="run-elapsed"
    title={local?"从开始精剪到本轮结束":task.observedOnly?"从恢复跟踪此任务时开始记录":"从取得任务号到确认云端结束，含云端排队"}>
    {label} {clock}</span>{task.retryMessage && <div>{task.retryMessage} · {retryIn?retryIn+" 秒后重查":"正在重查"}</div>}</div>;
}

type Ref = {name:string;protected:boolean};
type Binding = {source:string; slot:number; seconds:number|null; note:string};
type Job = {action?:string;cloud?:Timing;reworks?:Rework[];changes?:string[];segment:string; state:string; deps:Binding[]; bindings?:(Binding & {plannedSlot?:number})[]; skipped?:{slot:number;reason:string}[]; round:number; account:string|null; error:string; review?:{reason:string;cutAdvice?:string;join?:string}};
type Profile = {name:string;site:string;instance:string;capacity:number;pool:string;poolCapacity:number;connected:boolean};
type Music = {action?:string;cloud?:Timing;state:string;error:string;audio:string|null;handoff:string|null};
type CutInputs = {clips:{segment:string;review:{usableStart:number;usableEnd:number;cutAdvice?:string}}[]};
type Status = {cut?:Timing & {action?:string;state?:string;published?:string;error?:string;feedbackReview?:string;finalReview?:{review:{reason:string};decision:{reason:string};audio:string}};start?:{state:string;error:string};budget?:{used:number;limit:number;reworkUsed?:number;reworkLimit?:number;reserved?:number;counts?:Record<string,number>}|null;music?:Music|null;jobs:Job[];accounts:Profile[];paused:boolean};

const emptyStatus:Status={jobs:[],accounts:[],paused:false};
function useFilmStatus(delivery:string|null) {
  const {active}=useProjectRoute();
  const [snapshot,setSnapshot]=useState<{delivery:string;data:Status}|null>(null);
  const [error,setError]=useState("");
  const sequence=useRef(0);
  const setStatus=(data:Status)=>{sequence.current++;if(delivery)setSnapshot({delivery,data});};
  useEffect(()=>{
    setError("");
    if(!active || !delivery)return;
    let live=true;
    let timer:number;
    const refresh=async()=>{
      const order=++sequence.current;
      try {
        const data=await videoApi<Status>("GET",encodeURIComponent(delivery));
        if(live && order===sequence.current){setSnapshot({delivery,data});setError("");}
      } catch(e){if(live)setError(String(e));}
      if(live)timer=window.setTimeout(refresh,5000);
    };
    void refresh();
    return ()=>{live=false;clearTimeout(timer);};
  },[delivery,active]);
  return {delivery,status:snapshot?.delivery===delivery?snapshot?.data:undefined,setStatus,error};
}
const FilmContext=createContext<ReturnType<typeof useFilmStatus>|null>(null);
export function FilmStatusScope({delivery,children}:{delivery:string|null;children:ReactNode}) {
  const session=useFilmStatus(delivery);
  return <FilmContext.Provider value={session}>{children}</FilmContext.Provider>;
}

function FilmProgress({status,run,busy}:{status:Status;run:(path:string)=>void;busy:boolean}) {
  const {jobs,cut,music}=status;
  const produced=jobs.filter(j=>(status.budget?.counts?.[j.segment]??0)>0).length;
  const adopted=jobs.filter(j=>j.state==="已采用" || j.state==="已出片待选剪").length;
  const working=jobs.filter(j=>j.state!=="已采用" && j.state!=="已出片待选剪" && (j.state!=="排队中" || j.deps.length>0 || j.error));
  if(!jobs.length && !music && !status.start)return null;
  const cutAction=cut?.action || (cut?.state==="渲染中"?"工作台正在处理精剪":cut?.state);
  return <section className="film-progress" aria-label="本集交付进度">
    <div className="spread"><strong>{status.paused?"已暂停新派发":cutAction || (music?.handoff?"精剪已收到素材":status.start?.state==="待开工"?"工作台正在安排视频与配乐":"本集制作进度")}</strong>
      {cut?.state!=="已出片" && <button className="btn" disabled={busy} onClick={()=>run("pause/"+!status.paused)}>{status.paused?"恢复本集派发":"暂停本集派发"}</button>}</div>
    {!music && status.start && status.start.state!=="待开工" && <div>{status.start.state} {status.start.error}</div>}
    {jobs.length>0 && <div className="small">已出片 {produced} / {jobs.length} 段 · 已采用 {adopted} 段{music && " · 配乐："+(music.action || music.state)}</div>}
    {!status.paused && working.map(j=><div className="small" key={j.segment}>{j.segment} · {j.action || j.state}{j.error && j.error!==j.action && " · "+j.error}
      {["待核对","需处理"].includes(j.state) && <button className="btn" disabled={busy} onClick={()=>run(encodeURIComponent(j.segment)+"/retry")}>恢复 {j.segment} 原任务</button>}</div>)}
    {!status.paused && !working.length && jobs.some(j=>j.state==="排队中") && <div className="small">视频等待派发</div>}
    {!status.paused && music?.error && music.state!=="云端失败" && <button className="btn" disabled={busy} onClick={()=>run("bgm/retry")}>恢复配乐任务</button>}
    {cut?.state==="修改受阻" && <button className="btn" disabled={busy} onClick={()=>run("finecut/undo")}>撤销上一条修改并重试</button>}
    {cut?.error && <div className="small">{cut.error}</div>}
    {status.paused && <div className="small">已经提交的工作可以收尾，恢复后继续下一步。</div>}
  </section>;
}
export function SharedFilmProgress() {
  const session=useContext(FilmContext);
  const act=useAction();
  if(!session?.delivery)return null;
  const {status,delivery,setStatus,error}=session;
  return <>{status && <FilmProgress status={status} busy={act.busy} run={path=>act.run(async()=>{
    const base=encodeURIComponent(delivery);
    await videoApi("POST",base+"/"+path);
    setStatus(await videoApi<Status>("GET",base));
  })}/>}<ErrorLine error={act.error || (error?{message:error}:null)}/></>;
}

export function VideoProduction({delivery,episode,full=false,finecut=false,automatic=false,onProgress}:{delivery:string;episode:string;full?:boolean;finecut?:boolean;automatic?:boolean;onProgress?:()=>void}) {
  const shared=useContext(FilmContext);
  const isShared=shared?.delivery===delivery;
  const local=useFilmStatus(isShared?null:delivery);
  const session=isShared?shared:local;
  const status=session.status??emptyStatus;
  const {setStatus}=session;
  const {active,go}=useProjectRoute();
  const [cut,setCut]=useState<CutInputs|null>(null);
  const wasWaiting=useRef(false);
  const [refs,setRefs]=useState<Record<string,Ref[]>>({});
  const [draft,setDraft]=useState<Record<string,Binding[]>>({});
  const [manual,setManual]=useState<string[]>([]);
  const [selected,setSelected]=useState<string[]>([]);
  const [preview,setPreview]=useState<{segment:string;version:string;label:string}|null>(null);
  const [edit,setEdit]=useState("");
  const [loadError,setLoadError]=useState("");
  const act=useAction();
  const progress=useRef<string|undefined>(undefined);
  const notify=useRef(onProgress);
  notify.current=onProgress;
  const base=encodeURIComponent(delivery);
  const segments=Object.keys(refs);
  const job=(seg:string)=>status.jobs.find(j=>j.segment===seg);
  const deps=(seg:string)=>draft[seg]??job(seg)?.deps??[];
  const save=(seg:string,list:Binding[])=>{
    const count=(refs[seg]??[]).length;
    const appended=list.filter(d=>d.slot>count).map(d=>d.slot).sort((a,b)=>a-b);
    const numbered=list.map(d=>d.slot<=count?d:{...d,slot:count+1+appended.indexOf(d.slot)});
    setDraft(old=>({...old,[seg]:numbered}));
    setManual(old=>[...new Set([...old,seg])]);
  };
  useEffect(()=>{
    let active=true;
    videoApi<Record<string,Ref[]>>("GET",base+"/references").then(v=>{if(active)setRefs(v);}).catch(e=>{if(active)setLoadError(String(e));});
    return ()=>{active=false;};
  },[base]);
  useEffect(()=>{
    if(!session.status)return;
    const next=JSON.stringify([status.music?.handoff,status.cut?.state,status.cut?.published,status.start?.state]);
    if(progress.current!==undefined && progress.current!==next)notify.current?.();
    progress.current=next;
  },[session.status,status]);
  const early=segments.slice(0,segments.indexOf(edit));
  const available=(refs[edit]??[]).map((r,i)=>({slot:i+1,label:`图 ${i+1} · ${r.name}`,disabled:r.protected}));
  const occupied=new Set(deps(edit).map(d=>d.slot));
  const originalCount=available.length;
  const lastAppend=Math.min(9,Math.max(originalCount,...occupied)+1);
  for(let n=originalCount+1;n<=lastAppend;n++) available.push({slot:n,label:`追加图 ${n}`,disabled:false});
  async function change(path:string,body?:unknown) {
    setStatus(await videoApi<Status>("POST",base+"/"+path,body));
  }
  const cutUrl=`#/e/${episode}/finecut?delivery=${base}`;
  useEffect(()=>{
    if(status.music && !status.music.handoff) wasWaiting.current=true;
    if(active && !finecut && wasWaiting.current && status.music?.handoff){
      wasWaiting.current=false;go(cutUrl);
    }
  },[active,finecut,status.music?.handoff,cutUrl,go]);
  useEffect(()=>{
    let live=true;
    if(finecut && status.music?.handoff) videoApi<CutInputs>("GET",base+"/finecut").then(v=>{if(live)setCut(v);}).catch(e=>{if(live)setLoadError(String(e));});
    return ()=>{live=false;};
  },[finecut,base,status.music?.handoff]);
  async function enqueue(targets:string[]){
    const missing=targets.filter(s=>segments.indexOf(s)>0 && draft[s]===undefined && !job(s));
    const suggested=missing.length ? await videoApi<(Binding&{target:string})[]>("POST",base+"/plan") : [];
    await change("queue",targets.map(segment=>({segment,automatic:!manual.includes(segment),dependencies:missing.includes(segment)
      ? suggested.filter(d=>d.target===segment).map(({target,...b})=>b) : deps(segment)})));
    setSelected([]);setDraft({});setManual([]);setEdit("");
  }
  if(finecut) return <section className="card">
    {status.cut?.published && <video key={status.cut.published} controls playsInline preload="metadata"
      aria-label="本集精剪成片" style={{maxWidth:"100%",maxHeight:"65vh"}} src={`/api/video/${base}/final?v=${status.cut.published}`}/>}
    <TaskElapsed task={status.cut} local/>
    {status.cut?.finalReview && <details><summary>成片复核与交片决定</summary><p>独立评审 · {status.cut.finalReview.review.reason}</p><p>专家 · {status.cut.finalReview.decision.reason}</p><p>{status.cut.finalReview.audio}</p></details>}
    {status.cut?.feedbackReview && <p>修改验收 · {status.cut.feedbackReview}</p>}
    {cut ? <><h3>已接收精剪素材</h3><div className="small muted">本集配乐 · {status.music?.action || "音频文件已返回（AI 未试听）"}</div><audio controls src={`/api/video/${base}/bgm`}/>
      {cut.clips.map(c=><p key={c.segment}><a href={`/api/video/${base}/${encodeURIComponent(c.segment)}/video/adopted`}>{c.segment}</a> · 可用 {c.review.usableStart}–{c.review.usableEnd} 秒 {c.review.cutAdvice}</p>)}</>
      : <p>{status.jobs.some(j=>j.state==="已止损")?"本集有分段达到止损条件，精剪等待可用素材。":status.music?.error?"配乐尚未就绪 · "+status.music.error:"专家等待各段成功出片和本集配乐，随后自动选材精剪。"}</p>}
    <ErrorLine error={act.error||(loadError||session.error?{message:loadError||session.error}:null)}/></section>;
  return <section className="card" aria-label="视频生成队列">
    {!isShared && !status.music && status.start && <p role="status">{status.start.state==="待开工"?"正在安排分段承接与整集派发":status.start.state} {status.start.error}</p>}
    {status.budget && <p>成功出片 {status.budget.used} / {status.budget.limit} 版{status.budget.reworkLimit != null && ` · 其中返工 ${status.budget.reworkUsed} / ${status.budget.reworkLimit} 次`}{Boolean(status.budget.reserved) && ` · 已预留 ${status.budget.reserved} 次`} · 每段最多返工 3 次</p>}
    {status.music && <div><b>本集 BGM · {status.music.action || status.music.state}{status.music.audio && !(status.music.action||"").includes("未试听") && " · AI 未试听"}</b>
      <TaskElapsed task={status.music.cloud}/>
      {status.music.audio && <audio controls preload="metadata" aria-label="本集配乐" src={`/api/video/${base}/bgm`}/>}
      {status.music.error && <p>{status.music.error}</p>}
      {status.music.handoff && <a href={cutUrl}>查看精剪素材</a>}</div>}
    {!automatic && <details><summary>调整派发</summary><div className="row" style={{marginTop:12}}>
      <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
        const plan=await videoApi<(Binding&{target:string})[]>("POST",base+"/plan");
        const changes:Record<string,Binding[]>={};
        for(const s of segments) if(!job(s)||job(s)?.state==="排队中") changes[s]=plan.filter(d=>d.target===s).map(({target,...b})=>b);
        setDraft(changes);setManual([]);
      })}>专家预排承接</button>
    </div></details>}
    <table className="package-table"><thead><tr><th>生成段</th><th>参考来自</th><th>进度</th><th>审片与剪点</th></tr></thead><tbody>
      {segments.map(s=>{const j=job(s);const editable=!automatic && !full && (!j||j.state==="排队中");return <tr key={s}>
        <td><label>{!automatic && !full && <input type="checkbox" disabled={!editable} checked={selected.includes(s)} onChange={e=>setSelected(e.target.checked?[...selected,s]:selected.filter(x=>x!==s))}/>} {s}</label></td>
        <td>{deps(s).filter(d=>!j?.skipped?.some(b=>b.slot===d.slot) && !j?.bindings?.some(b=>(b.plannedSlot??b.slot)===d.slot)).map(d=><div key={d.slot}>{d.source} → 图 {d.slot}{d.seconds!==null&&` · ${d.seconds} 秒`}</div>)}
          {j?.skipped?.map(b=><div key={b.slot}>原计划图 {b.slot} · {b.reason}</div>)}
          {j?.bindings?.map(b=><a key={b.slot} href={`/api/video/${base}/${encodeURIComponent(s)}/reference/${b.slot}`} target="_blank" rel="noreferrer">{b.source} → 图 {b.slot} · 看来源帧（{b.seconds} 秒）</a>)}
          {editable&&segments.indexOf(s)>0&&<button className="btn" onClick={()=>setEdit(edit===s?"":s)}>编辑承接</button>}</td>
        <td>{j?.action || j?.state || "待派发"} {j?.cloud?.startedAt?j.account:""} {j&&j.round>0&&` · 返工 ${j.round}/3`}<TaskElapsed task={j?.cloud}/>{j?.error&&!j.cloud?.retryMessage&&<p>{j.error}</p>}
        </td>
        <td>{j && <ReworkNotes segment={s} notes={j.reworks??(j.changes??[]).map((change,i)=>({number:i+1,change}))}/>}
          {j?.review&&<details><summary>{j.review.join??"查看审片"}</summary><p>{j.review.reason}</p><p>{j.review.cutAdvice}</p></details>}
          {j?.state==="已采用"&&<button className="btn" onClick={()=>setPreview({segment:s,version:"adopted",label:"采用版"})}>预览采用版</button>}
</td>
      </tr>;})}
    </tbody></table>
    {edit&&<fieldset><legend>{edit} 的状态参考</legend>
      {deps(edit).map((d,i)=>{const patch=(val:Partial<Binding>)=>save(edit,deps(edit).map((x,n)=>n===i?{...d,...val}:x));return <div key={i} className="row">
        <label>来源段<select value={d.source} onChange={e=>patch({source:e.target.value})}>{early.map(s=><option key={s}>{s}</option>)}</select></label>
        <label>目标图位<select value={d.slot} onChange={e=>patch({slot:Number(e.target.value)})}>{available.map(o=><option key={o.slot} value={o.slot} disabled={o.disabled||(o.slot!==d.slot && occupied.has(o.slot))}>{o.label}</option>)}</select></label>
        <label>原片秒数<input type="number" min="0" step="any" placeholder="专家选帧" value={d.seconds??""} onChange={e=>patch({seconds:e.target.value===""?null:Number(e.target.value)})}/></label>
        <label>要沿用的状态<input value={d.note} onChange={e=>patch({note:e.target.value})}/></label>
        <button className="btn" onClick={()=>save(edit,deps(edit).filter((_,n)=>n!==i))}>移除此承接</button></div>;})}
      <button className="btn" onClick={()=>save(edit,[])}>设为独立段</button>
      <button className="btn" disabled={!available.some(o=>!o.disabled&&!deps(edit).some(d=>d.slot===o.slot))} onClick={()=>{
        const o=available.find(v=>!v.disabled&&!deps(edit).some(d=>d.slot===v.slot));
        if(o)save(edit,[...deps(edit),{source:early[early.length-1],slot:o.slot,seconds:null,note:""}]);
      }}>添加来源</button>
    </fieldset>}
    {!automatic && !full && <button className="btn" disabled={act.busy||!selected.length} onClick={()=>act.run(()=>enqueue(selected))}>
      所选 {selected.length} 段试跑</button>}
    {preview&&<WorkbenchDialog placement="center" label={`${preview.segment} · ${preview.label}`} close={()=>setPreview(null)}>
      <video className="segment-video-preview" controls autoPlay playsInline
        aria-label={`${preview.segment} 视频预览`}
        src={`/api/video/${base}/${encodeURIComponent(preview.segment)}/video/${preview.version}`}/>
    </WorkbenchDialog>}
    <ErrorLine error={act.error||(loadError||session.error?{message:loadError||session.error}:null)}/>
  </section>;
}
