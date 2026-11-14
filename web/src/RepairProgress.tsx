import { useEffect, useRef, useState } from "react";
import { cmd } from "./api";
import { ErrorLine, PROVIDER_LABEL, pickable, rno, useAction, usePoll, useQuery } from "./ui";
import { FILM_STAGES } from "./workflow";

const names=Object.fromEntries(FILM_STAGES);
const actions:Record<string,string>={revise:"返修",check:"核对",verify:"复核",early:"结构体检"};
const states:Record<string,string>={pending:"待处理",running:"执行中",adopt:"待采用",done:"完成"};

function resetTime(run:any) {
  const value=run?.quota_resets_at;
  if(!value)return "";
  const duration=/^in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(value);
  const epoch=run.ended_at ?? run.started_at;
  const date=duration && epoch ? new Date((epoch+Number(duration[1]||0)*3600+Number(duration[2]||0)*60+Number(duration[3]||0))*1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
}

export function RepairProgress({view,stage,reload}:{view:any;stage:string;reload:()=>Promise<void>}) {
  const eid=view.episode.id;
  const signature=view.artifacts.map((a:any)=>a.adopted_version_id+":"+(a.tasks.at(-1)?.runs.at(-1)?.status||"")).join("/");
  const work=useQuery<any>("stress_worklist",{episode_id:eid},[stage,signature]);
  const [choices,setChoices]=useState<Record<string,string>>({});
  const [notes,setNotes]=useState<Record<string,string>>({});
  const [confirmRound,setConfirmRound]=useState(false);
  const [show,setShow]=useState(false);
  const anchor=useRef<HTMLSpanElement>(null);
  useEffect(()=>{if(!show)return;const close=(e:MouseEvent)=>{if(anchor.current&&!anchor.current.contains(e.target as Node))setShow(false);};
    document.addEventListener("mousedown",close);return()=>document.removeEventListener("mousedown",close);},[show]);
  const act=useAction();
  const plans=work.data?.plans||[];
  const open=plans.find((p:any)=>p.state==="open");
  const step=open?.steps.find((s:any)=>s.state!=="done");
  const paused=step?.state==="running" && step.runStatus==="waiting_quota";
  const catalog=useQuery<any>(paused && stage==="stress"?"model_catalog":null);
  usePoll(!!step && ["queued","running"].includes(step.runStatus),work.reload);
  const reportArt=view.artifacts.find((a:any)=>a.stage==="stress");
  const report=reportArt?.versions.find((v:any)=>v.id===reportArt.adopted_version_id);
  const blockers=work.data?.blockers||[];
  const changes=blockers.filter((b:any)=>b.type==="impact");
  const unresolved=blockers.filter((b:any)=>b.type==="issue");
  const next=async(name:string,args:any)=>{
    try {await cmd(name,args);} finally {await Promise.all([work.reload(),reload()]);}
  };
  if(!work.data)return work.error?<p className="error">{work.error}</p>:null;
  if(stage!=="stress" && !open && !changes.length && !(["asset_design","worksheet"].includes(stage)&&unresolved.length))return null;
  if(stage!=="stress")return <aside className="card repair-link-card"><a href={`#/e/${eid}/stress`}>{open?"继续返修与核对":changes.length?`有 ${new Set(changes.map((b:any)=>b.stage)).size} 个下游阶段需要核对`:"进入 07 前需处理审查问题"}</a></aside>;
  if(!open && !plans.length && !blockers.length && !report?.content?.options?.length)return null;
  const selected=Object.fromEntries(Object.entries(choices).filter(([,v])=>v!=="").map(([key,v])=>[key,Number(v)]));
  const three=Object.keys(selected).some(key=>plans.filter((p:any)=>p.report_version_id===report?.id && p.steps.some((s:any)=>s.findings?.some((f:any)=>f.id===key && f.status==="仍存在"))).length>=3);
  const target=view.artifacts.find((a:any)=>a.stage===step?.stage);
  const run=target?.tasks.flatMap((t:any)=>t.runs).find((r:any)=>r.id===step?.runId);
  const retry=(provider?:string)=>act.run(()=>next("retry_run",{run_id:step.runId,provider}));
  const provider=PROVIDER_LABEL[run?.provider] ?? run?.provider ?? "当前执行器";
  const reset=resetTime(run);
  const shown=!open && report ? report.content.issues.filter((issue:any)=>unresolved.some((b:any)=>b.id===issue.id) || report.content.options.some((o:any)=>o.issueId===issue.id)) : [];
  const createButton=!open && report?.content.options?.length>0 && <footer className="repair-footer">
      <span className="muted">{Object.keys(selected).length ? `已选 ${Object.keys(selected).length} 个返修方案` : "为要修的问题选一个返修方案；不选的本单不修"}</span>
      {three && <label><input type="checkbox" checked={confirmRound} onChange={e=>setConfirmRound(e.target.checked)}/>已核对三轮差异，决定再修一轮</label>}
      <button className="btn primary" disabled={act.busy||!Object.keys(selected).length||(three&&!confirmRound)} onClick={()=>act.run(async()=>{await next("start_repair_plan",{episode_id:eid,mode:"report",report_version_id:report.id,choices:selected,confirm_new_round:confirmRound});setChoices({});setConfirmRound(false);})}>按所选方案建立返修单</button></footer>;
  // 实测 138：不占正文位置，只在版本行末尾放一个按钮，点开浮层处理；有待你处理的事项时按钮变黄。内容不变，专家与流程照常读取。
  const needsYou=open ? (step?.state==="adopt" || paused || ["failed","stopped"].includes(step?.runStatus)) : unresolved.length>0;
  const status=open ? (step?.state==="adopt" ? "返修稿待你采用" : paused ? "额度用尽，已暂停" : ["failed","stopped"].includes(step?.runStatus) ? "返修执行失败，需要处理" : "返修单进行中")
    : unresolved.length ? `${unresolved.length} 个问题必须处理后才能进入 07` : changes.length ? "下游阶段需要核对" : shown.length ? `独审提出 ${shown.length} 个可选修改` : "历史返修记录";
  return <span className="repair-anchor" ref={anchor}><button className={"pill repair-pill"+(needsYou?" attention":"")} aria-expanded={show} title={status} onClick={()=>setShow(!show)}>返修与核对{needsYou?` · ${open?1:unresolved.length} 待处理`:""}</button>
    {show && <div className="repair-progress" role="dialog" aria-label="返修与核对">
      <header className="repair-toolbar"><h3>返修与核对</h3><span className="repair-count">{status}</span></header>
    {open && <><ol>{open.steps.map((s:any)=><li key={s.stage}>{names[s.stage]} · {actions[s.action]} · {(s.state==="running" && ({waiting_quota:"等待额度",failed:"执行失败",stopped:"已停止",queued:"已排队"} as Record<string,string>)[s.runStatus]) || states[s.state]} {s.versionId && rno(view.artifacts.find((a:any)=>a.stage===s.stage)?.versions.find((v:any)=>v.id===s.versionId)?.display_no)} {s.note}</li>)}</ol>
      {paused ? <div role="status"><p>{provider} 额度已用尽，{names[step.stage]}核对已暂停。{reset && <>服务商预计 {reset} 恢复额度。</>}</p>
        <div className="row wrap"><button className="btn" disabled={act.busy} onClick={()=>retry()}>额度恢复后重试</button>
          {run && catalog.data?.quotaRetryPolicy==="manual_cli" && pickable(Object.keys(catalog.data.text)).filter(p=>p!==run.provider).map(p=>
            <button key={p} className="btn" disabled={act.busy} onClick={()=>retry(p)}>换 {PROVIDER_LABEL[p] ?? p} 继续</button>)}
        </div></div> : step?.state==="adopt" ? <button className="btn primary" disabled={act.busy} onClick={()=>act.run(()=>next("adopt",{artifact_id:target.id,version_id:step.versionId,expected_adopted_version_id:target.adopted_version_id}))}>采用 {rno(target.versions.find((v:any)=>v.id===step.versionId)?.display_no)} 并继续</button> :
       step && (step.state==="pending"||["failed","stopped","waiting_quota"].includes(step.runStatus)) && <button className="btn primary" disabled={act.busy} onClick={()=>act.run(()=>next("advance_repair_plan",{plan_id:open.id}))}>继续{actions[step.action]}</button>}
      {step?.versionId && <a className="btn" href={`#/e/${eid}/${step.stage}?v=${step.versionId}`}>查看返修稿</a>}
      {step?.diagnostic && <details><summary>查看技术详情</summary><pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{step.diagnostic}</pre></details>}</>}
    {!open && changes.length>0 && <div className="repair-downstream"><div>{changes.map((b:any)=><p key={b.id}>{b.text}</p>)}</div><button className="btn" disabled={act.busy} onClick={()=>act.run(()=>next("start_repair_plan",{episode_id:eid,mode:"upstream"}))}>逐步核对下游采用稿</button></div>}
    {!open && report && report.content.issues.map((issue:any)=>{
      const options=report.content.options.filter((o:any)=>o.issueId===issue.id);
      const required=unresolved.some((b:any)=>b.id===issue.id);
      if(!required && !options.length)return null;
      const noteKey=report.id+":"+issue.id;
      const note=notes[noteKey];
      return <article className="repair-issue-row" key={issue.id}><p><strong>{issue.id}</strong> {issue.problem}</p><div className="repair-controls"><label>返修方案<select aria-label={issue.id+" 返修方案"} value={choices[issue.id]??""} onChange={e=>setChoices({...choices,[issue.id]:e.target.value})}>
        <option value="">本单不修</option>{options.map((o:any,i:number)=><option key={i} value={i}>{o.proposal}</option>)}</select></label>
        {required && !["source","format","decision","required_setting"].includes(issue.category) && <>
          <label className="repair-note-toggle"><input type="checkbox" checked={note!==undefined} onChange={e=>{const updated={...notes};if(e.target.checked)updated[noteKey]="";else delete updated[noteKey];setNotes(updated);}}/>添加备注</label>
          {note!==undefined && <label>备注<input autoFocus value={note} onChange={e=>setNotes({...notes,[noteKey]:e.target.value})}/></label>}
          <button className="btn" disabled={act.busy} onClick={()=>act.run(()=>next("carry_stress_issue",{episode_id:eid,report_version_id:report.id,issue_id:issue.id,quote:note?.trim()||"",scope:""}))}>带着问题制作</button></>}</div>
      </article>;
    })}
    {createButton}
    {plans.filter((p:any)=>p.state!=="open").map((p:any)=><details key={p.id}><summary>{new Date(p.created_at*1000).toLocaleString()} · {p.state==="unresolved"?"仍有问题":p.state==="abandoned"?"已由新报告取代":"已完成"}</summary>
      {p.steps.map((s:any)=><section key={s.stage}><b>{names[s.stage]} · {actions[s.action]}</b><p>{s.note}</p>{Array.isArray(s.result) ? s.result.map((r:any)=><p key={r.id}><b>{r.id} {r.status}</b> {r.note}{r.quote && <q>{r.quote}</q>}</p>) :
      s.result && <><p>{s.result.scope} · {s.result.retelling}</p><p>{s.result.diagnosis}</p>{s.result.issues?.map((i:any)=><p key={i.id}>{i.id} · {i.problem} {i.evidence}</p>)}{s.result.options?.map((o:any,n:number)=><p key={n}>{o.issueId} · {o.proposal}</p>)}</>}</section>)}</details>)}
    <ErrorLine error={act.error}/>
  </div>}</span>;
}
