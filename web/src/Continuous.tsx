import { ModelReply } from "./ModelReply";
import { ContinuousRecoveryNote } from "./ContinuousRecovery";
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
  const imaging=["images","image_review","image_fix"].includes(run?.cursor?.phase);
  const requests: {provider:string;model:string;state:string;n:number}[]=data.imageProgress?.requests??[];
  const channelNames:Record<string,string>={"openai-image-api":"OpenAI 图片 API"};
  const requestStates:Record<string,string>={succeeded:"已出图",submitted:"生成中",pending:"排队中",failed:"失败",unknown:"结果待核实",confirmed_not_executed:"确认未执行"};
  const switched=run?.cursor?.queue?.includes(run?.cursor?.runId)?run?.cursor?.formatSwitches?.[run?.cursor?.runId]:null;
  const action=run?.cursor?.phase==="plan_review"?"分镜定稿独审":run?.cursor?.phase==="image_review"?"整集图片统一审核":run?.cursor?.phase==="image_fix"?"按整集审核意见修图":switched?`换 ${PROVIDER_LABEL[switched.to]??switched.to} 重试 · 正在生成`:run?.cursor?.phase==="judgment"?(run.cursor.judgmentKind || "正在判断"):run?.cursor?.waitingRun?"正在生成，完成后自动继续":run?.cursor?.phase==="images"?`素材已采用 ${data.imageProgress?.adopted??progress?.current??0}/${data.imageProgress?.total??progress?.total??0} 项`:run?.cursor?.phase==="review"?`09 独审 ${progress?.current??0}/${progress?.total??0} 段`:run?.cursor?.phase==="repair"?"返修":run?.cursor?.phase==="judgment"?(run.cursor.judgmentKind==="素材用途匹配"?"正在匹配素材用途":run.cursor.judgmentKind || "正在判断"):"起草与检查";
  // 实测 124：显示本步已用时间（阶段开始或最近一次恢复运行起算），不是从创建起的总时长。
  const since=run?Math.max(run.cursor?.stageStartedAt ?? 0, run.cursor?.resumedAt ?? 0) || run.created_at:0;
  const elapsed=run?Math.max(0,Math.floor(Date.now()/1000-since)):0;
  const clock=`${Math.floor(elapsed/60).toString().padStart(2,"0")}:${(elapsed%60).toString().padStart(2,"0")}`;
  // 实测 133：操作按钮并排一行，避免状态条变高后把图片选择卡挤出面板。
  const endButton=run && !["done","cancelled","failed"].includes(run.state)?<button className="btn ghost" disabled={act.busy} onClick={()=>execute("cancel_continuous")}>结束连续制作</button>:null;
  const issues=(data.issues??[]).filter((i:any)=>['provisional','missing','accepted'].includes(i.state));
  return <section className="continuous-status" aria-label="连续制作进度">
    {imaging && data.imageProgress && <div className="small"><b>本集累计出图请求</b>{requests.length?Array.from(new Set(requests.map(r=>r.provider+" · "+r.model))).map(key=>{const rows=requests.filter(r=>r.provider+" · "+r.model===key);return <p key={key}>{channelNames[rows[0].provider]??rows[0].provider} · {rows[0].model} · {rows.map(r=>(requestStates[r.state]??r.state)+" "+r.n).join("，")}</p>;}):<p>尚未发起请求</p>}{data.imageProgress.latestError && <p>最近一次出图错误 · {data.imageProgress.latestError.title ?? "图片请求"} · {data.imageProgress.latestError.error}</p>}</div>}
    {data.execution && !imaging && <ModelReply provider={data.execution.provider} model={data.execution.reported_model} requestedModel={data.execution.requested_model}><span className="tiny muted">{run?.state==="running"?"当前任务":"最近任务"} · {run?.cursor?.phase==="judgment"?run.cursor.judgmentKind:data.execution.purpose==="revise"?"新返修任务":data.execution.purpose==="create"?"起草任务":"检查任务"}{data.execution.status==="succeeded"?" · 回包已保存":data.execution.status==="failed"?" · 执行失败":""}</span></ModelReply>}
    {data.expert&&<p className="small continuous-expert">专家：{PROVIDER_LABEL[data.expert.provider]??data.expert.provider} · {data.expert.model??"默认型号"}</p>}
    {run?.cursor.expertUnavailable&&<p className="small">专家不可用，按现有流程处理 · <a href="#/sys/executors">连接设置</a></p>}
    {!!issues.length&&<details><summary>未解决 {issues.filter((i:any)=>i.state==='provisional').length} 条 · 缺项 {issues.filter((i:any)=>i.state==='missing').length} 项</summary>
      {issues.map((i:any)=><div key={i.id}><b>{i.title} · {i.state==='accepted'?'导演已接受':i.state==='missing'?'缺项':'AI 暂用，未解决'}</b><p>{i.text}</p>
        {i.state==='provisional'&&<button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd('accept_material_issue',{issue_id:i.id});await reload();})}>保留这个问题</button>}
        <a className="btn" href={`#/e/${data.episodeId}/assets`}>查看素材</a></div>)}
    </details>}
    {run?.mode==="fix" && run.cursor.fixStatus && <p>{run.cursor.fixStatus}</p>}
    {run?.cursor.kickoffAdjustment && <div className="kickoff-adjustment"><p>调整前：{run.cursor.kickoffAdjustment.before}</p><p>调整后：{run.cursor.kickoffAdjustment.after}</p></div>}
    {run?.state==="done" && run.target_stage==="delivery"?<p>{run.reason}</p>:!run || ["done","cancelled","failed"].includes(run.state)?<>
      {run && <p>{run.reason}</p>}
      <b className="continuous-start-title">交给 AI 往下做</b>
      <div className="continuous-start">
        <button className="btn" disabled={act.busy} onClick={()=>execute("start_continuous")}>继续做到 06 故事审查</button>
        <button className="btn primary" disabled={act.busy} onClick={()=>execute("start_continuous","delivery")}>继续做到图文交付</button>
      </div>
      <p className="muted small">启动后，专家自动选稿并继续制作。</p>
      {data.methodNotice && <div><p>{data.methodNotice}</p><details><summary>查看本集方法</summary>{FILM_STAGES.slice(0,6).map(([stage])=><ProjectMethodSync key={stage} pid={data.projectId} eid={data.episodeId} stage={stage}/>)}</details></div>}
    </>:run.state==="running"?<>
      <p>AI 正在：{stage} · {action} · 本步已用 {clock}</p>
      <div className="row continuous-actions"><button className="btn" disabled={act.busy || !!run.pause_requested} onClick={()=>execute("pause_continuous")}>{run.pause_requested?"本次调用结束后暂停":"暂停"}</button>{endButton}</div>
    </>:<>
      <p>{run.reason}</p><ContinuousRecoveryNote run={run} all/><div className="row continuous-actions">{run.state==="paused"&&<button className="btn primary" disabled={act.busy} onClick={()=>execute("resume_continuous")}>恢复我的任务</button>}{endButton}</div>
    </>}
    <ErrorLine error={act.error}/>
  </section>;
}

export function ContinuousChoice({run,eid,pid,onCompare,reload}:{run:any;eid:string;pid:string;onCompare:(route:string)=>void;reload:()=>void|Promise<void>}) {
  return null;
}
