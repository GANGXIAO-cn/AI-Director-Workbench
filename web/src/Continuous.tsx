import { ModelReply } from "./ModelReply";
import { ContinuousRecoveryNote } from "./ContinuousRecovery";
import {ImageChoice} from "./ImageCompare";
import {DirectionChoice, KickoffChoice} from "./Directions";
import { ProjectMethodSync } from "./UploadedMethods";
import { cmd } from "./api";
import { ErrorLine, useAction, PROVIDER_LABEL } from "./ui";
import { FILM_STAGES } from "./workflow";

export function ContinuousStatus({data,error,reload}:{data:any;error:string|null;reload:()=>void|Promise<void>}) {
  const act=useAction();
  if(!data) return error?<ErrorLine error={{message:error}}/>:null;
  const run=data.run;
  const execute=(name:string,target_stage="stress")=>act.run(async()=>{await cmd(name,run && name!=="start_continuous"?{continuous_id:run.id}:{episode_id:data.episodeId,target_stage});await reload();});
  const stage=({assets:"07 出图",delivery:"图文交付",package:"H3 出包"} as Record<string,string>)[run?.cursor?.stage]??FILM_STAGES.find(([s])=>s===run?.cursor?.stage)?.[1]??"01 故事方向";
  const progress=run?.cursor?.progress;
  const switched=run?.cursor?.queue?.includes(run?.cursor?.runId)?run?.cursor?.formatSwitches?.[run?.cursor?.runId]:null;
  const action=switched?`换 ${PROVIDER_LABEL[switched.to]??switched.to} 重试 · 正在生成`:run?.cursor?.phase==="judgment"?(run.cursor.judgmentKind || "正在判断"):run?.cursor?.waitingRun?"正在生成，完成后自动继续":run?.cursor?.phase==="images"?`正在生成与评估 ${progress?.current??0}/${progress?.total??0} 张`:run?.cursor?.phase==="review"?`09 独审 ${progress?.current??0}/${progress?.total??0} 段`:run?.cursor?.phase==="repair"?"返修":run?.cursor?.phase==="judgment"?(run.cursor.judgmentKind==="素材用途匹配"?"正在匹配素材用途":run.cursor.judgmentKind || "正在判断"):"起草与检查";
  // 实测 124：显示本步已用时间（阶段开始或最近一次恢复运行起算），不是从创建起的总时长。
  const since=run?Math.max(run.cursor?.stageStartedAt ?? 0, run.cursor?.resumedAt ?? 0) || run.created_at:0;
  const elapsed=run?Math.max(0,Math.floor(Date.now()/1000-since)):0;
  const clock=`${Math.floor(elapsed/60).toString().padStart(2,"0")}:${(elapsed%60).toString().padStart(2,"0")}`;
  // 实测 133：操作按钮并排一行，避免状态条变高后把图片选择卡挤出面板。
  const endButton=run && !["done","cancelled"].includes(run.state)?<button className="btn ghost" disabled={act.busy} onClick={()=>execute("cancel_continuous")}>结束连续制作</button>:null;
  const issues=(data.issues??[]).filter((i:any)=>['provisional','missing','accepted'].includes(i.state));
  return <section className="continuous-status" aria-label="连续制作进度">
    {data.execution && <ModelReply provider={data.execution.provider} model={data.execution.reported_model} requestedModel={data.execution.requested_model}><span className="tiny muted">{run?.state==="running"?"当前任务":"最近任务"} · {run?.cursor?.phase==="judgment"?run.cursor.judgmentKind:data.execution.purpose==="revise"?"新返修任务":data.execution.purpose==="create"?"起草任务":"检查任务"}{data.execution.status==="succeeded"?" · 回包已保存":data.execution.status==="failed"?" · 执行失败":""}</span></ModelReply>}
    {data.expert&&<p className="small continuous-expert">专家：{PROVIDER_LABEL[data.expert.provider]??data.expert.provider} · {data.expert.model??"默认型号"}</p>}
    {run?.cursor.expertUnavailable&&<p className="small">专家不可用，按现有流程处理 · <a href="#/sys/executors">连接设置</a></p>}
    {!!issues.length&&<details><summary>未解决 {issues.filter((i:any)=>i.state==='provisional').length} 条 · 缺项 {issues.filter((i:any)=>i.state==='missing').length} 项</summary>
      {issues.map((i:any)=><div key={i.id}><b>{i.title} · {i.state==='accepted'?'导演已接受':i.state==='missing'?'缺项':'AI 暂用，未解决'}</b><p>{i.text}</p>
        {i.state==='provisional'&&<button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd('accept_material_issue',{issue_id:i.id});await reload();})}>保留这个问题</button>}
        <a className="btn" href={`#/e/${data.episodeId}/assets`}>查看素材</a></div>)}
    </details>}
    {run?.mode==="fix" && run.cursor.fixStatus && <p>{run.cursor.fixStatus}</p>}
    {run?.cursor.kickoffAdjustment && <div className="kickoff-adjustment"><p>调整前：{run.cursor.kickoffAdjustment.before}</p><p>调整后：{run.cursor.kickoffAdjustment.after}</p></div>}
    {!run || ["done","cancelled"].includes(run.state)?<>
      {run && <p>{run.reason}</p>}
      <b className="continuous-start-title">交给 AI 往下做</b>
      <div className="continuous-start">
        <button className="btn" disabled={act.busy} onClick={()=>execute("start_continuous")}>继续做到 06 故事审查</button>
        <button className="btn primary" disabled={act.busy} onClick={()=>execute("start_continuous","delivery")}>继续做到图文交付</button>
      </div>
      <p className="muted small">AI 从当前阶段起草、检查并采用各阶段稿件；需要取舍时给你具体稿件选。不点则仍由你逐阶段手动推进。</p>
      {data.methodNotice && <div><p>{data.methodNotice}</p><details><summary>查看本集方法</summary>{FILM_STAGES.slice(0,6).map(([stage])=><ProjectMethodSync key={stage} pid={data.projectId} eid={data.episodeId} stage={stage}/>)}</details></div>}
    </>:run.state==="running"?<>
      <p>AI 正在：{stage} · {action} · 本步已用 {clock}</p>
      <div className="row continuous-actions"><button className="btn" disabled={act.busy || !!run.pause_requested} onClick={()=>execute("pause_continuous")}>{run.pause_requested?"本次调用结束后暂停":"暂停"}</button>{endButton}</div>
    </>:run.state==="waiting_choice"?(["visual","image_failed"].includes(run.choice?.kind)?<div className="row continuous-actions"><button className="btn primary" disabled={act.busy} title="不用在这里选，交给 AI 按当前候选处理并继续" onClick={()=>execute("resume_continuous")}>交给 AI 继续</button>{endButton}</div>:<><p>稿件已准备好，请在下方选择。</p>{endButton}</>):<>
      <p>{run.reason}</p><ContinuousRecoveryNote run={run} all/><div className="row continuous-actions"><button className="btn primary" disabled={act.busy} onClick={()=>execute("resume_continuous")}>继续</button>{endButton}</div>
    </>}
    <ErrorLine error={act.error}/>
  </section>;
}

