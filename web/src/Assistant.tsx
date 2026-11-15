import { ContinuousStatus, ContinuousChoice } from "./Continuous";
import { StressReviewChat } from "./StressReviewChat";
import { FirstDraft } from "./FirstDraft";
import { ChatMarkdown } from "./ChatMarkdown";
import { ChatReviewIssue } from "./ReviewEvidence";
import { DecisionAdoption, RecordMessageDecision, episodeScenes } from "./Decisions";
import { ModelReply } from "./ModelReply";
import { changeStageEdit } from "./Cooperation";
import { FILM_STAGES } from "./workflow";
import { useDisplay } from "./DisplayPreferences";
import { useProjectRoute } from "./projectNavigation";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useLayoutEffect, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";
import { addDirectorFiles, clipboardFiles, DirectorFiles, useDirectorCommand } from "./DirectorFiles";
import { cmd, q } from "./api";
import { ErrorLine, PRODUCTION_STEPS, PROVIDER_LABEL, pickable, STAGES, Tag, rno, useAction, usePoll, useQuery } from "./ui";

export type Selection = { label: string; data: Record<string, any> } | null;

const STAGE_ART: Record<string, string> = Object.fromEntries([...FILM_STAGES.map(([key]) => [key,key]), ["publish","publish"]]);

const RUN_FAILURE: Record<string,string> = {
  schema: "没有按要求的格式回复，已自动重试一次仍不合格。可以重试，或换一个执行器重试。",
  authentication: "登录已失效。请在终端重新登录后重试，或换一个执行器重试。",
  timeout: "等待回复超时。可以重试，或换一个执行器重试。",
  network: "网络连接中断。可以重试，或换一个执行器重试。",
  "回包两次不符合契约": "回复两次都不符合格式要求。可以重试，或换一个执行器重试。",
};
function runFailureText(r:any) {
  const name = PROVIDER_LABEL[r.provider] ?? r.provider;
  return `${name} ${RUN_FAILURE[r.reason] ?? "运行失败：" + (r.reason || "原因未知") + "。可以重试，或换一个执行器重试。"}`;
}

export function artifactFor(view: any, stage: string) {
  return view.artifacts.find((a: any) => a.stage === STAGE_ART[stage]);
}

