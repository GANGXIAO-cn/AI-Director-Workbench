import { changeStageEdit } from "./Cooperation";
import { budgetAmount } from "./budget";
import { useDisplay } from "./DisplayPreferences";
import { useProjectRoute } from "./projectNavigation";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";
import { addDirectorFiles, clipboardFiles, DirectorFiles, useDirectorCommand } from "./DirectorFiles";
import { cmd, q } from "./api";
import { Avatars, ErrorLine, Modal, PROVIDER_LABEL, STAGES, Tag, rno, useAction, useQuery } from "./ui";

export type Selection = { label: string; data: Record<string, any> } | null;

const STAGE_ART: Record<string, string> = { story:"story", script:"script", plan:"plan", prompts:"prompts", publish:"publish" };

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
  const art = artifactFor(view, stage);
  const tasks = art?.tasks?.filter((t:any)=>t.goal!=="两遍独审") || [];
  const focused = art?.versions?.find((v:any)=>v.id===focus);
  const [sentTask,setSentTask]=useState<{id:string;focus?:string|null}|null>(null);
  const task = (sentTask && sentTask.focus===focus && tasks.find((t:any)=>t.id===sentTask.id)) || tasks.find((t:any)=>t.runs.some((r:any)=>r.id===focused?.source_run_id)) || tasks.at(-1);
  const [text, setText] = useState(() => sessionStorage.getItem(`draft:${view.episode.id}:${stage}`) ?? "");
  const submit = useDirectorCommand();
  const [files,setFiles] = useState<File[]>([]);
  const input = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(composeRef,()=>message=>{
    const draft = [text.trimEnd(),message].filter(Boolean).join("\n");
    if (draft.length>500) throw new Error("配置已保存，当前对话草稿剩余字数不足。请先整理草稿。");
    setText(draft);
    requestAnimationFrame(()=>input.current?.focus());
  },[text]);
  const open = task && !["closed", "abandoned"].includes(task.state);
  const plan = (open ? task.executor_plan : null) ?? view.kickoff?.executor_plan;
  const catalog = useQuery<any>("model_catalog", {}, [active]);
  const [selected, setSelected] = useState(plan?.create ?? { provider: "codex" });
  useEffect(() => setSelected(plan?.create ?? {provider:"codex"}), [task?.id, open, plan?.create?.provider, plan?.create?.model, plan?.create?.effort]);
  const provider = selected.provider;
  const models = catalog.data?.text?.[provider]?.models ?? [];
  const connection = catalog.data?.text?.[provider]?.connection;
  const identity = useQuery<{authenticated:boolean|null;accountLabel?:string;simulated?:boolean;unavailable?:boolean}>(art ? "executor_identity" : null,{provider});
  const status = identity.data;
  const accountText = identity.error || (!status ? "正在读取登录状态…" : status.simulated ? "模拟执行器" : status.unavailable ? "CLI 未找到"
    : status.authenticated===false ? "CLI 未登录" : status.authenticated===true ? (status.accountLabel ?? "本机账号")+" · 已登录"
    : "使用 CLI 当前账号，登录状态待确认");
  const chosenModel = selected.model ?? connection?.model ?? null;
  const chosenEffort = selected.effort ?? (chosenModel===connection?.model ? connection?.effort : null);
  const efforts = models.find((m:any) => m.id === chosenModel)?.efforts ?? [null];
  const [history, setHistory] = useState(false);
  const [impact, setImpact] = useState<any>(null);
  const act = useAction();
  useEffect(() => { try { sessionStorage.setItem(`draft:${view.episode.id}:${stage}`, text); } catch { /* ignore */ } }, [text]);
  const draftV = focused ? (focused.id!==art.adopted_version_id ? focused : null) : art?.versions?.filter((v: any) => v.id !== art.adopted_version_id).slice(-1)[0];
  const focusV = focused ?? draftV;
  useEffect(() => {
    if (focusV && focusV.id !== art?.adopted_version_id) q("adoption_preview", { version_id: focusV.id }).then(setImpact).catch(() => setImpact(null));
    else setImpact(null);
  }, [focusV?.id, art?.adopted_version_id]);
  const send = () => act.run(async () => {
    if (!text.trim()) { if (files.length) throw new Error("请写明希望助手怎样使用附件"); return; }
    const instruction=text.trim().replace(/[。！]$/,"");
    const step=instruction.match(/^修改第(\d{1,2})步$/);
    if (!files.length && (step || ["修改这里","修改本阶段","结束修改"].includes(instruction))) {
      const targetStage=step ? STAGES[Number(step[1])-1]?.[0] : stage;
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
    const basis = focused?.id ?? art.adopted_version_id ?? focusV?.id;
    const options = {workspace_revision:art.revision,base_version_id:basis ?? undefined,selection:sel?.data ?? null};
    if (!connection) throw new Error("请先刷新执行器连接");
    const nextExecutor = {...selected,model:selected.model ?? (selected.effort ? chosenModel : null),connectionRevision:connection.revision};
    if (open) {
      await submit("post_message", {task_id:task.id, text, create_executor:nextExecutor, ...options},files);
    } else {
      const result=await submit("create_task", {episode_id:view.episode.id, stage:art.stage, goal:text, executor:{create:nextExecutor}, ...options},files);
      setSentTask({id:result.taskId,focus});
    }
    setText(""); setFiles([]); setSel(null); await reload();
  });
  const runs = task?.runs ?? [];
  const live = runs.filter((r: any) => ["queued", "running"].includes(r.status));
  return (
    <aside className="assistant">
      <div className="head">导演助手 <button className="btn focus-toggle" aria-pressed={focusChat} onClick={()=>setFocusChat(!focusChat)}>{focusChat ? "返回成果文档" : "专注对话"}</button><Avatars create={plan?.create?.provider} review={plan?.review?.provider} /><span className="muted tiny" style={{ marginLeft: "auto", textAlign: "right" }}>{task ? `任务：${task.goal.slice(0, 14)}` : "尚无任务"}{task ? <><br />自动修订 {task.used_rounds}/{task.max_rounds} 轮</> : null}</span></div>
      <button className="btn ghost" onClick={()=>setHistory(true)}>本集对话历史</button>
      {STAGE_ART[stage] && <button className="btn ghost" onClick={openTrial}>讨论与试写</button>}
      {history && <EpisodeHistory view={view} close={()=>setHistory(false)} reload={reload}/>}
      <div className="msgs">
        {(task?.messages ?? []).map((m: any) => <Message key={m.id} m={m} task={task} reload={reload} art={art} />)}
        {live.map((r: any) => (
          <div key={r.id} className="mcard">
            <div className="msg"><div className="who"><i className="prog" />程序 · 进度</div></div>
            <div className="small">{PROVIDER_LABEL[r.provider] ?? r.provider} 正在{r.purpose === "review" ? "独立审查（另开会话）" : r.purpose === "cold_read" ? "冷读" : r.purpose === "comparison" ? "对照审查" : "起草"}… <Tag tone="blue">{r.status}</Tag></div>
            <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => act.run(async () => { await cmd("stop_run", { run_id: r.id }); reload(); })}>停止</button><span className="muted tiny">停止后保留已保存的部分；未返回的请求按任务状态恢复</span></div>
          </div>))}
        {runs.filter((r: any) => r.status === "waiting_quota").map((r: any) => (
          <div key={r.id} className="mcard focus">
            <div className="small"><Tag tone="amber">等待额度</Tag> {PROVIDER_LABEL[r.provider]} 额度用尽{r.quota_resets_at ? `，预计 ${r.quota_resets_at} 恢复` : ""}。不会自动切换。</div>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id }); reload(); })}>等待后重试</button>
              {catalog.data?.quotaRetryPolicy === "manual_cli" && Object.keys(catalog.data.text).filter(p => p !== r.provider).map((p) => (
                <button key={p} className="btn" onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id, provider: p }); reload(); })}>换 {PROVIDER_LABEL[p]}</button>))}
            </div>
          </div>))}
        {runs.filter((r: any) => r.status === "failed").slice(-1).map((r: any) => (
          <div key={r.id} className="mcard"><div className="small error">{PROVIDER_LABEL[r.provider]} 运行失败：{r.reason}{r.diagnostic ? ` · ${r.diagnostic.slice(0, 120)}` : ""}</div>
            <button className="btn" style={{ marginTop: 8 }} onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id }); reload(); })}>重试</button></div>))}
        {art?.stage === "story" && draftV && !draftV.gate.review && !live.length && (
          <div className="mcard">
            <div className="small muted">故事稿 {rno(draftV.display_no)} 已完成</div>
            <div className="row wrap">
              <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{
                await cmd("submit_review",{version_id:draftV.id,task_id:task?.id});
                reload();
              })}>{view.cooperationMode==="beginner" ? "下一步 · 送故事独审" : "送故事独审"}</button>
              {view.cooperationMode!=="beginner" && [["edit","我先自己改几处"],["compare","按本集分工并排出稿"]].map(([action,label])=>
                <button key={action} className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
                  if (art.editState==="protected") await changeStageEdit(art,true);
                  setFocusChat(false);
                  go(`#/e/${view.episode.id}/story?v=${draftV.id}&${action}=${action==="compare" ? draftV.id : "1"}`);
                  reload();
                })}>{label}</button>)}
            </div>
          </div>)}
        {impact && focusV && impact.items.length > 0 && (
          <div className="mcard focus">
            <div className="small" style={{ marginBottom: 6 }}>采用 {rno(focusV.display_no)} 会影响</div>
            {impact.items.map((it: any, i: number) => <div key={i} className="impact"><span>{it.target} · <span className="muted">{it.reason}</span></span><Tag tone={it.kind === "auto" ? "green" : "amber"}>{it.kind === "auto" ? "自动更新" : "需复核"}</Tag></div>)}
            {impact.unaffected?.length > 0 && <div className="muted tiny" style={{ marginTop: 6 }}>不受影响：{impact.unaffected.join("、")}。已采用的版本保留在历史里。</div>}
          </div>)}
        {(stage === "assets" ? batches : batches.filter((b) => ["awaiting_authorization", "pending_reconcile"].includes(b.state))).map((b) => (
          <div key={b.id} className="mcard">
            <div className="spread small"><span>图像批次 · {b.provider} · {b.operation === "edit" ? "局部重画" : "生成"}</span><Tag tone={b.state === "completed" ? "green" : b.state === "pending_reconcile" ? "amber" : "blue"}>{BATCH_STATE[b.state] ?? b.state}</Tag></div>
            <div className="muted tiny" style={{ margin: "6px 0" }}>{b.count} 张 · {b.size} · 预算 {budgetAmount(b.budget_cents,b.currency)} · 费用 {b.cost_state === "unknown" ? "未知" : `估算 ${budgetAmount(b.estimate_cents,b.currency)}`}</div>
            <button className="btn" onClick={() => onOpenBatch(b)}>{b.state === "awaiting_authorization" ? "确认、调整或取消" : "打开批次"}</button>
          </div>))}
      </div>
      {STAGE_ART[stage] && !art ? <div className="composer"><button className="btn primary" onClick={openTrial}>用现有资料先试写</button></div> : STAGE_ART[stage] ? <div className="composer">
        {sel && <div className="selchip">选区：{sel.label} <span style={{ cursor: "pointer" }} onClick={() => setSel(null)}>×</span></div>}
        <DirectorFiles files={files} remove={act.busy ? undefined : i=>setFiles(files.filter((_,n)=>n!==i))}/>
        <textarea ref={input} disabled={act.busy} onPaste={e=>{
          const pasted = clipboardFiles(e.clipboardData);
          if (pasted.length) {e.preventDefault();act.run(async()=>setFiles(addDirectorFiles(files,pasted)));}
        }} maxLength={500} placeholder={art?.editState==="protected"?"讨论本版，或说明要修改的内容":"输入你的想法，继续完善这一集"} value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (!act.busy && !e.nativeEvent.isComposing && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault();send(); } }} />
        <div className="row small" style={{flexWrap:"wrap"}}>
          <label className="btn ghost">添加附件<input type="file" aria-label="添加阶段附件" multiple hidden accept=".md,.txt,.docx,.pdf,image/jpeg,image/png" disabled={act.busy}
            onChange={e=>{const picked=Array.from(e.target.files ?? []);e.target.value="";act.run(async()=>setFiles(addDirectorFiles(files,picked)));}}/></label>
          <button className="btn ghost" onClick={openRequirements}>阶段技能</button>
          <span className="muted tiny">⌘ / Ctrl + 回车发送</span>
          <select aria-label="创作执行器" disabled={act.busy} value={provider} onChange={e => setSelected({provider:e.target.value})}>
            {Object.keys(catalog.data?.text ?? {[provider]:{}}).map(p => <option key={p} value={p}>{PROVIDER_LABEL[p] ?? p}</option>)}</select>
          <select aria-label="创作型号" disabled={act.busy} value={chosenModel ?? ""} onChange={e => setSelected({provider, model:e.target.value || null})}>
            {models.map((m:any) => <option key={m.id ?? "default"} value={m.id ?? ""}>{m.label}</option>)}</select>
          {efforts.length > 1 && <select aria-label="推理强度" disabled={act.busy} value={chosenEffort ?? ""} onChange={e => setSelected({...selected, effort:e.target.value || null})}>
            {efforts.map((v:any) => <option key={v ?? "default"} value={v ?? ""}>{v ?? "默认强度"}</option>)}</select>}
          <span className="tiny" aria-label="当前账号连接">{accountText}</span>
          <button className="btn ghost" disabled={act.busy} onClick={()=>act.run(async()=>{
            try {await cmd("refresh_cli_models",{provider});}
            finally {await Promise.all([catalog.reload(),identity.reload()]);}
          })}>刷新连接和型号</button>
          <a className="tiny" href="#/sys/executors">连接设置</a>
          {open && <button className="btn ghost" onClick={() => act.run(async () => { await cmd("abandon_task", { task_id: task.id }); reload(); })}>新任务</button>}
          <span className="muted tiny" style={{ marginLeft: "auto" }}>{text.length}/500字</span>
          <button className="btn primary" style={{ borderRadius: "50%", width: 28, height: 28, padding: 0 }} onClick={send} disabled={act.busy}>↑</button>
        </div>
        <ErrorLine error={act.error} />
      </div> : <div className="composer">本页操作针对当前阶段。请在对应成果页向导演助手提出改稿要求。</div>}
    </aside>
  );
}