export function ContinuousChoice({run,eid,pid,onCompare,reload}:{run:any;eid:string;pid:string;onCompare:(route:string)=>void;reload:()=>void|Promise<void>}) {
  const act=useAction();
  if(!run?.choice || !["waiting_choice","blocked"].includes(run.state)) return null;
  const choice=run.choice;
  const choose=(key:string)=>act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:key});await reload();});
  if(choice.kind==="preview")return <section className="mcard continuous-choice preview-choice" data-chat-kind="choice">
    <h3>{choice.title}</h3>
    <div className="choice-buttons"><a className="btn" target="_blank" rel="noreferrer" href={`/api/delivery-file/${choice.deliveryId}/${encodeURIComponent("00_图文对照.html")}`}>打开图文交付页</a>
    <button className="btn primary" disabled={act.busy} onClick={()=>choose("satisfied")}>满意，完成交付</button></div>
    <p className="choice-hint">不满意？直接在下方说哪里不喜欢</p>
    {choice.reply && <p>{choice.reply}</p>}<ErrorLine error={act.error}/>
  </section>;
  if(["visual","image_failed"].includes(choice.kind))return <ImageChoice run={run} eid={eid} onCompare={onCompare} reload={reload}/>;
  if(choice.kind==="direction")return <DirectionChoice run={run} onCompare={onCompare} reload={reload}/>;
  if(choice.kind==="kickoff_conflict")return <KickoffChoice run={run} pid={pid} reload={reload}/>;
  return <section className="mcard continuous-choice" data-chat-kind="choice">
    <div className="choice-eyebrow">需要你选一下</div><h3>{choice.title}</h3>
    {choice.current && <div className="continuous-option"><b><span className="tag">当前版本</span></b><p>{choice.current.disabledReason || "保持现状，带着问题继续"}</p></div>}
    {choice.candidates.map((c:any)=><div className="continuous-option" key={c.key}><b><span className="tag">{c.key}</span> {c.recommended && <span className="tag">推荐</span>} {c.label}</b><p>✓ {c.check}</p></div>)}
    {(choice.candidates.length>0 || choice.current) && <button className="btn primary compare-open" onClick={()=>onCompare(`#/e/${eid}/${choice.stage}?compare=${run.id}`)}>并排比较</button>}
    <div className="choice-buttons">
      {choice.current && <button className="btn" disabled={act.busy || !choice.current.adoptable || run.state!=="waiting_choice"} onClick={()=>choose("current")}>{choice.current.adoptable?"保持现状，带着问题继续":"当前版本仅供对照"}</button>}
      {choice.candidates.map((c:any)=><button key={c.key} className={"btn "+(c.recommended?"primary":"")} disabled={act.busy || run.state!=="waiting_choice"} onClick={()=>choose(c.key)}>用 {c.key} 这一版</button>)}
    </div>
    {choice.settings && <a className="btn" href={`#/p/${pid}/settings?reason_default=${encodeURIComponent("连续制作：两份候选都与此设定冲突，导演选择修改设定")}`}>修改这条设定</a>}
    <p className="choice-hint">都不满意？直接在下方输入框说想法</p><ErrorLine error={act.error}/>
  </section>;
}