/** 导演助手: clarification, progress, draft, review and image-batch cards, plus the composer. */
export default function Assistant({ view, stage, sel, setSel, reload, focus, batches, onOpenBatch, composeRef, openRequirements, openTrial }: {
  view: any; stage: string; sel: Selection; setSel: (s: Selection) => void; reload: () => void; focus?: string | null;
  batches: any[]; onOpenBatch: (b: any) => void; openRequirements: () => void; openTrial:()=>void; composeRef?:Ref<(draft:string)=>void>;
}) {
  const {focusChat,setFocusChat} = useDisplay();
  const {go, active} = useProjectRoute();
  const continuous=useQuery<any>("continuous_view",{episode_id:view.episode.id});
  usePoll(active,()=>continuous.reload(),continuous.data?.run && !["done","cancelled"].includes(continuous.data.run.state)?1500:10000);
  useEffect(()=>{if(continuous.data?.run)reload();},[continuous.data?.run?.updated_at]);
  // 实测 119：连续制作进行中，AI 从导演正在看的阶段推进到下一阶段时，页面跟着跳到新阶段；导演主动打开别的阶段则不打扰。
  const aiStage=continuous.data?.run?.state==="running"?continuous.data.run.cursor?.stage:undefined;
  const lastAiStage=useRef<string|undefined>(undefined);
  useEffect(()=>{
    const previous=lastAiStage.current;lastAiStage.current=aiStage;
    if(!active || !aiStage || !previous || aiStage===previous || previous!==stage || aiStage===stage) return;
    if(STAGES.some(([key])=>key===aiStage)) go(`#/e/${view.episode.id}/${aiStage}`);
  },[aiStage,active]);
  const standardGoals=useQuery<Record<string,string>>("standard_goals");
  // 实测 122：07 素材的各页签（备图工作单、按镜头补图、全部图片）共用 07 的对话与输入框，切换页签对话不消失。
  const chatStage = ["worksheet","shot_assets","assets"].includes(stage) ? "asset_design" : stage;
  const art = artifactFor(view, chatStage);
  const visual=useQuery<any>(stage==="story"?"episode_palettes":null,{episode_id:view.episode.id,stage:"assets"});
  const tasks = art?.tasks?.filter((t:any)=>t.goal!=="两遍独审" && !t.internal) || [];
  const story = stage==="characters" ? artifactFor(view,"story") : null;
  const adoptedStory = story?.versions.find((v:any)=>v.id===story.adopted_version_id);
  const focused = art?.versions?.find((v:any)=>v.id===focus);
  const [sentTask,setSentTask]=useState<{id:string;focus?:string|null}|null>(null);
  const task = (sentTask && sentTask.focus===focus && tasks.find((t:any)=>t.id===sentTask.id)) || tasks.find((t:any)=>t.runs.some((r:any)=>r.id===focused?.source_run_id)) || tasks.at(-1);
  const [text, setText] = useState(() => sessionStorage.getItem(`draft:${view.episode.id}:${chatStage}`) ?? "");
  const submit = useDirectorCommand();
  const [files,setFiles] = useState<File[]>([]);
  const input = useRef<HTMLTextAreaElement>(null);
  const attachmentInput = useRef<HTMLInputElement>(null);
  const [modelMenu,setModelMenu]=useState<"create"|"review"|"expert"|null>(null);
  useImperativeHandle(composeRef,()=>message=>{
    const draft = [text.trimEnd(),message].filter(Boolean).join("\n");
    if (draft.length>500) throw new Error("配置已保存，当前对话草稿剩余字数不足。请先整理草稿。");
    setText(draft);
    requestAnimationFrame(()=>input.current?.focus());
  },[text]);
  const open = task && !["closed", "abandoned"].includes(task.state);
  const plan = {...((open ? task.executor_plan : null) ?? view.kickoff?.executor_plan), ...art?.executorSettings};
  const expert=useQuery<any>("episode_expert",{episode_id:view.episode.id},[view]);
  const catalog = useQuery<any>("model_catalog", {}, [active]);
  const stream=useRef<HTMLDivElement>(null), follow=useRef(true);

  const [impact, setImpact] = useState<any>(null);
  const act = useAction();
  useEffect(() => { try { sessionStorage.setItem(`draft:${view.episode.id}:${chatStage}`, text); } catch { /* ignore */ } }, [text]);
  const draftV = focused ? (focused.id!==art.adopted_version_id ? focused : null) : art?.versions?.filter((v: any) => v.id !== art.adopted_version_id).slice(-1)[0];
  const focusV = focused ?? draftV;
  const basis = sel?.data.versionId ?? focused?.id ?? art?.adopted_version_id ?? focusV?.id;
  const baseVersion = art?.versions.find((v:any)=>v.id===basis);
  const reviewing = new Set(art?.tasks.flatMap((t:any)=>t.runs
    .filter((r:any)=>["review","cold_read","comparison"].includes(r.purpose) && ["queued","running"].includes(r.status))
    .map((r:any)=>r.target_version_id)));
  const reviewLabels = art?.versions.filter((v:any)=>reviewing.has(v.id)).map((v:any)=>rno(v.display_no)).join("、");
  const showReviewHint = baseVersion && reviewLabels && task?.state!=="clarifying";
  useEffect(() => {
    if (focusV && focusV.id !== art?.adopted_version_id) q("adoption_preview", { version_id: focusV.id }).then(setImpact).catch(() => setImpact(null));
    else setImpact(null);
  }, [focusV?.id, art?.adopted_version_id]);
  const send = () => act.run(async () => {
    if (!text.trim()) { if (files.length) throw new Error("请写明希望助手怎样使用附件"); return; }
    if(["delivery","package"].includes(stage)) {
      await submit("fix_after_delivery",{episode_id:view.episode.id,text},files);setText("");setFiles([]);await continuous.reload();await reload();return;
    }
    const instruction=text.trim().replace(/[。！]$/,"");
    const step=instruction.match(/^修改第(\d{1,2})步$/);
    if (!files.length && (step || ["修改这里","修改本阶段","结束修改"].includes(instruction))) {
      const targetStage=step ? (Number(step[1])===9 && stage==="prompts" ? "prompts" : PRODUCTION_STEPS[Number(step[1])-1]?.[0]) : chatStage;
      if (!targetStage || !STAGE_ART[targetStage]) throw new Error("请选择目录中有稿件的写作阶段");
      const target=artifactFor(view,targetStage);
      const editing=instruction!=="结束修改";
      if (target && ((editing && target.editState==="protected") || (!editing && target.editState==="editing"))) {
        await changeStageEdit(target,editing);
      }
      setText("");setSel(null);reload();
      if (targetStage!==stage) go(`#/e/${view.episode.id}/${targetStage}`);
      return;
    }
    if (!art) throw new Error("本阶段还没有成果，请先试写");
    if(task?.state==="clarifying" && !files.length){await cmd("answer_clarification",{task_id:task.id,answer:text});setText("");await reload();return;}
    const options = {auto_dialogue:true,discussion_only:!!view.skippedStages[chatStage],workspace_revision:art.revision,base_version_id:basis ?? undefined,selection:sel?.data ?? null};
    const connection = catalog.data?.text?.[plan.create.provider]?.connection;
    if (!connection) throw new Error("请先刷新执行器连接");
    const nextExecutor = {...plan.create,connectionRevision:connection.revision};
    if (open) {
      await submit("post_message", {task_id:task.id, text, create_executor:nextExecutor, ...options},files);
    } else {
      const result=await submit("create_task", {episode_id:view.episode.id, stage:art.stage, goal:text, executor:{...plan,create:nextExecutor}, ...options},files);
      setSentTask({id:result.taskId,focus});
    }
    follow.current=true;setText(""); setFiles([]); setSel(null); await reload();
  });
  const runs = task?.runs ?? [];
  const live = runs.filter((r: any) => ["queued", "running"].includes(r.status));
  useEffect(()=>{if(follow.current && stream.current){
    const report=stage==="stress" && !live.length && (task?.lastMessage?.kind ?? task?.messages?.at(-1)?.kind)==="draft" && Array.from(stream.current.querySelectorAll<HTMLElement>(".stress-chat")).at(-1);
    if(report) stream.current.scrollTop+=report.getBoundingClientRect().top-stream.current.getBoundingClientRect().top;
    else stream.current.scrollTop=stream.current.scrollHeight;
  }},[task?.id,(task?.messageCount ?? task?.messages?.length),live.length]);
  // 实测 117：停下时滚到对话末尾的“已停下”卡片。
  const startRevision=(version:any,issues:any[])=>{
    const items=issues.map(i=>({id:i.id,location:i.loc,reason:i.reason,blocking:!!i.blocking,setting:i.setting_content}));
    const anchor=items.length===1 ? items[0].location.anchor : "本轮 "+items.length+" 条审查意见";
    setSel({label:rno(version.display_no)+" · "+anchor,data:{versionId:version.id,anchor,reviewIssues:items}});
    setText("请基于 "+rno(version.display_no)+" 修订所选的 "+items.length+" 条审查意见，逐条核对定位和依据，提交一份完整新稿。");
    requestAnimationFrame(()=>input.current?.focus());
  };
  const deliveryFinished = continuous.data?.run?.state === "done" && continuous.data.run.target_stage === "delivery";
  const continuousStatus = <ContinuousStatus data={continuous.data?{...continuous.data,episodeId:view.episode.id,projectId:view.episode.project_id}:null} error={continuous.error} reload={async()=>{await continuous.reload();await reload();}}/>;
  return (
    <aside className="assistant">
      <div className="head"><strong>导演助手</strong><button className="btn focus-toggle" aria-pressed={focusChat} onClick={()=>setFocusChat(!focusChat)}>{focusChat ? "返回成果文档" : "专注对话"}</button>
      <details className="director-more"><summary className="btn">更多工具</summary><div className="director-menu">
        {deliveryFinished && <details><summary>连续制作记录</summary>{continuousStatus}</details>}
        {stage==="story"&&<details><summary>本集开工资料</summary>
          <p>{view.sourceImport?.parsed_text?.split("\n")[0]||view.kickoff?.idea_text||(view.kickoff?.source_type==="topic"?"来源：项目选题卡":"")}</p>
          {view.kickoff?.goal&&<p>要求：{view.kickoff.goal}</p>}
          <p>画面风格：{[...(visual.data?.cards??[]).map((c:any)=>c.name),view.kickoff?.spec?.style].filter(Boolean).join(" · ")||"项目默认"}</p>
          {view.sourceImport&&<details><summary>查看原文</summary><pre className="chat-prose">{view.sourceImport.parsed_text}</pre></details>}</details>}

        {STAGE_ART[chatStage] && <button className="btn" onClick={openTrial}>讨论与试写</button>}
        {art?.stage==="story" && art.versions.length>0 && <button className="btn" onClick={()=>{setFocusChat(false);go("#/e/"+view.episode.id+"/story?v="+(focused?.id??art.adopted_version_id??art.versions.at(-1).id));}}>稿件与历史版本</button>}
        {art && <><button className="btn" onClick={openRequirements}>阶段技能</button><b>执行与连接</b>
          <a className="tiny" href="#/sys/executors">连接设置</a>
          {open && <button className="btn ghost" onClick={() => act.run(async () => { await cmd("abandon_task", { task_id: task.id }); reload(); })}>新任务</button>}
</>}<span className="tiny muted">{task ? "任务："+task.goal.slice(0,32)+" · 自动修订 "+task.used_rounds+"/"+task.max_rounds+" 轮" : "尚无任务"}</span>
        <button className="btn" onClick={e=>e.currentTarget.closest("details")?.removeAttribute("open")}>收起</button>
      </div></details></div>
      {!deliveryFinished && continuousStatus}
      <div className="msgs" ref={stream} role="log" aria-label="本阶段对话" onScroll={e=>{const n=e.currentTarget;follow.current=n.scrollHeight-n.scrollTop-n.clientHeight<60;}}>
        <ContinuousChoice run={continuous.data?.run} eid={view.episode.id} pid={view.episode.project_id} onCompare={route=>{setFocusChat(false);go(route);}} reload={async()=>{await continuous.reload();await reload();}}/>
        {adoptedStory && art && !art.versions.length && !tasks.length &&
          <div className="mcard" data-chat-kind="action">
            <b>故事稿 {rno(adoptedStory.display_no)} 已采用</b>
            <p>起草后可查看本集出场角色及其小传。</p>
            <button className="btn primary" disabled={!standardGoals.data || act.busy || art.editState==="protected"} onClick={()=>act.run(async()=>{
              await submit("create_task",{episode_id:view.episode.id,stage:"characters",goal:standardGoals.data?.characters},[]);
              await reload();
            })}>根据故事稿起草角色小传</button>
          </div>}
        {!tasks.length && art?.stage==="story" && view.kickoff && <div className="mcard" data-chat-kind="action"><b>本集来源已保存</b><button className="btn" disabled={!standardGoals.data || act.busy} onClick={()=>act.run(async()=>{await submit("create_task",{episode_id:view.episode.id,stage:"story",goal:standardGoals.data?.story},[]);await reload();})}>按开工资料起草</button></div>}
        {stage==="treatment" && art?.adopted_version_id && <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
          await cmd("start_repair_plan",{episode_id:view.episode.id,mode:"early"});await reload();go(`#/e/${view.episode.id}/stress`);
        })}>先做一次结构体检（可选）</button>}
        <EpisodeMessages key={view.episode.id+":"+chatStage} view={view} stage={chatStage} reload={reload} revise={startRevision}/>
        <FirstDraft stage={chatStage} view={view} art={art} reload={reload}/>
        {art?.stage==="prompts" && art.tasks.some((t:any)=>t.goal==="两遍独审" && (t.messageCount ?? t.messages.length)>0) &&
          <a className="btn" href={`#/e/${view.episode.id}/review`}>查看分段独审记录</a>}
        {live.map((r: any) => (
          <div key={r.id} className="mcard" data-chat-kind="progress">
            <ModelReply provider={r.provider} model={r.reported_model} requestedModel={r.requested_model} effort={r.reported_effort || r.requested_effort}><span className="tiny muted">任务进度</span></ModelReply>
            {/* 实测 118：进度卡固定为三行（说明+状态 / 计时 / 停止与提示），计时位数变化不再引起换行跳动。 */}
            <div className="small run-progress-line"><span className="run-progress-text">{PROVIDER_LABEL[r.provider] ?? r.provider} {r.status==="queued"?"等待执行：":"正在"}{r.purpose === "review" ? "独立审查（另开会话）" : r.purpose === "cold_read" ? "冷读" : r.purpose === "comparison" ? "对照审查" : "处理你的消息"}…</span> <Tag tone="blue">{r.status==="queued"?"已排队":"执行中"}</Tag></div>
            <div className="small run-progress-timer"><RunTimer run={r}/></div>
            <div className="run-progress-stop"><button className="btn" onClick={() => act.run(async () => { await cmd("stop_run", { run_id: r.id }); reload(); })}>停止</button><span className="muted tiny">停止后保留已保存的部分；未返回的请求按任务状态恢复</span></div>
          </div>))}
        {runs.filter((r: any) => r.canRetry && r.status === "waiting_quota").map((r: any) => (
          <div key={r.id} className="mcard focus" data-chat-kind="action">
            <div className="small"><Tag tone="amber">等待额度</Tag> {PROVIDER_LABEL[r.provider]} 额度用尽{r.quota_resets_at ? `，预计 ${r.quota_resets_at} 恢复` : ""}。不会自动切换。</div>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn" disabled={act.busy} onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id }); reload(); })}>等待后重试</button>
              {catalog.data?.quotaRetryPolicy === "manual_cli" && pickable(Object.keys(catalog.data.text)).filter(p => p !== r.provider).map((p) => (
                <button key={p} className="btn" disabled={act.busy} onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id, provider: p }); reload(); })}>换 {PROVIDER_LABEL[p]}</button>))}
            </div>
          </div>))}
        {runs.at(-1)?.canRetry && runs.at(-1)?.status==="stopped" && <div className="mcard" data-chat-kind="action">
          <div className="small">本轮已停止</div>
          <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("retry_run",{run_id:runs.at(-1).id});await reload();})}>用原输入重试</button>
        </div>}
        {runs.filter((r: any) => r.canRetry && r.status === "failed").slice(-1).map((r: any) => (
          <div key={r.id} className="mcard" data-chat-kind="action"><div className="small error">{runFailureText(r)}</div>
            {/* 实测 143：失败说人话，原始信息收进技术详情；可换执行器重试 */}
            <div className="row" style={{ marginTop: 8, flexWrap: "wrap", gap: 6 }}>
              <button className="btn" onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id }); reload(); })}>重试</button>
              {catalog.data?.text && pickable(Object.keys(catalog.data.text)).filter(p => p !== r.provider).map(p =>
                <button key={p} className="btn" disabled={act.busy} onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id, provider: p }); reload(); })}>换 {PROVIDER_LABEL[p] ?? p} 重试</button>)}
            </div>
            {r.diagnostic && <details><summary className="small muted">查看技术详情</summary><pre className="chat-prose">{r.diagnostic}</pre></details>}</div>))}
        {art?.stage === "story" && draftV && !draftV.gate.review && !live.length && (
          <div className="mcard story-next" data-chat-kind="choice" key={draftV.id}>
            <div className="small muted">故事稿 {rno(draftV.display_no)} 已完成</div>
            <div className="story-next-actions">
              <button disabled={act.busy} onClick={()=>act.run(async()=>{
                await cmd("submit_review",{version_id:draftV.id,task_id:task?.id});
                reload();
              })}>补充独审</button>
              {view.cooperationMode!=="beginner" && [["edit","手动改稿"],["compare","并排出稿"]].map(([action,label])=>
                <button key={action} disabled={act.busy} onClick={()=>act.run(async()=>{
                  if (art.editState==="protected") await changeStageEdit(art,true);
                  setFocusChat(false);
                  go(`#/e/${view.episode.id}/story?v=${draftV.id}&${action}=${action==="compare" ? draftV.id : "1"}`);
                  reload();
                })}>{label}</button>)}
            </div>
            <details className="story-next-help"><summary>操作说明</summary>
              <p><b>补充独审</b>　点击即送审，意见会出现在对话中。</p>
              {view.cooperationMode!=="beginner" && <><p><b>手动改稿</b>　打开本版编辑器，保存后另出一版。</p>
                <p><b>并排出稿</b>　先打开比较页，提交后调用两次模型，各写一份候选稿。</p></>}
            </details>
          </div>)}
        {impact && focusV && impact.items.length > 0 && (
          <div className="mcard focus">
            <div className="small" style={{ marginBottom: 6 }}>采用 {rno(focusV.display_no)} 会影响</div>
            {impact.items.map((it: any, i: number) => <div key={i} className="impact"><span>{it.target} · <span className="muted">{it.reason}</span></span><Tag tone={it.kind === "auto" ? "green" : "amber"}>{it.kind === "auto" ? "自动更新" : "需复核"}</Tag></div>)}
            {impact.unaffected?.length > 0 && <div className="muted tiny" style={{ marginTop: 6 }}>不受影响：{impact.unaffected.join("、")}。已采用的版本保留在历史里。</div>}
          </div>)}
        {(stage === "assets" ? batches : batches.filter((b) => ["awaiting_authorization", "pending_reconcile"].includes(b.state))).map((b) => (
          <div key={b.id} className="mcard" data-chat-kind={["awaiting_authorization","pending_reconcile"].includes(b.state)?"action":"status"}>
            <div className="spread small"><span>图像批次 · {b.provider} · {b.operation === "edit" ? "局部重画" : "生成"}</span><Tag tone={b.state === "completed" ? "green" : b.state === "pending_reconcile" ? "amber" : "blue"}>{BATCH_STATE[b.state] ?? b.state}</Tag></div>
            <div className="muted tiny" style={{ margin: "6px 0" }}>{b.count} 张 · {b.size}</div>
            <button className="btn" onClick={() => onOpenBatch(b)}>{b.state === "awaiting_authorization" ? "确认、调整或取消" : "打开批次"}</button>
          </div>))}
      </div>
      {STAGE_ART[chatStage] && !art ? <div className="composer"><button className="btn primary" onClick={openTrial}>用现有资料先试写</button></div> : (STAGE_ART[chatStage] || ["delivery","package"].includes(stage)) ? <div className="composer">
        {showReviewHint && <p className="composer-review-hint" role="status"><strong>{reviewLabels} 独审尚未完成。</strong>
          现在发送会基于 {rno(baseVersion.display_no)} 另出一版。独审意见只对应 {reviewLabels}。想参考这次意见，请等独审完成后再发送。</p>}
        {sel && <div className="selchip">选区：{sel.label} <span style={{ cursor: "pointer" }} onClick={() => setSel(null)}>×</span></div>}
        <DirectorFiles files={files} remove={act.busy ? undefined : i=>setFiles(files.filter((_,n)=>n!==i))}/>
        <textarea ref={input} disabled={act.busy} onPaste={e=>{
          const pasted = clipboardFiles(e.clipboardData);
          if (pasted.length) {e.preventDefault();act.run(async()=>setFiles(addDirectorFiles(files,pasted)));}
        }} maxLength={500} placeholder="讨论当前稿件，或说明要怎样修改" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (!act.busy && !e.nativeEvent.isComposing && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault();send(); } }} />
        <div className="composer-tools">
          <button className="attach-plus" aria-label="添加附件" title="添加附件" disabled={act.busy} onClick={()=>attachmentInput.current?.click()}>＋</button>
          <input ref={attachmentInput} type="file" aria-label="添加阶段附件" multiple hidden accept=".md,.txt,.docx,.pdf,image/jpeg,image/png" disabled={act.busy}
            onChange={e=>{const picked=Array.from(e.target.files ?? []);e.target.value="";act.run(async()=>setFiles(addDirectorFiles(files,picked)));}}/>
          <span className="tiny muted composer-count" title="⌘ / Ctrl + 回车发送">{text.length}/500字</span>
          {art && catalog.data && (["create","review","expert"] as const).map(role=>{
            const slot=role==="expert"?(expert.data ?? plan.expert ?? plan.create):plan[role];
            const provider=PROVIDER_LABEL[slot.provider] || slot.provider;
            const name=slot.model ? catalog.data.text[slot.provider]?.models?.find((m:any)=>m.id===slot.model)?.label || slot.model : provider+" 默认";
            const receipt=role==="expert"?undefined:art.tasks.flatMap((t:any)=>t.runs).filter((r:any)=>
              r.status==="succeeded" && r.provider===slot.provider && (r.requested_model??null)===(slot.model??null) &&
              (["review","cold_read","comparison"].includes(r.purpose) === (role==="review"))
            ).sort((a:any,b:any)=>b.created_at-a.created_at)[0];
            const receiptHint=receipt ? (receipt.reported_model ? `上次运行报告的型号：${receipt.reported_model}` : "上次运行未返回实际型号") : "";
            const label=role==="create"?"创作":role==="expert"?"专家":"独审";
            return <div key={role} className="composer-model"
              onBlur={e=>{if(e.relatedTarget && !e.currentTarget.contains(e.relatedTarget)) setModelMenu(old=>old===role?null:old);}}
              onKeyDown={e=>{if(e.key==="Escape"){setModelMenu(null);e.currentTarget.querySelector("button")?.focus();}}}>
              <button className="model-trigger" aria-expanded={modelMenu===role} title={[label,name,receiptHint].filter(Boolean).join(" · ")} onClick={()=>setModelMenu(modelMenu===role?null:role)}>
                <small>{label}</small><span>{name}</span><i aria-hidden="true">⌄</i>
              </button>
              {modelMenu===role && <div className="executor-panel">
                <StageExecutor role={role} slot={slot} catalog={catalog.data} refreshCatalog={catalog.reload} busy={act.busy}
                  onChange={value=>act.run(async()=>{
                    if(role==="expert"){await cmd("save_episode_expert",{episode_id:view.episode.id,...value});await expert.reload();}
                    else await cmd("save_stage_executor",{artifact_id:art.id,role,provider:value.provider,model:value.model??null,effort:value.effort??null});
                    await reload();
                  })}/>
              </div>}
            </div>;
          })}
          <button className="btn primary" style={{ borderRadius: "50%", width: 28, height: 28, padding: 0 }} aria-label="发送消息" onClick={send} disabled={act.busy||!text.trim()}>↑</button>
        </div>
        <ErrorLine error={act.error} />
      </div> : <div className="composer">本页操作针对当前阶段。请在对应成果页向导演助手提出改稿要求。</div>}
    </aside>
  );
}