const BATCH_STATE: Record<string, string> = { awaiting_authorization: "待确认", authorized: "已授权", completed: "完成", partial: "部分待发",
  pending_reconcile: "待核对", partially_succeeded: "部分成功", failed: "失败", cancelled: "已取消" };

function EpisodeHistory({view,close,reload}:{view:any;close:()=>void;reload:()=>void}) {
  const [filter,setFilter] = useState("");
  const records = view.artifacts.flatMap((art:any) => art.tasks.flatMap((task:any) => {
    const stage = task.goal === "两遍独审" ? "review" : art.stage;
    return task.messages.map((message:any) => ({art,task,message,stage}));
  })).sort((a:any,b:any) => a.message.created_at-b.message.created_at || a.task.created_at-b.task.created_at ||
    a.task.id.localeCompare(b.task.id) || a.message.seq-b.message.seq);
  const names = Object.fromEntries(STAGES);
  const visible = records.filter((r:any) => !filter || filter === r.stage);
  return <Modal width={760} onClose={close}><div className="spread"><h2>本集对话历史 · {view.episode.display_code}</h2>
    <button className="btn" onClick={close}>关闭历史</button></div>
    <label>筛选阶段 <select value={filter} onChange={e=>setFilter(e.target.value)}><option value="">全部阶段</option>
      {STAGES.filter(([k])=>records.some((r:any)=>r.stage===k)).map(([k,label])=><option key={k} value={k}>{label}</option>)}</select></label>
    {visible.length === 0 && <p>本集还没有阶段对话记录。</p>}
    {visible.map(({art,task,message,stage}:any,i:number)=><div key={message.id} className="history-entry">
      {visible[i-1]?.task.id !== task.id && <h3>{names[stage]} · {task.goal}</h3>}
      <time className="muted tiny">{new Date(message.created_at*1000).toLocaleString()}</time>
      <Message m={message} task={task} art={art} reload={reload} closeHistory={close}/>
    </div>)}
  </Modal>;
}