function RunTimer({run}:{run:{status:string;created_at:number;started_at?:number|null}}) {
  const [now,setNow]=useState(Date.now);
  useEffect(()=>{
    const id=window.setInterval(()=>setNow(Date.now()),1000);
    return ()=>window.clearInterval(id);
  },[]);
  const queued=run.status==="queued";
  const start=queued ? run.created_at : run.started_at ?? run.created_at;
  if(!Number.isFinite(start)) return null;
  const seconds=Math.max(0,Math.floor(now/1000-start));
  const label=queued ? "已等待" : run.started_at!=null ? "已执行" : "提交至今";
  return <span role="timer" aria-live="off" className="run-elapsed">{label} {Math.floor(seconds/60)}分{String(seconds%60).padStart(2,"0")}秒</span>;
}

const BATCH_STATE: Record<string, string> = { awaiting_authorization: "待确认", authorized: "已授权", completed: "完成", partial: "部分待发",
  pending_reconcile: "待核对", partially_succeeded: "部分成功", failed: "失败", cancelled: "已取消" };

function EpisodeMessages({view,stage:current,reload,revise}:any) {
  const root=useRef<HTMLDivElement>(null);
  const [older,setOlder]=useState<any[]>([]);
  const [cursor,setCursor]=useState<string|null>(null);
  const [more,setMore]=useState<boolean|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const pending=useRef(false);
  const restore=useRef<{node:HTMLElement;height:number;top:number}|null>(null);
  const initial=view.chatPage;
  const lookup=new Map<string,any>(view.artifacts.flatMap((art:any)=>art.tasks.filter((t:any)=>!t.internal).map((task:any)=>[task.id,{art,task,stage:task.goal==="两遍独审"?"review":art.stage}])));
  const latest=initial?.messages ?? view.artifacts.flatMap((art:any)=>art.tasks.filter((t:any)=>!t.internal).flatMap((task:any)=>task.messages.map((m:any)=>({...m,taskId:task.id}))));
  const previousPage=useRef<any>(initial);
  useEffect(()=>{
    const previous=previousPage.current;previousPage.current=initial;
    if(!previous || !initial || previous===initial)return;
    if(!previous.messages.some((m:any)=>initial.messages.some((n:any)=>n.id===m.id))){
      setOlder([]);setCursor(null);setMore(null);return;
    }
    setOlder(old=>Array.from(new Map([...old,...previous.messages].map((m:any)=>[m.id,m])).values()));
    setCursor(old=>old ?? previous.before);
    setMore(old=>old ?? previous.hasMore);
  },[initial]);
  const unique=new Map<string,any>([...older,...latest].map((m:any)=>[m.id,m]));
  const records=Array.from(unique.values()).filter((m:any)=>lookup.has(m.taskId)).map((message:any)=>({...lookup.get(message.taskId),message}))
    .sort((a:any,b:any)=>a.message.created_at-b.message.created_at || a.task.created_at-b.task.created_at || a.task.id.localeCompare(b.task.id) || a.message.seq-b.message.seq || a.message.id.localeCompare(b.message.id));
  const hasMore=more ?? initial?.hasMore ?? false;
  const loadOlder=async()=>{
    if(pending.current || !hasMore)return;
    pending.current=true;setLoading(true);setError(null);
    try {
      const page=await q<any>("episode_messages",{episode_id:view.episode.id,before:cursor ?? initial.before,limit:30});
      const node=root.current?.closest<HTMLElement>(".msgs");
      if(node)restore.current={node,height:node.scrollHeight,top:node.scrollTop};
      setOlder(old=>[...page.messages,...old]);setCursor(page.before);setMore(page.hasMore);
    }catch(e){setError(String(e));}
    finally{pending.current=false;setLoading(false);}
  };
  const loadRef=useRef(loadOlder);loadRef.current=loadOlder;
  useEffect(()=>{const node=root.current?.closest<HTMLElement>(".msgs");if(!node)return;
    const scroll=()=>{if(node.scrollTop<60)void loadRef.current();};
    node.addEventListener('scroll',scroll);return()=>node.removeEventListener('scroll',scroll);
  },[]);
  useLayoutEffect(()=>{const saved=restore.current;if(saved){saved.node.scrollTop=saved.top+saved.node.scrollHeight-saved.height;restore.current=null;}},[older]);
  const names = Object.fromEntries(STAGES);
  const groups: {stage:string;rows:any[]}[] = [];
  for (const record of records) {
    if (groups.at(-1)?.stage !== record.stage) groups.push({stage:record.stage,rows:[]});
    groups[groups.length-1].rows.push(record);
  }
  return <div ref={root} className="chat-history">{hasMore && <button className="btn" disabled={loading} onClick={loadOlder}>{loading?"正在加载…":"加载更早的聊天记录"}</button>}{error && <p className="error">{error}</p>}{groups.map(({stage,rows})=>{
    const messages=rows.map(({art,task,message})=><Message key={message.id} view={view} scenes={episodeScenes(view)} m={message} task={task} art={art} reload={reload} revise={stage===current?revise:undefined}/>);
    return stage===current?<section key={rows[0].message.id}><h3>{names[stage]}</h3>{messages}</section>:
      <details key={rows[0].message.id}><summary>{names[stage]} · {rows.length} 条对话</summary>{messages}</details>;
  })}</div>;
}