function Message({ m, task, reload, art, closeHistory }: { m:any; task:any; reload:()=>void; art:any; closeHistory?:()=>void }) {
  const {focusChat, setFocusChat} = useDisplay();
  const act = useAction();
  const payload = m.payload ? JSON.parse(m.payload) : null;
  if (m.role === "user") return <div className="msg user">{m.text}{payload?.attachments?.map((f:any,i:number)=><div className="tiny" key={i}>附件 · {f.name}</div>)}{m.selection && <div className="muted tiny">选区 {Object.values(JSON.parse(m.selection)).join(" · ")}</div>}</div>;
  if (m.kind === "clarify") return (
    <div className="mcard focus">
      <div className="msg"><div className="who"><i className="codex" />澄清</div></div>
      <div>{m.text}</div>
      {!closeHistory && task.state === "clarifying" && <div className="row" style={{ marginTop: 8 }}>{payload.options.map((o: string) => (
        <button key={o} className="btn" onClick={() => act.run(async () => { await cmd("answer_clarification", { task_id: task.id, answer: o }); reload(); })}>{o}</button>))}</div>}
      {payload.relatedIds?.length > 0 && <div className="muted tiny">关联：{payload.relatedIds.join("、")}</div>}
      <ErrorLine error={act.error} />
    </div>);
  if (m.kind === "draft") {
    const v = art?.versions.find((x: any) => x.id === payload.versionId);
    return (
      <div className="mcard">
        <div className="spread small"><span>{art?.stage === "story" ? "故事稿" : art?.stage === "script" ? "剧本" : "成果"} {v ? rno(v.display_no) : ""} 草稿 · {PROVIDER_LABEL[payload.provider] ?? payload.provider}</span>
          <Tag tone={art?.adopted_version_id === payload.versionId ? "green" : "amber"}>{art?.adopted_version_id === payload.versionId ? "已采用" : "待采用"}</Tag></div>
        <div className="small" style={{ margin: "6px 0" }}>{m.text}{payload.baseVersionId ? `（基于 ${rno(art?.versions.find((x: any) => x.id === payload.baseVersionId)?.display_no)}）` : ""}</div>
        <div className="row"><a className="btn" onClick={()=>{closeHistory?.(); if (focusChat) setFocusChat(false);}} href={`#/e/${task.episode_id}/${art.stage}?v=${payload.versionId}`}>{closeHistory ? "打开完整稿" : "定位"}</a></div>
      </div>);
  }
  if (m.role === "system") {
    const isReview = m.kind === "review" || (m.kind === "text" && m.text.startsWith("独立审查（会话隔离）结论"));
    const review = isReview && m.run_id && art?.versions.flatMap((v: any) => v.reviews).find((r: any) => r?.run_id === m.run_id);
    return <div className="mcard small">{review ? <>独立审查 · 另开会话 <Tag tone={review.original_decision === "pass" ? "green" : "red"}>{review.original_decision === "pass" ? "通过" : review.original_decision === "uncertain" ? "待确认" : "退回"}</Tag> · {review.issues.length} 条意见</> : m.text}</div>;
  }
  return <div className="msg"><div className="who"><i className="codex" />助手</div><div>{m.text}</div></div>;
}