function Message({ view, scenes, m, task, reload, art, closeHistory, revise }: { view:any; scenes:string[]; m:any; task:any; reload:()=>void; art:any; closeHistory?:()=>void; revise?:(v:any,issues:any[])=>void }) {
  const {focusChat, setFocusChat} = useDisplay();
  const act = useAction();
  const [reasons,setReasons]=useState<Record<string,string>>({});
  const [keep,setKeep]=useState(true);
  const [instruction,setInstruction]=useState(m.text);
  const {hash}=useProjectRoute();
  const payload = m.payload ? JSON.parse(m.payload) : null;
  const run = m.run_id && task?.runs?.find((r:any)=>r.id===m.run_id);
  const author = {provider:run?.provider || payload?.provider,model:run?.reported_model,requestedModel:run?.requested_model,
    effort:run?.reported_effort || run?.requested_effort};
  if (m.role === "user") return <div className="msg user">{m.text}{payload?.attachments?.map((f:any,i:number)=><div className="tiny" key={i}>附件 · {f.name}</div>)}{m.selection && <div className="muted tiny">选区 {JSON.parse(m.selection).anchor || JSON.parse(m.selection).text || "已选内容"}</div>}{m.kind==="text" && FILM_STAGES.some(([stage])=>stage===art.stage) && <RecordMessageDecision message={m} eid={task.episode_id} reload={reload}/>}</div>;
  const needsAnswer=m.kind==="clarify" && !closeHistory && task.state==="clarifying" && (task.latestClarifyId ?? task.messages.filter((x:any)=>x.kind==="clarify").at(-1)?.id)===m.id;
  if (m.kind === "clarify") return (
    <div className="mcard" data-chat-kind={needsAnswer?"choice":"status"}>
      {needsAnswer && <strong className="chat-choice-title">请选择</strong>}
      <ModelReply {...author}><ChatMarkdown text={m.text}/></ModelReply>
      {needsAnswer && <div className="row" style={{ marginTop: 8 }}>{payload.options.map((o: string) => (
        <button key={o} className="btn" onClick={() => act.run(async () => { await cmd("answer_clarification", { task_id: task.id, answer: o }); reload(); })}>{o}</button>))}</div>}
      {payload.relatedIds?.length > 0 && <div className="muted tiny">关联：{payload.relatedIds.join("、")}</div>}
      <ErrorLine error={act.error} />
    </div>);
  if (m.kind === "draft") {
    const v = art?.versions.find((x: any) => x.id === payload.versionId);
    return (
      <ModelReply {...author}><div className="mcard" data-chat-kind={art?.adopted_version_id===payload.versionId?"status":"action"}>
        <div className="spread small"><span>{art?.stage === "story" ? "故事稿" : art?.stage === "script" ? "剧本" : "成果"} {v ? rno(v.display_no) : ""} 草稿 · {PROVIDER_LABEL[payload.provider] ?? payload.provider}</span>
          <Tag tone={art?.adopted_version_id === payload.versionId ? "green" : "amber"}>{art?.adopted_version_id === payload.versionId ? "已采用" : "待采用"}</Tag></div>
        <div className="small" style={{ margin: "6px 0" }}>{v ? `已生成 ${rno(v.display_no)}，${art.adopted_version_id===v.id?"已采用":"待采用"}` : m.text}{payload.baseVersionId ? `（基于 ${rno(art?.versions.find((x: any) => x.id === payload.baseVersionId)?.display_no)}）` : ""}</div>
        {art?.stage==="stress" && v && revise && <StressReviewChat report={v} view={view} reload={reload}/>}
        {art?.stage==="story" && v && art.adopted_version_id!==v.id && v.gate.adoptable && <DecisionAdoption art={art} version={v} eid={task.episode_id} scenes={scenes} onDone={reload}>采用 {rno(v.display_no)}</DecisionAdoption>}
        <ErrorLine error={act.error}/><div className="row"><a className="btn" onClick={()=>{closeHistory?.(); if (focusChat) setFocusChat(false);}} href={`#/e/${task.episode_id}/${art.stage}?v=${payload.versionId}`}>{closeHistory ? "打开完整稿" : "定位"}</a></div>
      </div></ModelReply>);
  }
  if (m.role === "system") {
    const isReview = m.kind === "review" || (m.kind === "text" && m.text.startsWith("独立审查（会话隔离）结论"));
    const review = isReview && m.run_id && art?.versions.flatMap((v: any) => v.reviews).find((r: any) => r?.run_id === m.run_id);
    if(review && art?.stage==="story"){
      const v=art.versions.find((v:any)=>v.reviews.some((r:any)=>r.id===review.id));
      const pending=review.issues.filter((i:any)=>!i.waiver_reason);
      return <section className="mcard" data-chat-kind={pending.length && !closeHistory?"action":"status"}><b>独立审查 · {rno(v.display_no)} · 第 {review.pass_no} 遍</b><p>{review.original_decision==="pass"?"通过":review.original_decision==="uncertain"?"待确认":"退回"} · {review.issues.length} 条意见</p>
        {revise && !closeHistory && pending.length>1 && <button className="btn review-revise-all" onClick={()=>revise(v,pending)}>修订全部待处理意见（{pending.length}）</button>}
        {review.issues.map((i:any)=><ChatReviewIssue key={i.id} issue={i} version={v}>
          {i.waiver_reason ? <p className="ok">已放行（仅本版）：{i.waiver_reason}</p> : !closeHistory && <>
            <div className="review-actions">
              {revise && <button className="btn" onClick={()=>revise(v,[i])}>修订这条</button>}
              {!!i.blocking && <a className="btn" href={"#/p/"+task.project_id+"/settings"}>去项目设定 ↗</a>}
            </div>
            {!i.blocking && <div className="row wrap"><input aria-label="放行备注（选填）" placeholder="备注（选填）" value={reasons[i.id]??""} onChange={e=>setReasons({...reasons,[i.id]:e.target.value})}/><button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("waive",{issue_id:i.id,version_id:v.id,reason:reasons[i.id]??""});await reload();})}>按本版放行</button></div>}</>}
        </ChatReviewIssue>)}<details><summary>冷读与对照依据</summary>{review.observations.map((o:any,index:number)=><p key={index}>{o.evidence}</p>)}</details><ErrorLine error={act.error}/></section>;
    }
    return <div className="mcard small">{review ? <>独立审查 · 另开会话 <Tag tone={review.original_decision === "pass" ? "green" : "red"}>{review.original_decision === "pass" ? "通过" : review.original_decision === "uncertain" ? "待确认" : "退回"}</Tag> · {review.issues.length} 条意见</> : m.text}</div>;
  }
  if (payload?.internalJudgment) {
    let summary = "已收到判断回复，正在核对与接续";
    try {
      const result = JSON.parse(m.text);
      if (Array.isArray(result.placements)) summary = `建议 ${result.placements.length} 个商品入景镜头`;
      else if (Array.isArray(result.bindings)) summary = `已返回 ${result.bindings.length} 张素材的用途判断`;
      else if (Array.isArray(result.mappings)) summary = `已返回 ${result.mappings.length} 项道具用途核对`;
    } catch { /* The run's business validator will explain malformed replies. */ }
    return <ModelReply {...author}><b>{payload.internalJudgment}</b><p>{summary}</p>
      <details><summary>查看判断依据与编号</summary><ChatMarkdown text={m.text}/></details>
    </ModelReply>;
  }
  return <ModelReply {...author}><ChatMarkdown text={m.text}/>{payload?.stageDiscussion && <>
    <small className="muted">仅讨论，稿件未改动</small>
    {revise && !payload?.internalJudgment && !view.skippedStages[art.stage] && <div className="discussion-convert">
      <details><summary>修改指令</summary><textarea aria-label="落实到新稿的修改指令" value={instruction} onChange={e=>setInstruction(e.target.value)}/></details>
      <label><input type="checkbox" checked={keep} onChange={e=>setKeep(e.target.checked)}/>后面也要守住</label>
      <button className="btn" disabled={act.busy || !instruction.trim()} onClick={()=>act.run(async()=>{
        const viewed=new URLSearchParams(hash.split("?")[1]).get("v");
        await cmd("create_task",{episode_id:task.episode_id,stage:art.stage,goal:instruction.trim(),auto_dialogue:true,
          workspace_revision:art.revision,base_version_id:viewed || art.adopted_version_id || art.versions.at(-1)?.id,
          discussion_id:m.id,keep_instruction:keep,executor:art.executorSettings});
        await reload();
      })}>按以上讨论生成新稿</button><ErrorLine error={act.error}/>
    </div>}
  </>}</ModelReply>;
}

function StageExecutor({role,slot,catalog,refreshCatalog,busy,onChange}: {
  role:"create"|"review"|"expert"; slot:any; catalog:any; refreshCatalog:()=>Promise<void>; busy:boolean; onChange:(value:any)=>void
}) {
  const label=role==="create"?"创作":role==="expert"?"专家":"独审";
  const models=catalog.text[slot.provider]?.models ?? [];
  const needsCatalog=slot.provider==="claude" && !catalog.text.claude?.fetchedAt && !models.some((m:any)=>m.id);
  const [modelError,setModelError]=useState("");
  useEffect(()=>{
    let active=true;
    setModelError("");
    if (needsCatalog) {
      cmd("refresh_cli_models",{provider:"claude"}).then(()=>refreshCatalog()).catch(err=>{
        if(active) setModelError(err instanceof Error ? err.message : "无法读取 Claude 型号");
      });
    }
    return ()=>{active=false;};
  },[needsCatalog,refreshCatalog]);
  const efforts=models.find((m:any)=>m.id===(slot.model ?? null))?.efforts ?? [];
  const identity=useQuery<any>("executor_identity",{provider:slot.provider});
  const status=identity.data;
  return <fieldset className="col" disabled={busy}><legend>{label}</legend>
    <select aria-label={label+"执行器"} value={slot.provider} onChange={e=>onChange({provider:e.target.value,model:null,effort:null})}>
      {pickable(Object.keys(catalog.text),slot.provider).map(p=><option key={p} value={p}>{PROVIDER_LABEL[p]??p}</option>)}</select>
    <select aria-label={label+"型号"} disabled={needsCatalog && !modelError} value={slot.model??""} onChange={e=>onChange({...slot,model:e.target.value||null,effort:null})}>
      {models.map((m:any)=><option key={m.id??"default"} value={m.id??""}>{m.label}</option>)}</select>
    {needsCatalog && <small role="status">{modelError || "正在读取 Claude Code 型号…"}</small>}
    {efforts.some(Boolean) && <select aria-label={label+"推理强度"} value={slot.effort??""} onChange={e=>onChange({...slot,effort:e.target.value||null})}>
      {efforts.map((v:any)=><option key={v??"default"} value={v??""}>{v??"默认强度"}</option>)}</select>}
    <span className="tiny">{identity.error || (!status?"正在读取登录状态…":status.unavailable?"CLI 未找到":status.simulated?"模拟执行器":status.authenticated===false?"CLI 未登录":status.authenticated===true?(status.accountLabel??"本机账号")+" · 已登录":"登录状态待确认")}</span>
    {(modelError || identity.error || status?.unavailable || status?.authenticated===false) && <a className="link" href="#/sys/executors">打开连接设置</a>}
  </fieldset>;
}
