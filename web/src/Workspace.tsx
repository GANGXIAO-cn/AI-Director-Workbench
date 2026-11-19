import {TopicRuntimeTag} from "./TopicRuntime";
import {PreKickoffWorkspace} from "./Directions";
import { RepairProgress } from "./RepairProgress";
import { AssetWorksheet } from "./AssetWorksheet";
import { stressGrade } from "./StressReviewChat";
import { ChatMarkdown } from "./ChatMarkdown";
import { FIRST_DRAFT } from "./FirstDraft";
import { LocateStory, MarkPassage, ReviewEvidence, type StoryLocation } from "./ReviewEvidence";
import { Decisions, DecisionAdoption, DecisionMessageSummary, episodeScenes } from "./Decisions";
import { StageInputs, InputGroups } from "./StageInputs";
import { Supplements, RoleStates } from "./Supplements";
import { StageChecklist } from "./StageChecklist";
import { FILM_FIELDS, FILM_STAGES, NEXT_FILM_STAGE, OPTIONAL_FILM_STAGES } from "./workflow";
import { StageTrial } from "./StageTrial";
import { VersionCompare } from "./VersionCompare";
import { EpisodeCooperation, OptionalStage, RevisionBar } from "./Cooperation";
import { PublishCues } from "./PublishCues";
import { MediaPreview, PromptMedia } from "./PromptMedia";
import { VersionSignals } from "./PromptSignals";
import { EpisodeSceneContact, OriginalZoom } from "./SceneContact";
import { ProjectMethodSync, UploadedMethods, canUseUploadedMethods } from "./UploadedMethods";
import { StoryComparison } from "./StoryComparison";
import { useDisplay } from "./DisplayPreferences";
import { ShotBoard, ReferenceReview } from "./ShotReferences";
import { OrderedImageInputs } from "./OrderedImageInputs";
import { RequiredImage } from "./RequiredImage";
import { useProjectRoute } from "./projectNavigation";
import { ModelPick } from "./Project";
import { ProjectLibrary } from "./ProjectLibrary";
import { EpisodePaletteSettings, PaletteTrace, StepRequirements, VersionSourceText } from "./Resources";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { blob, cmd, deliveryFile, q, upload } from "./api";
import Assistant, { Selection, artifactFor } from "./Assistant";
import Canvas from "./Canvas";
import { ErrorLine, Modal, PROVIDER_LABEL, Pills, PRODUCTION_STEPS, DELIVERY_STEPS, MATERIAL_TABS, SHOT_TABS, Tag, fmtMs, rno, useAction, usePoll, useQuery } from "./ui";
import { PlanActing } from "./PlanActing";
import { ColumnDivider, savedWidths, type Widths } from "./WorkspaceColumns";
import { frameOrientation } from "./specs";

type Ctx = {
  location:StoryLocation|null;
  write:(name:string,args:Record<string,any>)=>Promise<any>; locked:boolean; openTrial:()=>void; view: any; eid: string; reload: () => Promise<void>; sel: Selection; setSel: (s: Selection) => void; assets: any[];
  batches: any[]; focus: string | null; setFocus: (v: string | null) => void; openBatch: (b: any) => void;
};

function layout(plan: any) {
  if (!plan) return { segments: [], shots: {}, total: 0 };
  const segs = [...plan.segments].sort((a: any, b: any) => a.order - b.order);
  let clock = 0;
  const shots: Record<string, any> = {};
  const out = segs.map((s: any) => {
    let local = 0;
    const own = plan.shots.filter((x: any) => x.segment === s.id).sort((a: any, b: any) => a.order - b.order);
    for (const sh of own) { shots[sh.id] = { start: clock + local, end: clock + local + sh.duration_ms }; local += sh.duration_ms; }
    const seg = { ...s, start: clock, end: clock + local, duration: local, shots: own.map((x: any) => x.id) };
    clock += local;
    return seg;
  });
  return { segments: out, shots, total: clock };
}

export default function Workspace({ id, stage }: { id: string; stage: string }) {
  const { hash, active, go } = useProjectRoute();
  const { focusChat, setFocusChat } = useDisplay();
  const canvasMode = stage === "assets" && new URLSearchParams(hash.split("?")[1]).get("tab") === "canvas";
  const worksheet = stage==="worksheet";
  const chatOnly = focusChat && !canvasMode && !["package","worksheet","shot_assets"].includes(stage);
  const [previewId,setPreviewId]=useState("");
  useEffect(()=>setPreviewId(""),[id,stage,chatOnly]);
  const [packagePanel,setPackagePanel]=useState<HTMLElement|null>(null);
  useEffect(() => {
    if (!active || !chatOnly) return;
    const exit = (e:KeyboardEvent) => {
      if (e.key==="Escape" && !e.defaultPrevented && !e.isComposing && !document.querySelector(".desktop-page:not([hidden]) .scrim")) {
        e.preventDefault(); if(previewId) setPreviewId(""); else setFocusChat(false);
      }
    };
    window.addEventListener("keydown",exit);
    return ()=>window.removeEventListener("keydown",exit);
  },[active,chatOnly,setFocusChat,previewId]);
  const routeParams=new URLSearchParams(hash.split("?")[1]);
  const focus = routeParams.get("v");
  const pendingStoryRun = new URLSearchParams(hash.split("?")[1]).get("run");
  const view = useQuery<any>("episode_view", { episode_id: id, chat_limit:30 },[stage,pendingStoryRun]);
  const assets = useQuery<any[]>("episode_assets", { episode_id: id },[stage]);
  const batches = useQuery<any[]>("episode_batches", { episode_id: id },[stage]);
  const [location,setLocation]=useState<StoryLocation|null>(null);
  useEffect(()=>setLocation(null),[id,stage,focus]);
  const [sel, setSel] = useState<Selection>(null);
  const [cols, setCols] = useState<Widths>(savedWidths);
  useEffect(()=>{try {localStorage.setItem("ws-cols", JSON.stringify(cols));} catch { /* storage disabled */ }}, [cols]);
  const [batch, setBatch] = useState<any>(null);
  const [showPalettes, setShowPalettes] = useState(false);
  const [showMethod, setShowMethod] = useState(false);
  const [trialRoute,setTrialRoute] = useState("");
  useEffect(()=>{setTrialRoute("");setCheckStage("");},[id,stage]);
  const decisions=useQuery<any[]>("director_decisions",{episode_id:id},[stage]);
  const [showDecisions,setShowDecisions]=useState(false);
  const [requirements,setRequirements]=useState(false);
  const [checkStage,setCheckStage]=useState("");
  const composeRef=useRef<((draft:string)=>void)|null>(null);
  const batchParam = new URLSearchParams(hash.split("?")[1] ?? "").get("batch");
  useEffect(() => { if (batchParam) setBatch({ id: batchParam }); }, [batchParam]);
  const routeHash = useRef(hash);
  routeHash.current = hash;
  const setFocus = (id:string|null) => {
    if (routeHash.current !== hash) return;
    const [path,query] = hash.split("?");
    const params = new URLSearchParams(query);
    if (id) params.set("v",id); else params.delete("v");
    const next = params.size ? path+"?"+params.toString() : path;
    if (next !== hash) go(next);
  };
  const hasRequirements = [...FILM_STAGES.map(([key])=>key),"publish","assets","space","cast","review"].includes(stage);
  const needsLabel = ["space","cast","assets"].includes(stage) ? "本集出图增强方法" : stage==="review" ? "分镜审核依据" : "本步需求与参考";
  const documentPanel=useRef<HTMLDivElement>(null);
  const versionStage = stage==="review" ? "plan" : stage;
  const missingVersion = !!(view.data && focus && [...FILM_STAGES.map(([key])=>key),"publish"].includes(versionStage)
    && !view.data.artifacts.find((a:any)=>a.stage===versionStage)?.versions.some((version:any)=>version.id===focus));
  useEffect(()=>{ if (missingVersion) void view.reload(); },[missingVersion,focus,versionStage,id]);
  const busy = view.data?.artifacts.some((a: any) => a.tasks.some((t: any) => t.runs.some((r: any) => ["queued", "running"].includes(r.status))));
  const reload = async () => { await Promise.all([view.reload(), assets.reload(), batches.reload(), decisions.reload()]); };
  const imagesRunning=batches.data?.some(b=>["authorized","running"].includes(b.state));
  usePoll(!!busy || !!imagesRunning || !!view.data?.deliveryInProgress, reload);
  if (!view.data) return <div className="page">{view.error ?? "读取中…"}</div>;
  const v = view.data;
  if (!v.kickoff) return <PreKickoffWorkspace eid={id} pid={v.episode.project_id} episode={v.episode} reload={view.reload}/>;
  if (missingVersion) {
    return <div className="page">当前阶段的版本列表中没有此稿。
      <button className="btn" onClick={view.reload}>刷新版本列表</button><button className="btn" onClick={()=>setFocus(null)}>返回当前稿</button></div>;
  }
  const stageArtifact = artifactFor(v,stage);
  const inputVersion = focus ?? stageArtifact?.adopted_version_id ?? stageArtifact?.versions.at(-1)?.id;
  const ctx: Ctx = { location, locked:stageArtifact?.editState==="protected", view: v, eid: id, reload, sel, setSel, assets: assets.data ?? [], batches: batches.data ?? [], focus, setFocus,
    write:(name,args)=>cmd(name,{...args,editor_guard:[stageArtifact.id,stageArtifact.revision]}),
    openBatch: setBatch, openTrial:()=>setTrialRoute(id+":"+stage) };
  const previews = v.artifacts.flatMap((a:any)=>a.versions.map((version:any)=>({
    version, label:(FILM_STAGES.find(([key])=>key===a.stage)?.[1] ?? ({publish:"出版资料"} as Record<string,string>)[a.stage] ?? a.stage)
      +" · "+rno(version.display_no)+(version.id===a.adopted_version_id?" · 已采用":"")
  })));
  const preview=chatOnly && previews.find((item:any)=>item.version.id===previewId);
  const adopted = (s: string) => !!v.artifacts.find((a: any) => a.stage === s)?.adopted_version_id;
  const status = (s: string) => {
    const map: Record<string, boolean> = { story: adopted("story"), script: adopted("script"), plan: adopted("plan"),
      ...v.productionStatus, prompts: adopted("prompts"),
      publish: adopted("publish"), delivery: !!v.productionStatus?.delivery };
    return (map[s] || adopted(s)) ? "done" : s === stage ? "now" : "";
  };
  return (
    <LocateStory.Provider value={stage==="story"?setLocation:null}><div className="app episode-workspace">
      {!worksheet && stage!=="stress" && <RepairProgress view={v} stage={stage} reload={reload}/>}
      {!worksheet && <ProjectMethodSync key={id+stage} pid={v.episode.project_id} eid={id} stage={canUseUploadedMethods(stage)?stage:undefined}/>}
      <nav className="stage-views" aria-label="本阶段工作区">
        {!canvasMode && hasRequirements && <><button className="pill" aria-pressed={chatOnly && !requirements} onClick={()=>setFocusChat(true)}>创作对话</button>
        <button className="pill" aria-label={needsLabel} title={needsLabel} aria-haspopup="dialog" onClick={()=>setRequirements(true)}>本步需求</button>
        <button className="pill" aria-pressed={!chatOnly && !requirements} onClick={()=>{
          setFocusChat(false); requestAnimationFrame(()=>documentPanel.current?.focus({preventScroll:true}));
        }}>成果文档</button></>}
        <button className="pill" onClick={()=>setCheckStage(stage)}>过关清单</button>
        {chatOnly && previews.length>0 && <select className="preview-picker" aria-label="预览成果物" value={previewId} onChange={e=>setPreviewId(e.target.value)}>
          <option value="">预览成果物</option>{previews.map((item:any)=><option key={item.version.id} value={item.version.id}>{item.label}</option>)}</select>}
      </nav>
      <div className={"ws"+(preview?" with-preview":"")+(chatOnly?" chat-only":"")+(canvasMode?" canvas-only":"")} style={{flex:1, gridTemplateColumns: preview ? "minmax(0, 3fr) minmax(0, 2fr)" : canvasMode || chatOnly ? "minmax(0, 1fr)" : `${cols[0]}px minmax(0, 1fr) ${cols[1]}px`}}>
        {!canvasMode && <div className="catalog">
          <div className="spread" style={{ padding: "0 8px 12px" }}><b>目录</b><a className="muted" href={`#/p/${v.episode.project_id}`}>⇤</a></div>
          <EpisodeCooperation eid={id} mode={v.cooperationMode} reload={view.reload}/>
          {PRODUCTION_STEPS.map(([k,label])=>{
            const tabs=k==="asset_design"?MATERIAL_TABS:k==="plan"?SHOT_TABS:[];
            const current=k===stage || tabs.some(([key])=>key===stage);
            const done=k==="asset_design"?adopted(k) && v.productionStatus.materialsConfirmed:k==="plan"?adopted("plan"):status(k)==="done";
            return <a key={k} className={`item ${current?"on":""}`} href={`#/e/${id}/${current?stage:k==="asset_design"?"worksheet":k}`}>
              {label}{v.skippedStages[k]?<small>已跳过</small>:<span className={`st ${done?"done":current?"now":""}`}/>}</a>;
          })}
          <b>交付</b>{DELIVERY_STEPS.map(([k,label])=><a key={k} className={`item ${k===stage?"on":""}`} href={`#/e/${id}/${k}`}>{label}<span className={`st ${status(k)}`}/></a>)}
        </div>}
        {!canvasMode && <><ColumnDivider side={0} width={cols[0]} resize={width=>setCols(old=>[width,old[1]])}/>
          <ColumnDivider side={1} width={cols[1]} resize={width=>setCols(old=>[old[0],width])}/></>}
        <div className="center" ref={documentPanel} tabIndex={-1}>
          <header className="document-toolbar">
          <div className="topbar">
            {!worksheet && !canvasMode && !hasRequirements && stage!=="package" && <button className="btn narrow-chat" onClick={()=>setFocusChat(true)}>专注对话</button>}
            <button className="btn" onClick={()=>setShowDecisions(true)}>导演决定 {decisions.data?.filter(d=>d.state==="active").length??0}</button>
            {["assets","prompts"].includes(stage) && <button className="btn" onClick={() => setShowPalettes(true)}>本集色卡</button>}
            {stage !== "package" && <button className="btn" onClick={() => setShowMethod(true)}>制作方法</button>}
            {canvasMode && <button className="btn" onClick={()=>setRequirements(true)}>{needsLabel}</button>}
            <span className="title">{v.episode.display_code} · {v.episode.title || "未命名"}</span>
            <span className="muted small">{frameOrientation(v.kickoff.spec.aspectRatio)}</span>
            <span style={{ marginLeft: "auto" }} className="chip">◐ {v.kickoff.relation === "continue" ? "承接前集" : "独立成集"}</span>
            <span className="chip">▯ {v.kickoff.spec.aspectRatio}</span>
          </div>
          {[MATERIAL_TABS,SHOT_TABS].filter(tabs=>tabs.some(([key])=>key===stage)).map(tabs=><nav key={tabs[0][0]} aria-label="阶段页签" className="row">{tabs.map(([key,label])=><a key={key} className={"pill "+(stage===key?"on":"")} aria-current={stage===key?"page":undefined} href={`#/e/${id}/${key}`}>{label}</a>)}</nav>)}
          {stageArtifact && <RevisionBar art={stageArtifact} reload={view.reload}/>}
          </header>
          {["plan","prompts","assets"].includes(stage) && !!v.pendingShotImages?.length && <section className="card amber"><strong>还有状态图或九宫格待出</strong><p>{v.pendingShotImages.join("、")}</p><a className="btn" href={`#/e/${id}/asset_design`}>追加素材条目</a> <a className="btn" href={`#/e/${id}/assets`}>去 07 出图</a></section>}
          {routeParams.get("report") && <StressRepair ctx={ctx} stage={stage} reportId={routeParams.get("report")!} optionIndex={routeParams.get("option")}/>}
          {stageArtifact && inputVersion && <StageInputs key={inputVersion} versionId={inputVersion} records={decisions.data??[]} art={stageArtifact} onSave={async vid=>{await reload();setFocus(vid);}}/>}
          {(["space","cast"].includes(stage) || OPTIONAL_FILM_STAGES.has(stage)) && <OptionalStage key={id+stage} eid={id} stage={stage} skippedReason={v.skippedStages[stage] || ""} reload={view.reload}/>}
          {stage==="package"?<PackageView ctx={ctx} panel={packagePanel}/>:!v.skippedStages[stage] && <StageView key={id+stage+String(ctx.locked)} stage={stage} ctx={ctx} />}
        </div>
        {stage==="package" && <aside className="assistant package-aside" aria-label="出包设置" ref={setPackagePanel}/>}
        {/* 实测 122：备图工作单也保留导演助手，切换素材页签时对话不消失。 */}
        {!canvasMode && stage!=="package" && <Assistant key={id + ":" + stage} composeRef={composeRef} openTrial={ctx.openTrial} openRequirements={()=>setRequirements(true)} view={v} stage={stage} sel={sel} setSel={setSel} reload={reload} focus={focus} batches={ctx.batches} onOpenBatch={setBatch} />}
        {preview && <aside className="focus-preview" aria-label="成果预览">
          <header className="spread"><strong>{preview.label}</strong><button className="btn" onClick={()=>setPreviewId("")}>关闭预览</button></header>
          {Object.entries(preview.version.content).map(([field,value])=><section key={field}>
            <h3>{FILM_FIELD_NAMES[field] || field}</h3>{typeof value==="string" ? <ChatMarkdown text={value}/> : <FilmValue value={value}/>}
          </section>)}</aside>}
      </div>
      {trialRoute===id+":"+stage && <StageTrial key={trialRoute} projectId={v.episode.project_id} episodeId={id} stage={stage}
        artifacts={v.artifacts} currentVersion={focus} provider={v.kickoff.executor_plan.create.provider} close={()=>setTrialRoute("")} reload={reload}/>}
      {checkStage===stage && <StageChecklist eid={id} stage={worksheet?"asset_design":stage}
        version={[...FILM_STAGES.map(([key])=>key),"publish","review"].includes(stage)?focus:null}
        delivery={stage==="package" ? routeParams.get("delivery") : null}
        scope={stage==="delivery" ? routeParams.get("scope") || (v.kickoff.spec.packaging==="trial"?"trial":"full") : "full"}
        segments={stage==="delivery" ? routeParams.getAll("segment") : []}
        close={()=>setCheckStage("")}/>}
      {showDecisions && <Decisions eid={id} stage={stage} records={decisions.data??[]} scenes={episodeScenes(v)} refresh={reload} close={()=>setShowDecisions(false)}/>}
      {showPalettes && <EpisodePaletteSettings key={stage} eid={id} stage={stage} close={() => setShowPalettes(false)}/>}
      {showMethod && <StageMethods key={id+stage} pid={v.episode.project_id} eid={id} stage={stage} close={() => setShowMethod(false)} />}
      {requirements && <StepRequirements key={stage} pid={v.episode.project_id} eid={id} stage={["space","cast"].includes(stage)?"assets":versionStage} close={()=>{setRequirements(false); void reload();}} toComposer={stage==="review"?undefined:text=>{composeRef.current?.(text); if (window.matchMedia("(max-width: 1120px)").matches) setFocusChat(true);}}/>}
      {batch && <BatchModal batch={batch} assets={ctx.assets} close={() => { setBatch(null); reload(); }} />}
    </div></LocateStory.Provider>
  );
}

function StageMethods({ pid, eid, stage, close }: { pid:string; eid: string; stage: string; close: () => void }) {
  const data = useQuery<any>("stage_methods", { episode_id: eid, stage });
  const [off,setOff]=useState<string[]|null>(null);
  const act=useAction();
  const disabled=off ?? data.data?.disabled ?? [];
  return <Modal onClose={close} width={760}>
    <div className="spread"><h2>本阶段制作方法</h2><button className="btn" onClick={close}>关闭</button></div>
    {data.error && <p className="error">{data.error}</p>}
    {canUseUploadedMethods(stage) && <UploadedMethods pid={pid} eid={eid} stage={stage}/>}
    {data.data && <><p className="muted small">当前阶段配置 · {data.data.ref}</p>
      {!data.data.sections.length && <p>此版本未收录本阶段的小节。</p>}
      {!!data.data.guides?.length && <details className="card"><summary>查阅 H3 指南原文</summary>
        {data.data.guides.map((guide:{name:string;text:string;methodSource:{sha256:string}})=>
          <details key={guide.name}><summary>{["ref-en.txt","h3ReferenceGuide.txt"].includes(guide.name)?"全参考指南":"文本与关键帧指南"} · {guide.name}</summary>
            <p className="muted tiny">SHA-256 {guide.methodSource.sha256}</p><pre style={{whiteSpace:"pre-wrap"}}>{guide.text}</pre></details>)}</details>}
      {data.data.sections.map((item: any) => <details className="card" key={item.name+(item.pass ?? "")}>
        <summary>{item.pass==="cold_read"?"第一遍 · ":item.pass==="comparison"?"第二遍 · ":""}{item.name.replace(/\.md$/, "")} · {item.methodSource.path.startsWith("yoko-")?"对标原文":item.methodSource.path.startsWith("unified-film/")?"工作台本地化规则":item.role === "method" ? "通用" : "项目资料"}</summary>
        {["story","script","plan","prompts","publish"].includes(stage) && item.methodSource.path.startsWith("sections/") && <label><input type="checkbox"
          checked={!disabled.includes(item.name)} onChange={e=>setOff(e.target.checked?disabled.filter((n:string)=>n!==item.name):[...disabled,item.name])}/>下次起草读取这节方法</label>}
        <p className="muted tiny">{item.methodSource.source?.path ?? item.methodSource.path} {item.methodSource.source?.lines} · {item.methodSource.sha256.slice(0, 16)}</p>
        <div className="field">{item.text}</div>
      </details>)}
      {off!==null && <button className="btn primary" onClick={()=>act.run(async()=>{await cmd("set_stage_methods",{episode_id:eid,stage,disabled});close();})}>保存阶段方法</button>}
      <ErrorLine error={act.error}/></>}
  </Modal>;
}

function StageView({ stage, ctx }: { stage: string; ctx: Ctx }) {
  const {hash} = useProjectRoute();
  if (FILM_FIELDS[stage]) return <FilmDocument stage={stage} ctx={ctx}/>;
  switch (stage) {
    case "story": return <StoryResult ctx={ctx} />;
    case "script": return <ScriptView ctx={ctx} />;
    case "plan": return <PlanView ctx={ctx} />;
    case "worksheet": return <AssetWorksheet ctx={ctx} space={<SpaceView ctx={ctx} scenesOnly/>}/>;
    case "shot_assets": return artifactFor(ctx.view,"plan")?.adopted_version_id
      ? <><NeedsTable ctx={ctx}/><CastView ctx={ctx}/><a className="btn" href={`#/e/${ctx.eid}/assets?tab=canvas`}>九宫格与画布</a></>
      : <p>这一部分在 08 分镜采用后开放，用来补每一段需要的状态图和九宫格。</p>;
    case "space": return <SpaceView ctx={ctx} />;
    case "cast": return artifactFor(ctx.view,"plan")?.adopted_version_id ? <CastView ctx={ctx}/> : <p>角色出镜登记在 08 分镜采用后开放。</p>;
    case "assets": return <AssetsView ctx={ctx} />;
    case "prompts": return <PromptsView ctx={ctx} />;
    case "review": return <ReviewView ctx={ctx} />;
    case "publish": return <PublishView ctx={ctx} />;
    case "delivery": {
      const id = new URLSearchParams(hash.split("?")[1]).get("delivery");
      return id ? <SelectedDelivery ctx={ctx} id={id}/> : <DeliveryView ctx={ctx}/>;
    }
    default: return <PackageView ctx={ctx} />;
  }
}

// ---------------------------------------------------------------- common pieces
const PREV: Record<string, string> = Object.fromEntries(Object.entries(NEXT_FILM_STAGE).map(([a,b])=>[b,a]));
function useVersion(ctx: Ctx, stage: string) {
  const art = artifactFor(ctx.view, stage);
  const upstreamAdopted = !!PREV[stage] && (!!ctx.view.artifacts.find((a: any) => a.stage === PREV[stage])?.adopted_version_id || !!ctx.view.skippedStages[PREV[stage]]);
  useEffect(() => { if (!art && upstreamAdopted) cmd("open_stage", { episode_id: ctx.eid, stage }).then(ctx.reload).catch(() => null); }, [art?.id, upstreamAdopted]);
  const versions = art?.versions ?? [];
  const current = versions.find((v: any) => v.id === ctx.focus) ?? versions.find((v: any) => v.id === art?.adopted_version_id) ?? versions[versions.length - 1];
  return { art, versions, current };
}

function VersionTabs({ ctx, art, current, extra }: { ctx: Ctx; art: any; current: any; extra?: React.ReactNode }) {
  const [show, setShow] = useState(false);
  const [compare,setCompare] = useState(false);
  const { go } = useProjectRoute();
  const act=useAction();
  const refs=useQuery<any>(show && current ? "version_sources":null,{version_id:current?.id});
  return <div className="row version-controls">{art.versions.length > 4 ? <select aria-label="选择稿件版本" value={current?.id ?? ""} onChange={e=>ctx.setFocus(e.target.value)}>
    {[...art.versions].reverse().map(v=><option key={v.id} value={v.id}>{rno(v.display_no)}{v.aiAdopted?" · AI 采用":""}{v.origin==="manual"?" 手稿":v.origin==="combined"?" 组合":""}{v.id===art.adopted_version_id?" · 已采用":""}</option>)}
  </select> : art.versions.map((v: any) => (
    <button key={v.id} title={rno(v.display_no)+" · "+new Date(v.created_at*1000).toLocaleString()} className={`pill ${v.id === current?.id ? "on" : ""}`} onClick={() => ctx.setFocus(v.id)}>
      {rno(v.display_no)}{v.aiAdopted?" · AI 采用":""}{v.origin === "manual" ? " 手稿" : v.origin === "combined" ? " 组合" : ""}{art.adopted_version_id === v.id ? " ✓" : ""}</button>))}
    {current && <time className="muted small" dateTime={new Date(current.created_at*1000).toISOString()}>{new Date(current.created_at*1000).toLocaleString()}</time>}
    {art.versions.length>1 && <button className="btn" onClick={()=>setCompare(true)}>比较版本</button>}
    {compare && <VersionCompare versions={art.versions} current={current.id} adopted={art.adopted_version_id} close={()=>setCompare(false)}/>}
    <button className="btn" disabled={!current} onClick={()=>setShow(true)}>版本来源</button>
    {current && current.id!==art.versions[art.versions.length-1]?.id && <button className="btn" disabled={act.busy || ctx.locked} onClick={()=>act.run(async()=>{
      const r=await ctx.write("restore_version",{version_id:current.id,expected_adopted_version_id:art.adopted_version_id});
      await ctx.reload(); ctx.setFocus(r.versionId);})}>恢复此完整稿为新版本</button>}<ErrorLine error={act.error}/>
    {extra}
    {show && <Modal width={760} onClose={()=>setShow(false)}><h2>{rno(current.display_no)} 的来源</h2>
      {refs.error && <p className="error">{refs.error}</p>}
      {refs.data && <><p>来源 {refs.data.origin} {refs.data.base ? " · 基版 "+refs.data.base : ""} {refs.data.run ? " · 运行 "+refs.data.run : ""}</p>
        {refs.data.inputs.length===0 && <p>这个版本没有直接读取外部参考的运行记录。</p>}
        {refs.data.inputs.map((item:any)=><details key={item.sha256+item.name}>
          <summary>{item.upstreamVersion ? "起草时采用的"+(FILM_STAGES.find(([key])=>key===item.upstreamVersion.stage)?.[1].slice(3) || item.upstreamVersion.stage)+" · "+rno(item.upstreamVersion.display_no) :
            item.name+" · "+(({trial_reference:"阶段试写",citation:"引用资料",visual_reference:"导演色卡",method:"制作方法",project_method:"项目方法",step_requirements:"本步需求",reference_pack:"参考包",adaptation_source:"改编来源"} as Record<string,string>)[item.role] || item.role)}</summary>
          {item.upstreamVersion && <button className="btn" onClick={() => { setShow(false); go(`#/e/${ctx.eid}/${item.upstreamVersion.stage}?v=${item.upstreamVersion.id}`); }}>打开{item.upstreamVersion.stage==="story"?"故事":""}原稿</button>}
          <small>输入文件 SHA-256 {item.sha256}</small>{!refs.data.run && <p className="muted small">来源稿的运行 · {item.sourceRuns?.join("、")}</p>}
          <VersionSourceText role={item.role} text={item.text}/></details>)}</>}
      <button className="btn" onClick={()=>setShow(false)}>关闭</button></Modal>}</div>;
}

function storyAdoptionHint(version:any,adopted:boolean) {
  if(adopted) return "本版已采用，可到「02 角色」起草人物小传。";
  const gate=version.gate;
  if(gate.formatErrors.length) return "暂不能采用："+gate.formatErrors[0];
  const issues=gate.blocking.length+gate.unresolved.length;
  if(issues) return "暂不能采用。请先处理本版的 "+issues+" 条独审意见。";
  if(gate.reviewRequired && !gate.review) return "请先完成独立审查，再采用这版故事。";
  return "认可这版故事后，可点「采用并继续」进入角色创作。";
}

function AdoptBar({ ctx, art, version, hint, next }: { ctx: Ctx; art: any; version: any; hint: string; next?: string }) {
  const { go } = useProjectRoute();
  const act = useAction();
  const [confirmed,setConfirmed]=useState<string|null>(null);
  if (!version) return null;
  const adoptedHere = art.adopted_version_id === version.id;
  const remainder=version.gate.sourceRemainder?.trim();
  const adoption = {art,version,eid:ctx.eid,scenes:episodeScenes(ctx.view),disabled:!version.gate.adoptable || (!!remainder && confirmed!==version.id)};
  return (
    <div className={art.stage==="asset_design" ? "actionbar asset-next-bar" : "actionbar"}>
      <span className="muted small">{art.stage==="story" ? storyAdoptionHint(version,adoptedHere) : art.stage==="asset_design" ? "当前素材清单 "+rno(version.display_no) : hint}</span>
      <DecisionMessageSummary art={art} eid={ctx.eid} reload={ctx.reload}/>
      {!adoptedHere && art.adopted_version_id && <button className="btn" onClick={() => ctx.setFocus(art.adopted_version_id)}>对比 {rno(art.versions.find((v: any) => v.id === art.adopted_version_id)?.display_no)}</button>}
      {["story","plan"].includes(art.stage) && !version.gate.review && <button className="btn primary" onClick={() => act.run(async () => { await cmd("submit_review", { version_id: version.id }); ctx.reload(); })}>发起独立审查</button>}
      {!adoptedHere && remainder && <section><h4>以下原文没有归入任何场次</h4><pre>{version.gate.sourceRemainder}</pre><label><input type="checkbox" checked={confirmed===version.id} onChange={e=>setConfirmed(e.target.checked?version.id:null)}/>已核对剩余原文</label></section>}
      {!adoptedHere && <>
        <DecisionAdoption {...adoption} onDone={async vid=>{await ctx.reload();ctx.setFocus(vid);}}>{art.stage==="stress"?"只采用报告":"只采用"}</DecisionAdoption>
        {next && <DecisionAdoption {...adoption} cont onDone={async(_vid,result)=>{await ctx.reload();go(`#/e/${ctx.eid}/${result.blockers?.length?"stress":next}`);}}>{art.stage==="stress"?"采用报告并继续":art.stage==="asset_design"?"采用清单并去备图":"采用并继续"}</DecisionAdoption>}
      </>}
      {adoptedHere && art.stage==="asset_design" && <a className="btn asset-next" href={`#/e/${ctx.eid}/worksheet`}>进入备图工作单 →</a>}
      {adoptedHere && <Tag tone="green">{rno(version.display_no)} 已采用</Tag>}
      <ErrorLine error={act.error} />
    </div>
  );
}

const FILM_FIELD_NAMES: Record<string,string> = {
  synopsis:"故事梗概",cast:"角色与身份",handoff:"制作交接",notes:"说明",scenes:"场次",rules:"世界规则",assets:"素材条目",
  shots:"镜头",segments:"生成段",retelling:"冷读复述",scope:"审查范围",diagnosis:"诊断结论",issues:"问题与证据",options:"返修方案",
  title:"标题",body:"正文",action:"动作",durationBudget:"时长预算",supplements:"制作补充",decisionNotes:"导演决定落实情况",
  id:"编号", name:"名称", identity:"身份", goal:"目标", obstacle:"阻碍", behavior:"行为特征", voice:"声音身份",
  rule:"规则", choiceImpact:"对选择的影响", evidence:"依据", heading:"场景", characters:"人物", event:"事件", result:"结果",
  identityRequired:"需要身份基准", identityReason:"基准说明", timePoint:"发生时间", elapsedTime:"距上一场", rolePositions:"各角色位置", stateChanges:"状态变化",
  durationEstimateSec:"估计秒数", stage:"返修阶段", sceneIds:"涉及场次", problem:"问题", issueId:"对应问题", repairScope:"修改范围",
  proposal:"建议", kind:"类型", roleId:"角色编号", stateId:"状态编号", description:"描述", imagePrompt:"中文出图提示词",
  evidenceStatus:"实际证据状态", sceneId:"场号", objective:"表演目标", strategy:"策略", beats:"节拍", subtext:"潜台词", voiceDirection:"声音表演",
};
function FilmValue({value}: {value:any}) {
  if (Array.isArray(value)) return value.length ? <>{value.map((item,i)=><div className="card" key={i}><FilmValue value={item}/></div>)}</> : <p className="muted">无</p>;
  if (value && typeof value === "object") return <dl>{Object.entries(value).map(([key,v])=><div key={key}><dt><b>{FILM_FIELD_NAMES[key] || key}</b></dt><dd><FilmValue value={v}/></dd></div>)}</dl>;
  return <span style={{whiteSpace:"pre-wrap"}}>{value === "lightweight" ? "轻资产" : value === "premium" ? "精细资产" : String(value ?? "")}</span>;
}
function CharacterProfiles({value,ctx}: {value:any;ctx:Ctx}) {
  const fixed = useQuery<{roles:{id:string;sha256:string}[]}>("project_role_assets",{project_id:ctx.view.episode.project_id});
  const cast = useQuery<any>("cast_matrix",{episode_id:ctx.eid});
  const [preview,setPreview] = useState<{name:string;sha:string}|null>(null);
  if (!Array.isArray(value) || !value.length) return <FilmValue value={value}/>;
  const order = ["goal","obstacle","behavior","voice","identityRequired","identityReason"];
  return <div className="character-profiles">
    {(fixed.error || cast.error) && <p className="error">{fixed.error || cast.error}</p>}
    {preview && <Modal width={800} onClose={()=>setPreview(null)}><h2>{preview.name}</h2><OriginalZoom {...preview}/></Modal>}
    {value.map((role,index)=>{
    if (!role || typeof role!=="object" || Array.isArray(role)) return <FilmValue key={index} value={role}/>;
    const extra = Object.keys(role).filter(key=>!["name","id","identity",...order].includes(key));
    const baseline = cast.data?.roster?.[role.id]?.sha256;
    const sha = baseline || fixed.data?.roles.find(item=>item.id.toLowerCase()===String(role.id).toLowerCase())?.sha256;
    const source = baseline ? "本集身份图" : "项目固定身份图";
    return <article className="character-profile" key={role.id || index}>
      <header><h3>{role.name || role.id || "未命名角色"}</h3>{role.id && <small>编号 {role.id}</small>}</header>
      {sha ? <button className="character-photo" onClick={()=>setPreview({name:role.name+" · "+source,sha})}>
        <img src={blob(sha)} alt={role.name+"身份参考"}/><span>{source} · 查看原图</span>
      </button> : cast.data && fixed.data && <small>暂无身份图</small>}
      {role.identity && <div className="profile-identity"><FilmValue value={role.identity}/></div>}
      <dl>{[...order,...extra].filter(key=>role[key]!=null && role[key]!=="").map(key=>
        <div key={key}><dt>{key==="identityRequired" ? "身份基准" : FILM_FIELD_NAMES[key] || key}</dt>
          <dd>{typeof role[key]==="boolean" ? (role[key] ? "需要" : "不需要") : <FilmValue value={role[key]}/>}</dd>
        </div>)}</dl>
    </article>;
  })}</div>;
}
function WorldRules({value}:{value:any}) {
  if (!Array.isArray(value) || !value.length) return <FilmValue value={value}/>;
  return <div className="world-rules">{value.map((item,index)=>{
    if (!item || typeof item!=="object" || !item.rule) return <FilmValue key={index} value={item}/>;
    return <article className="world-rule" key={item.id || index}>
      <div className="world-rule-text"><FilmValue value={item.rule}/></div>
      {item.choiceImpact && <p><b>对角色行动的影响</b><FilmValue value={item.choiceImpact}/></p>}
      <details><summary>查看依据与编号</summary>
        {item.evidence && <p><FilmValue value={item.evidence}/></p>}
        <small>{item.id}</small>
        {Object.entries(item).filter(([key])=>!["id","rule","choiceImpact","evidence"].includes(key)).map(([key,v])=><div key={key}>{key} · <FilmValue value={v}/></div>)}
      </details>
    </article>;
  })}</div>;
}
function TreatmentScenes({value,ctx}:{value:any;ctx:Ctx}) {
  if (!Array.isArray(value) || !value.length) return <FilmValue value={value}/>;
  const cast=artifactFor(ctx.view,"characters");
  const roles=cast?.versions.find((v:any)=>v.id===cast.adopted_version_id)?.content.characters;
  const names=new Map<string,string>((Array.isArray(roles)?roles:[]).filter(r=>r?.id && r.name).map(r=>[r.id,r.name]));
  return <div className="treatment-scenes">{value.map((scene,index)=>{
    if (!scene || typeof scene!=="object") return <FilmValue key={index} value={scene}/>;
    const {id,heading,durationEstimateSec,characters,timePoint,event,result,elapsedTime,rolePositions,stateChanges,...other}=scene;
    const positions=rolePositions && typeof rolePositions==="object" && !Array.isArray(rolePositions);
    return <article className="treatment-scene" key={id || index}>
      <header><h3>{heading || id || `第 ${index+1} 场`}</h3>{durationEstimateSec!=null && <span>预计 {durationEstimateSec} 秒</span>}</header>
      {timePoint && <p className="scene-meta"><FilmValue value={timePoint}/></p>}
      {Array.isArray(characters) && characters.every(v=>typeof v==="string")
        ? characters.length>0 && <p className="scene-meta">出场人物　{characters.map(v=>names.get(v) || v).join("、")}</p>
        : characters && <FilmValue value={characters}/>}
      <div className="scene-event"><FilmValue value={event}/></div>
      {result && <p><strong>本场结果　</strong><FilmValue value={result}/></p>}
      <details><summary>查看场间衔接与角色位置</summary>
        {elapsedTime!=null && <p>距上一场　<FilmValue value={elapsedTime}/></p>}
        {positions ? <dl>{Object.entries(rolePositions).map(([role,pos])=><div key={role}><dt>{names.get(role) || role}</dt><dd><FilmValue value={pos}/></dd></div>)}</dl> : rolePositions && <FilmValue value={rolePositions}/>}
        {stateChanges && <p>状态变化　<FilmValue value={stateChanges}/></p>}
        {id && <small>场次编号　{id}</small>}
        {Object.entries(other).map(([key,v])=><div key={key}>{FILM_FIELD_NAMES[key] || key}　<FilmValue value={v}/></div>)}
      </details>
    </article>;
  })}</div>;
}
function StressIssues({issues,options,ctx,report}:{issues:any[];options:any[];ctx:Ctx;report:any}) {
  return <div className="stress-issues">{issues.map(issue=>{
    const proposals=options.filter(option=>option.issueId===issue.id);
    const target=FILM_STAGES.find(([key,label])=>key!=="stress" && (key===issue.stage || label.split(" ")[0]===issue.stage));
    return <article className="film-output-body stress-issue" key={issue.id}>
      <header><strong>问题 {issue.id}</strong><b className={"stress-grade "+stressGrade(report,issue)}>{{blocking:"必须修正",normal:"普通意见"}[stressGrade(report,issue) as string] || "待分级"}</b>
        {issue.stage && <span>返修阶段 {target?.[1] || issue.stage}</span>}
        {issue.sceneIds?.length>0 && <span>涉及场次　{issue.sceneIds.map((id:string)=>id.replace(/^scene_0*(\d+)$/, "第 $1 场")).join("、")}</span>}
      </header>
      <p>{issue.problem}</p>
      <div className="stress-evidence"><b>依据</b><FilmValue value={issue.evidence}/></div>
      {proposals.length>0 && <details><summary>查看返修方案（{proposals.length}）</summary>{proposals.map((option,i)=>
        <div key={i}><p>{option.proposal}</p><small>修改范围　{option.repairScope}</small>
          {target && <a className="btn stress-repair-link" href={`#/e/${ctx.eid}/${target[0]}?report=${encodeURIComponent(report.id)}&option=${options.indexOf(option)}`}>去{target[1].slice(3)}处理此方案 →</a>}</div>)}</details>}
    </article>;
  })}</div>;
}

function StressRepair({ctx,stage,reportId,optionIndex}:{ctx:Ctx;stage:string;reportId:string;optionIndex:string|null}) {
  const {go}=useProjectRoute();
  const act=useAction();
  const report=artifactFor(ctx.view,"stress")?.versions.find((v:any)=>v.id===reportId);
  const option=optionIndex && report?.content.options?.[Number(optionIndex)];
  const issue=report?.content.issues?.find((item:any)=>item.id===option?.issueId);
  const target=artifactFor(ctx.view,stage);
  const base=target?.versions.find((v:any)=>v.id===target.adopted_version_id);
  if (!issue || !option || !base) return <p>此返修方案或目标阶段采用稿不可用。</p>;
  const title=FILM_STAGES.find(([key])=>key===stage)?.[1];
  const goal=[`根据故事审查 ${rno(report.display_no)} 的 ${issue.id}，修订${title} ${rno(base.display_no)}，提交本阶段完整新稿。`,
    "问题："+issue.problem,"依据："+issue.evidence,"选定方案："+option.proposal,"修改范围："+option.repairScope].join("\n");
  return <section className="card stress-repair"><b>审查返修 · {issue.id} · {title}</b><p>{option.proposal}</p>
    <p>本次只为{title}生成待采用稿。{ctx.locked?"先点上方「修改本阶段」解除采用稿保护。":""}</p>
    <button className="btn primary" disabled={ctx.locked || act.busy} onClick={()=>act.run(async()=>{
      await ctx.write("create_task",{episode_id:ctx.eid,stage,base_version_id:base.id,workspace_revision:target.revision,goal});
      go(`#/e/${ctx.eid}/${stage}`);await ctx.reload();
    })}>基于 {rno(base.display_no)} 生成返修稿</button> <a className="btn" href={`#/e/${ctx.eid}/stress?v=${reportId}`}>返回审查报告</a><ErrorLine error={act.error}/>
  </section>;
}

function AssetDesignCards({value}:{value:any}) {
  if (!Array.isArray(value) || !value.length) return <FilmValue value={value}/>;
  const kinds:Record<string,string>={character:"角色",prop:"道具",scene:"场景"};
  const states:Record<string,string>={verified:"已核验",untested:"待验证"};
  return <div className="asset-design-list">{value.map((asset,i)=>{
    if (!asset || typeof asset!=="object") return <FilmValue key={i} value={asset}/>;
    const {name,roleId,stateId,kind,id,sceneIds,evidenceStatus,description,imagePrompt,...extra}=asset;
    const subject=name || [roleId,stateId].filter(Boolean).join(" · ") || id;
    return <article key={i} className="film-output-body asset-design-card">
      <header><h3>{subject}</h3><span>{kinds[kind] || kind}</span></header>
      {description && <p>{description}</p>}
      <div className="asset-card-meta">{Array.isArray(sceneIds) && sceneIds.length>0 && <span>涉及场次　{sceneIds.map(String).map(s=>s.replace(/^scene_0*(\d+)$/, "第 $1 场")).join("、")}</span>}
        {evidenceStatus && <span>证据状态　{states[evidenceStatus] || evidenceStatus}</span>}</div>
      {imagePrompt && <details><summary>查看出图提示词</summary><FilmValue value={imagePrompt}/></details>}
      <details><summary>素材编号与详情</summary><FilmValue value={{id,roleId,stateId,...extra}}/></details>
    </article>;
  })}</div>;
}

function FilmDocument({stage,ctx}: {stage:string;ctx:Ctx}) {
  const {art,current} = useVersion(ctx,stage);
  const title = FILM_STAGES.find(([key])=>key===stage)?.[1];
  const act = useAction();
  const [editing,setEditing] = useState(false);
  const [draft,setDraft] = useState<Record<string,string>>({});
  const issues=current?.content.issues;
  const options=current?.content.options;
  const groupedReview=stage==="stress" && !editing && Array.isArray(issues) && issues.length>0
    && issues.every(i=>typeof i?.id==="string" && typeof i.problem==="string" && (!i.sceneIds || (Array.isArray(i.sceneIds) && i.sceneIds.every((v:any)=>typeof v==="string"))))
    && Array.isArray(options) && options.every(o=>typeof o?.proposal==="string" && typeof o.repairScope==="string" && issues.some(i=>i.id===o.issueId));
  return <section data-film-stage={stage}><h1>{title}</h1>
    {stage==="stress" && <p className="muted">在导演助手对话里查看分级，选择方案后点「修订这条」。要按当前稿进入素材阶段，点「采用报告并继续」。</p>}
    {!art && <><p>上游成果采用或记录跳过原因后，可打开本阶段。</p><button className="btn" onClick={()=>act.run(async()=>{await cmd("open_stage",{episode_id:ctx.eid,stage});await ctx.reload();})}>打开本阶段</button></>}
    {art && <VersionTabs ctx={ctx} art={art} current={current} extra={stage==="stress" && <RepairProgress view={ctx.view} stage={stage} reload={ctx.reload}/>}/>}
    {art && !current && FIRST_DRAFT[stage] && !ctx.view.skippedStages[stage] && <p className="muted">可在「创作对话」中查看标准提示词并起草本阶段稿件。</p>}
    {art && !current && !FIRST_DRAFT[stage] && <p className="muted">{stage==="characters" && artifactFor(ctx.view,"story")?.adopted_version_id
      ? (art.tasks.length ? "角色小传尚未出稿。可在导演助手查看任务状态。" : "本集角色小传尚未起草。在导演助手点击「根据故事稿起草角色小传」，生成后即可在这里查看。")
      : "在导演助手选择“起草”，写下本阶段要求，生成待采用稿。"}</p>}
    {current && <>
      {stage==="asset_design" && current.gate.formatErrors.length>0
        ? <aside className="film-output asset-errors"><strong>清单已生成，补全以下内容后才能采用</strong><ul>{current.gate.formatErrors.map((error:string,i:number)=><li key={i}>{error}</li>)}</ul><button className="btn" disabled={ctx.locked || act.busy || art.tasks.some((t:any)=>t.runs.some((r:any)=>["running","queued"].includes(r.status)))} onClick={()=>act.run(async()=>{
            await ctx.write("create_task",{episode_id:ctx.eid,stage,base_version_id:current.id,workspace_revision:art.revision,
              goal:"基于当前素材清单解决以下校验问题，保留其余有效内容。已登记补充沿用资料中的原因；新增补充核对依据后说明原因。请提交完整修订稿。\n"+current.gate.formatErrors.join("\n")});
            ctx.setFocus(null);await ctx.reload();
          })}>让创作助手修正并生成新稿</button><ErrorLine error={act.error}/></aside>
        : current.gate.formatErrors.map((error:string,i:number)=><p className="error" key={i}>{error}</p>)}
      {stage==="characters" && current.gate.formatErrors.length>0 && <button className="btn primary"
        disabled={ctx.locked || act.busy || art.tasks.some((t:any)=>t.runs.some((r:any)=>["queued","running"].includes(r.status)))}
        onClick={()=>act.run(async()=>{
          await cmd("create_task",{episode_id:ctx.eid,stage,base_version_id:current.id,
            goal:"根据已采用故事稿、项目角色设定和本稿备注，补全本集角色表。请提交 characters 字段的完整数组，每个出场角色单列一条小传，保留有效备注。"});
          ctx.setFocus(null);await ctx.reload();
        })}>补全角色表，生成新稿</button>}
      {FILM_FIELDS[stage].map(([key,label])=>{
        const value = current.content[key];
        if (groupedReview && key==="options") return null;
        if (groupedReview && key==="issues") return <section className="film-output" key={key}><h2>{label}</h2><StressIssues issues={issues} options={options} ctx={ctx} report={current}/></section>;
        if (!editing && (stage==="characters" || stage==="world" || stage==="treatment") && key==="notes") {
          if (!value?.trim?.()) return null;
          return <details className="character-notes" key={current.id}>
            <summary>{stage==="world" ? "查看规则边界、待确认项与备注" : "查看起草备注"}</summary>{stage==="treatment" ? <ChatMarkdown text={value}/> : <FilmValue value={value}/>}
          </details>;
        }
        return <section className="film-output" key={key}><h2>{label}</h2>{editing
          ? <textarea aria-label={label} rows={8} value={draft[key] || ""} onChange={e=>setDraft({...draft,[key]:e.target.value})}/>
          : stage==="asset_design" && key==="assets" ? <AssetDesignCards value={value}/> : stage==="characters" && key==="characters" ? <CharacterProfiles value={value} ctx={ctx}/> : stage==="world" && key==="rules" ? <WorldRules value={value}/> : stage==="treatment" && key==="scenes" ? <TreatmentScenes value={value} ctx={ctx}/>
          : <div className="film-output-body">{stage==="treatment" && typeof value==="string" ? <ChatMarkdown text={value}/> : <FilmValue value={value}/>}</div>}</section>;
      })}
      {!editing ? <button className="btn" disabled={ctx.locked} onClick={()=>{setDraft(Object.fromEntries(FILM_FIELDS[stage].map(([key])=>[key,typeof current.content[key]==="string"?current.content[key]:JSON.stringify(current.content[key] ?? [],null,2)])));setEditing(true);}}>手工修改为新版本</button>
        : <><p className="muted small">记录数组使用 JSON；保存为新版本后再决定是否采用。</p><button className="btn primary" onClick={()=>act.run(async()=>{
          const jsonFields=new Set(["characters","rules","scenes","issues","options","assets"]);
          const content=Object.fromEntries(Object.entries(draft).map(([key,value])=>[key,jsonFields.has(key)?JSON.parse(value):value]));
          const result=await ctx.write("save_manual",{artifact_id:art.id,base_version_id:current.id,content});await ctx.reload();ctx.setFocus(result.versionId);setEditing(false);
        })}>保存新版本</button><button className="btn" onClick={()=>setEditing(false)}>取消编辑</button></>}
      <AdoptBar ctx={ctx} art={art} version={current} hint={stage==="stress"?"采用报告仅保存审查结论":"确认本阶段完整内容后采用"} next={stage==="asset_design" ? "worksheet" : NEXT_FILM_STAGE[stage]}/>
      {stage==="asset_design" && current.id===art.adopted_version_id && <RoleStates eid={ctx.eid} assets={ctx.assets} versionId={current.id}/>}
      {stage==="asset_design" && <a className="btn" href={`#/e/${ctx.eid}/worksheet`}>去备图</a>}
    </>}
    {OPTIONAL_FILM_STAGES.has(stage) && ctx.view.skippedStages[stage] && <a className="btn" href={`#/e/${ctx.eid}/${NEXT_FILM_STAGE[stage]}`}>继续下一阶段</a>}
    <ErrorLine error={act.error}/>
  </section>;
}

// ---------------------------------------------------------------- 01 故事稿 (J06, J36, J54, J59, J60)
function storyText(value:unknown):string {
  if (typeof value==="string") return value;
  return value==null ? "" : JSON.stringify(value,null,2);
}
function StoryResult({ctx}:{ctx:Ctx}) {
  const {hash,go}=useProjectRoute();
  const art=artifactFor(ctx.view,"story");
  const adopted=art?.versions.find((v:any)=>v.id===art.adopted_version_id);
  const located=art?.versions.find((v:any)=>v.id===ctx.location?.versionId);
  const final=located ?? adopted ?? art?.versions.at(-1);
  const passages=located ? ctx.location!.passages : [];
  const body=useRef<HTMLElement>(null);
  useLayoutEffect(()=>{
    const hit=body.current?.querySelector("mark");
    if(hit) hit.scrollIntoView({block:"nearest",inline:"nearest"});
  },[ctx.location]);
  const preview=ctx.focus || new URLSearchParams(hash.split("?")[1]).get("run");
  return <><h1>{FILM_STAGES[0][1]}</h1><TopicRuntimeTag target={ctx.view.topicRuntime}/>{final?<article ref={body} className="story-result"><small className={final.id===adopted?.id?"ok":"muted"}>{rno(final.display_no)} · {final.id===adopted?.id?"已采用":"草稿 · 待采用"}</small>
    <div className="story-reading">{storyText(final.content.synopsis).split(/\n\s*\n/).map((para,i)=><p key={i}>{para.split("\n").map((line,j)=><span key={j}>{j>0 && <br/>}<MarkPassage text={line} passage={passages.find(p=>p.field==="synopsis" && p.text===line)}/></span>)}</p>)}
      {passages.filter(p=>p.field!=="synopsis").map((p,i)=><p key={"source"+i}><small>{p.label}</small><br/><MarkPassage text={p.text} passage={p}/></p>)}</div>
    <a href={`#/e/${ctx.eid}/story?v=${final.id}`}>查看稿件与版本</a> · <a href={"/api/story/"+final.id+"/markdown"} download="故事梗概.md">下载故事梗概</a>
    <AdoptBar ctx={ctx} art={art} version={final} hint="确认完整梗概后采用；人物小传在02另存" next="characters"/>
    </article>:
    <p className="muted">还没有生成故事稿。起草完成后，正文会显示在这里。</p>}
    {preview && <Modal width={980} onClose={()=>go("#/e/"+ctx.eid+"/story")}><div className="spread"><strong>稿件预览与版本管理</strong><button className="btn" onClick={()=>go("#/e/"+ctx.eid+"/story")}>关闭预览</button></div><StoryView ctx={ctx}/></Modal>}</>;
}
function StoryView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "story");
  const {hash,go} = useProjectRoute();
  const params=new URLSearchParams(hash.split("?")[1]);
  const requestedRun = params.get("run");
  const result = requestedRun && art?.versions.find((v:any)=>v.source_run_id===requestedRun);
  const run = requestedRun && art?.tasks.flatMap((task:any)=>task.runs).find((r:any)=>r.id===requestedRun);
  useEffect(()=>{
    if (result) go(`#/e/${ctx.eid}/story?v=${result.id}`);
  },[requestedRun,result?.id,ctx.eid,go]);
  const sources = useQuery<any>(current && ctx.view.refPacks?.length ? "version_sources" : null,{version_id:current?.id,reference_only:true});
  const packs = ctx.view.refPacks?.filter((p:any)=>sources.data?.inputs.some((i:any)=>
    i.role==="reference_pack" && i.name===`pack-${p.packId}.txt`)) ?? [];
  const units = useMemo(()=>{
    const options: AdaptationUnit[] = [];
    for (const item of sources.data?.inputs ?? []) {
      if (item.role!=="adaptation_source") continue;
      const frozen = JSON.parse(item.text);
      const pack = ctx.view.refPacks.find((p:any)=>p.packId===frozen.pack);
      for (const unit of frozen.units) options.push({pack:frozen.pack,unit:unit.code,original:unit.original,
        label:`${pack ? pack.source+" v"+pack.version : frozen.pack} · ${unit.code}`});
    }
    return options;
  },[sources.data,ctx.view.refPacks]);
  const requestedPair=params.get("compare");
  const [requestedTab, setTab] = useState(requestedPair ? "compare" : "synopsis");
  const legacyTabs = [["cast", "角色表"], ["handoff", "交接说明"]]
    .filter(([field])=>storyText(current?.content[field]).trim());
  const tab = ["cast","handoff"].includes(requestedTab) && !legacyTabs.some(([key])=>key===requestedTab)
    ? "synopsis" : requestedTab;
  const [pairSource, setPairSource] = useState(requestedPair);
  useEffect(()=>{if (requestedPair) {setPairSource(requestedPair);setTab("compare");}},[hash,requestedPair]);
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState<any>({});
  const act = useAction();
  useEffect(() => { setDraft(current?.content ?? {}); setEdit(false); }, [current?.id]);
  const editId=params.get("edit")==="1" ? params.get("v") : null;
  useEffect(()=>{if (!ctx.locked && editId && current?.id===editId) {setEdit(true);setTab("synopsis");}},[hash,editId,current?.id,ctx.locked]);
  const k = ctx.view.kickoff;
  const manuscript = ctx.view.storyManuscript;
  const pictures = ctx.assets.filter(a=>a.origin==="kickoff_reference" && a.version_no===1);
  const sourcePanel = <><TopicRuntimeTag target={ctx.view.topicRuntime}/>{pictures.length>0 && <details className="card"><summary>本集开工参考图 · {pictures.length} 张</summary>
    <div className="row">{pictures.map(a=><figure key={a.id}><img src={blob(a.sha256)} alt={a.name} width={96}/>
      <figcaption>{a.name}</figcaption></figure>)}</div></details>}{manuscript && <details className="card"><summary>本集补充稿件 · {manuscript.original_name}</summary>
    <div className="muted tiny">用于下一次故事任务 · 文件指纹 {manuscript.sha256.slice(0,12)}</div>
    <pre className="field">{manuscript.parsed_text}</pre></details>}</>;
  if (requestedRun) return <div><h1>改编故事稿</h1><p role="status">{result ? "正在打开新稿…" :
    run && ["queued","running"].includes(run.status) ? "改编稿生成中…" :
    run?.reason || (run ? "本次运行尚无改编稿。" : "本集未找到这次改编任务。")}</p>
    <a href={`#/e/${ctx.eid}/story`}>查看本集故事稿</a></div>;
  if (!current) return (
    <div><div className="stagehead"><div><h1>故事稿</h1><div className="muted small">尚无稿件 · 来源：{k.source_type === "import" ? ctx.view.sourceImport?.original_name : k.idea_text ?? "选题卡"}</div></div></div>
      {sourcePanel}
      {ctx.view.sourceImport && <div className="field small">{ctx.view.sourceImport.parsed_text}</div>}
      <p className="muted">在右侧导演助手输入要求开始起草；导入的故事稿或剧本只作故事来源，起草后先过故事独审。</p></div>);
  const review = current.reviews[current.reviews.length - 1];
  const synopsis = storyText(current.content.synopsis);
  const chars = Array.from(synopsis).length;
  const paras = synopsis.split("\n").filter(Boolean);
  return (
    <div>
      <div className="stagehead">
        <div><h1>故事稿</h1><div className="muted small">{rno(current.display_no)} {current.id === art.adopted_version_id ? "已采用" : "草稿"} · 梗概 {chars} 字 · 来源：{k.source_type === "import" ? `导入 ${ctx.view.sourceImport?.original_name}` : k.source_type === "topic" ? "选题卡" : `一个想法「${k.idea_text}」`}{k.relation === "continue" ? ` · 承接遗留 ${k.carried_facts.join("；")}` : ""}{packs.length ? ` · 改编自参考包 ${packs.map((p:any)=>`${p.source} v${p.version}`).join("；")}` : ""}</div></div>
        <div className="small"><a href={`/api/story/${current.id}/markdown`} download="故事稿.md">下载本版故事稿</a></div>
        <VersionTabs ctx={ctx} art={art} current={current} />
      </div>
      <div className="spread"><div className="tabs">{[["synopsis", "故事梗概"], ...legacyTabs, ["notes", "开发辅助"], ["review", "独立审查"], ["compare","并排出稿"], ...(ctx.view.sourceImport ? [["source", "来源原文"]] : [])].map(([t, l]) => (
        <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => {if(t==="compare") setPairSource(art.comparisons?.find((c:any)=>c.version_id===current.id)?.source_version_id || current.id);setTab(t);}}>{l}</button>))}</div>
        {tab!=="compare" && <div className="row">{edit ? <><button className="btn" onClick={() => setEdit(false)}>取消</button>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await ctx.write("save_manual", { artifact_id: art.id, base_version_id: current.id, content: draft }); await ctx.reload(); ctx.setFocus(r.versionId); })}>保存为手稿</button></>
          : <button className="btn" disabled={ctx.locked} onClick={() => {setDraft(current.content);setEdit(true);}}>✎ 编辑</button>}</div>}</div>
      {sourcePanel}
      {tab === "synopsis" && (edit ? <textarea style={{ minHeight: 260, marginTop: 12 }} value={storyText(draft.synopsis)} onChange={(e) => setDraft({ ...draft, synopsis: e.target.value })} /> : (
        <div className="field story-reading" style={{ marginTop: 12, border: "1px solid #2b5fb8" }}>{paras.map((p: string, i: number) => (
          <div key={i} className={`para ${ctx.sel?.data.paragraph === i + 1 ? "sel" : ""}`} onClick={() => ctx.setSel({ label: `故事梗概 第${i + 1}段`, data: { field: "synopsis", paragraph: i + 1, text: p.slice(0, 40) } })}>{p}</div>))}</div>))}
      {tab === "cast" && (edit ? <textarea style={{ minHeight: 160, marginTop: 12 }} value={storyText(draft.cast)} onChange={(e) => setDraft({ ...draft, cast: e.target.value })} /> : <CastTable md={storyText(current.content.cast)} />)}
      {tab === "handoff" && (edit ? <textarea style={{ marginTop: 12 }} value={storyText(draft.handoff)} onChange={(e) => setDraft({ ...draft, handoff: e.target.value })} /> : <div className="field" style={{ marginTop: 12 }}>{storyText(current.content.handoff)}</div>)}
      {tab === "source" && <div className="field" style={{ marginTop: 12 }}><div className="muted small">导入原文（只作故事来源）· {ctx.view.sourceImport.original_name} · sha256 {ctx.view.sourceImport.sha256.slice(0, 12)}</div>{ctx.view.sourceImport.parsed_text}</div>}
      {tab === "notes" && <div className="col">
        {Object.entries({spine: "故事脊", timeline: "时间线", props: "规则与道具表", causes: "因果核对表", notes: "其他开发说明"}).map(([key, title]) =>
          (edit || current.content[key]) && <label className="col" key={key}><b>{title}</b>
            {edit ? <textarea aria-label={title} value={storyText(draft[key])} onChange={e => setDraft({...draft, [key]: e.target.value})}/> :
              <div className="field" onClick={() => ctx.setSel({ label: title, data: { field: key } })}>{storyText(current.content[key])}</div>}</label>)}
        {(current.content.adaptation != null || (edit && units.length>0)) && <StoryAdaptation units={units} value={edit ? draft.adaptation : current.content.adaptation}
          update={edit ? (adaptation:any)=>setDraft({...draft,adaptation}) : undefined}/>}
        {!edit && !["spine","timeline","props","causes","notes","adaptation"].some(k => current.content[k]) && <p className="muted">本稿未使用开发辅助表。</p>}
      </div>}
      {tab==="compare" && <StoryComparison write={ctx.write} locked={ctx.locked} key={pairSource || current.id} sourceId={pairSource || current.id} select={setPairSource}
        plan={{...k.executor_plan,...art.executorSettings}} art={art} reload={ctx.reload} open={(vid:string)=>{setTab("synopsis");go("#/e/"+ctx.eid+"/story?v="+vid);}}/>}
      {current.gate.formatErrors.length > 0 && <div className="card red" style={{ marginTop: 12 }}><b className="error">结构错误（不能放行）</b><ul className="small">{current.gate.formatErrors.map((e: string) => <li key={e}>{e}</li>)}</ul></div>}
      {tab!=="compare" && tab === "review" && <StoryReview ctx={ctx} version={current} review={review} />}
      <ErrorLine error={act.error} />
      {tab!=="compare" && <AdoptBar ctx={ctx} art={art} version={current} next="characters"
        hint={current.gate.blocking.length ? "必须守住的设定未守住：只能改故事或改设定" : current.gate.review ? "必需审核已执行，可采用" : "故事稿通过独审后才能交给制作"} />}
    </div>
  );
}

type AdaptationUnit = {pack:string;unit:string;original:string;label:string};
type AdaptationRow = Omit<AdaptationUnit,"label"> & {adapted:string;reason:string};
function StoryAdaptation({value,units,update}:{value:unknown;units:AdaptationUnit[];update?:(value:AdaptationRow[])=>void}) {
  const rows = value ?? [];
  const labels = {original:"原事件",adapted:"本集新事件",reason:"取舍理由"} as const;
  if (!Array.isArray(rows) || !rows.every(r=>r && ["pack","unit",...Object.keys(labels)].every(k=>typeof r[k]==="string"))) {
    return <details className="card"><summary>改编对照原文</summary><pre className="field">{JSON.stringify(value,null,2)}</pre></details>;
  }
  const change = (i:number,fields:Partial<AdaptationRow>)=>update?.(rows.map((r,n)=>n===i?{...r,...fields}:r));
  return <section className="card"><h2>改编对照</h2>{rows.map((row:AdaptationRow,i:number)=>{
    const selection = units.findIndex(u=>u.pack===row.pack && u.unit===row.unit);
    const unit = units[selection];
    return <div key={i} className="field">{update && units.length>0 ?
      <label>来源镜号或节拍<select aria-label="来源镜号或节拍" value={selection} onChange={e=>{
        const u=units[Number(e.target.value)]; change(i,{pack:u.pack,unit:u.unit,original:u.original});
      }}>{selection<0 && <option value={-1}>原记录 {row.unit}</option>}
        {units.map((u,n)=><option key={n} value={n}>{u.label}</option>)}</select></label>
      : <small>{unit?.label || `参考包 ${row.pack} · ${row.unit}`}</small>}
      {unit && row.original!==unit.original && <p className="small">来源原文　{unit.original}</p>}
      {Object.entries(labels).map(([key,label])=>{
        const field=key as keyof typeof labels;
        return <div key={key}>{update ? <label>{label}<textarea aria-label={label} value={row[field]}
          onChange={e=>change(i,{[field]:e.target.value})}/></label> : <div><b>{label}</b>　{row[field]}</div>}</div>;
      })}
      {update && <button className="btn" onClick={()=>update(rows.filter((_,n)=>n!==i))}>移除此行</button>}
    </div>;
  })}{update && units.length>0 && <button className="btn" onClick={()=>{
    const u=units.find(u=>!rows.some(r=>r.pack===u.pack && r.unit===u.unit)) ?? units[0];
    update([...rows,{pack:u.pack,unit:u.unit,original:u.original,adapted:"",reason:""}]);
  }}>添加改编对照</button>}</section>;
}

function CastTable({ md }: { md: string }) {
  const rows = md.split("\n").filter((l) => l.trim().startsWith("|")).map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
  const body = rows.filter((r) => !r.every((c) => /^:?-{3,}:?$/.test(c)));
  return <table className="table" style={{ marginTop: 12 }}><tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => i === 0 ? <th key={j}>{c}</th> : <td key={j}>{c}</td>)}</tr>)}</tbody></table>;
}

function StoryReview({ ctx, version, review }: { ctx: Ctx; version: any; review: any }) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const act = useAction();
  if (!review) return <div className="card" style={{ marginTop: 16 }}><b>独立审查</b><div className="muted small" style={{ marginTop: 6 }}>尚未送审。送审冻结这次读取的版本，审核者另开会话，只读输入清单。</div></div>;
  const cold = review.observations[0];
  return (
    <div style={{ marginTop: 18 }}>
      <div className="spread"><h2>{review.pass === "plan" ? "分镜审核" : "故事独立审查"} · 第 {review.pass_no} 次</h2>
        <span className="muted small">{PROVIDER_LABEL[review.reviewer]} · 另开会话（{review.isolation_level === "session" ? "会话隔离" : review.isolation_level}）· 只读取 {rno(version.display_no)} 与项目设定 · 清单 {review.manifest_hash.slice(0, 10)}</span></div>
      <div className="grid2">
        <div className="card"><div className="label" style={{ marginTop: 0 }}>冷读复述</div><div className="small">{cold?.evidence}</div></div>
        <div className="card"><div className="label" style={{ marginTop: 0 }}>对照检查（{review.observations.length - 1} 项）</div>
          {review.observations.slice(1, 7).map((o: any, i: number) => <div key={i} className="small" style={{ margin: "3px 0" }}><code className="mono">{o.objectId.slice(0, 18)}</code> {o.evidence.slice(0, 48)}</div>)}</div>
      </div>
      <div className="row" style={{ margin: "12px 0" }}><Tag tone={review.original_decision === "pass" ? "green" : "red"}>原结论 {review.original_decision === "pass" ? "通过" : "退回"}</Tag><span className="muted small">{review.issues.length} 条意见</span></div>
      {review.issues.map((i: any) => (
        <div key={i.id} className={`card ${i.blocking ? "red" : "line"}`} style={{ marginBottom: 10 }}>
          <div className="row"><Tag tone={i.blocking ? "red" : "amber"}>{i.blocking ? (i.category === "format" ? "硬错误" : "必须守住") : "普通意见"}</Tag><b>{i.loc.anchor}</b><span className="muted small">{i.category}</span></div>
          <ReviewEvidence issue={i} content={version.content}/>
          <div className="small" style={{ margin: "6px 0" }}>{i.reason}</div>
          {i.blocking ? <div className="row"><button className="btn" onClick={() => ctx.setSel({ label: `${i.loc.anchor}（审查意见）`, data: { issue: i.id, anchor: i.loc.anchor } })}>改故事</button>
            <a className="btn" href={`#/p/${ctx.view.episode.project_id}/settings`}>去项目设定</a><span className="muted tiny">没有放行入口</span></div>
            : i.waiver_reason ? <div className="ok small">已放行（仅本版本）：{i.waiver_reason}</div>
              : <div className="row"><input style={{ flex: 1 }} aria-label="放行备注（选填）" placeholder="备注（选填）" value={reasons[i.id] ?? ""} onChange={(e) => setReasons({ ...reasons, [i.id]: e.target.value })} />
                <button className="btn" onClick={() => act.run(async () => { await cmd("waive", { issue_id: i.id, version_id: version.id, reason: reasons[i.id] ?? "" }); ctx.reload(); })} disabled={act.busy}>按本版放行</button>
                <button className="btn" onClick={() => ctx.setSel({ label: `${i.loc.anchor}（审查意见）`, data: { issue: i.id } })}>修订</button></div>}
        </div>))}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 02 剧本 (J07, J24, J25, J68, J69)
const sceneDetails: Record<string, string> = {performance: "关键表演", choice: "选择与办法", response: "阻碍与回应", change: "新局面", payoff: "预期与回收", dialogue: "对白", sound: "声音",
  proposedSegments: "拟生成段", watchPurpose: "观看作用与保留过程", omit: "可省略的操作", budgetNote: "时长初估与依据"};

function ScriptBudget({ctx, script}: {ctx: Ctx; script: any}) {
  const plan = artifactFor(ctx.view, "plan");
  const chosen = plan?.versions.find((v: any) => v.id === plan.adopted_version_id);
  const budget = useQuery<any>(chosen ? "script_plan_budget" : null, {episode_id: ctx.eid, script_version: script.id, plan_version: chosen?.id});
  if (!chosen) return <p className="muted">采用制作方案后可按镜头核算分段时长。</p>;
  if (budget.error) return <p className="error">{budget.error}</p>;
  if (!budget.data) return null;
  return <div>
    <p>按已采用方案 {rno(chosen.display_no)} 的镜头时长汇总，共 {budget.data.segments.length} 段、{budget.data.total_ms / 1000} 秒。场号和原句与已采用剧本 {rno(script.display_no)} 核对。</p>
    <table className="table"><thead><tr><th>生成段</th><th>全片起点</th><th>段长</th><th>剧本场次与镜头</th></tr></thead>
      <tbody>{budget.data.segments.map((seg: any) => <tr key={seg.segment}><td>{seg.segment}</td><td>{seg.episode_start_ms / 1000} 秒</td><td>{seg.duration_ms / 1000} 秒</td>
        <td>{seg.scenes.map((group: any) => <div key={group.scene ?? ""}>{group.scene || "缺少场号"}{group.scene && !group.known && "（剧本未找到）"} · {group.shots.join("、")} · {group.duration_ms / 1000} 秒
          {group.unverifiedQuotes.length > 0 && <div className="muted tiny">原句待核对 · {group.unverifiedQuotes.join("、")}</div>}</div>)}</td></tr>)}</tbody></table>
    {budget.data.scenesOutsideSelection.length > 0 && <p>未被这些镜头引用的场次 · {budget.data.scenesOutsideSelection.join("、")}</p>}
  </div>;
}

function ScriptComparisonRows({left,right}:{left:any;right:any}) {
  const pair=[left,right];
  const sceneLists=pair.map(v=>v.content.scenes ?? []);
  const ids=new Set<string>(sceneLists.flatMap(list=>list.map((s:any)=>s.id)));
  const labels:Record<string,string>={heading:"场景",action:"动作",...sceneDetails};
  return <>{Array.from(ids,id=>{
    const scenes=sceneLists.map(list=>list.find((s:any)=>s.id===id));
    const fields=Array.from(new Set(scenes.flatMap(s=>Object.keys(s ?? {})))).filter(k=>k!=="id");
    const changed=fields.filter(k=>JSON.stringify(scenes[0]?.[k])!==JSON.stringify(scenes[1]?.[k]));
    const order=sceneLists.map(list=>list.findIndex((s:any)=>s.id===id));
    const status=!scenes[0]?"右稿新增":!scenes[1]?"右稿缺少此场":order[0]!==order[1]?"场次顺序变动":"";
    return <section key={id} className="card script-compare-scene"><h3>{id} · {scenes[1]?.heading || scenes[0]?.heading}</h3>
      <p className="small muted">{[status,changed.length ? "改动："+changed.map(k=>labels[k] || k).join("、") : "本场内容相同"].filter(Boolean).join(" · ")}</p>
      {changed.map(k=><div className="script-compare-pair" key={k}>{scenes.map((scene,i)=><div key={i}><b>{labels[k] || k}</b>
        <div><FilmValue value={scene?.[k] ?? "—"}/></div></div>)}</div>)}
    </section>;
  })}
    {(left.content.notes || right.content.notes) && <details><summary>{left.content.notes===right.content.notes?"两版说明相同":"查看两版说明差异"}</summary>
      <div className="script-compare-pair">{pair.map(v=><div key={v.id}><ChatMarkdown text={v.content.notes || "无说明"}/></div>)}</div></details>}
  </>;
}

function ScriptView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "script");
  const {hash}=useProjectRoute();
  const sourceScene=new URLSearchParams(hash.split("?")[1]).get("scene");
  const scriptBody=useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState("full");
  useEffect(()=>{
    if (!sourceScene || !current) return;
    setTab("full");
    const frame=requestAnimationFrame(()=>scriptBody.current?.querySelector<HTMLElement>('[data-script-scene="'+CSS.escape(sourceScene)+'"]')?.scrollIntoView({block:"center"}));
    return ()=>cancelAnimationFrame(frame);
  },[sourceScene,current?.id]);
  const [cmp, setCmp] = useState<any>(null);
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [editScenes, setEditScenes] = useState<any[] | null>(null);
  const [otherId,setOtherId]=useState("");
  useEffect(()=>{setPicks({});},[current?.id,otherId]);
  const [savedScroll, setSavedScroll] = useState<{ top: number; sel: Selection } | null>(null);
  const openSide = () => { setSavedScroll({ top: document.querySelector(".center")?.scrollTop ?? 0, sel: ctx.sel }); setTab("side"); };
  const closeSide = () => {
    setTab("full"); const sv = savedScroll;
    if (!sv) return;
    ctx.setSel(sv.sel);
    let tries = 0;
    const restore = () => { const el = document.querySelector(".center") as HTMLElement | null; if (!el) return;
      el.scrollTop = sv.top; if (Math.abs(el.scrollTop - sv.top) > 2 && tries++ < 60) requestAnimationFrame(restore); };
    requestAnimationFrame(restore);
  };
  const act = useAction();
  const adoptedId = art?.adopted_version_id;
  useEffect(() => {
    if (tab === "diff" && current && adoptedId && current.id !== adoptedId) q("compare", { a_id: adoptedId, b_id: current.id }).then(setCmp);
    else setCmp(null);
  }, [tab, current?.id, adoptedId]);
  if (!art) return <div className="empty"><p>正式剧本会引用已采用的故事。</p><button className="btn" onClick={ctx.openTrial}>先试写本阶段</button></div>;
  const story = artifactFor(ctx.view, "story");
  if (!current) return <div><div className="stagehead"><h1>{FILM_STAGES.find(([key])=>key==="script")?.[1]}</h1></div>
    <p className="muted">可在「创作对话」中查看标准提示词并起草本阶段稿件。</p></div>;
  const scenes = current.content.scenes ?? [];
  if(ctx.view.kickoff.sourceIntent==="locked_screenplay") return <section><h1>导演定稿 · 原文只读</h1>
    <p>{current.gate.sourceVerified?"已逐字核对":"逐字核对未通过"} · 修改原文请重新导入。</p>
    <VersionTabs ctx={ctx} art={art} current={current}/><div className="grid2"><section>{scenes.map(s=><article key={s.id}><h3>{s.id} {s.heading}</h3><p style={{whiteSpace:"pre-wrap"}}>{s.action}</p></article>)}</section>
    <section><h2>生产拆解</h2><FilmValue value={current.content.breakdown??[]}/></section></div>
    {current.gate.formatErrors.map(e=><p className="error" key={e}>{e}</p>)}
    <AdoptBar ctx={ctx} art={art} version={current} next="stress" hint="核对切分后的定稿原文"/></section>;
  const baseV = current.id !== adoptedId ? art.versions.find((v: any) => v.id === (current.base_id ?? adoptedId)) : null;
  const baseScenes = baseV ? Object.fromEntries((baseV.content.scenes ?? []).map((x: any) => [x.id, x.action])) : null;
  const changedCount = baseScenes ? scenes.filter((x: any) => baseScenes[x.id] !== x.action).length : 0;
  const others = art.versions.filter((v: any) => v.id !== current.id);
  const other=others.find(v=>v.id===otherId) ?? others.find(v=>v.id===current.base_id) ?? others.at(-1);
  return (
    <div ref={scriptBody}>
      <div className="stagehead">
        <div><h1>剧本</h1><div className="muted small">{rno(current.display_no)} {current.id === adoptedId ? "已采用" : "草稿"}{current.base_id ? `（基于 ${rno(art.versions.find((v: any) => v.id === current.base_id)?.display_no)}）` : ""} · {baseScenes ? `改动 ${changedCount} 处 · 绿色新增 / 红色删除` : `${scenes.length} 场`}</div></div>
        <VersionTabs ctx={ctx} art={art} current={current} />
      </div>
      <div className="spread"><div className="tabs">{[["full", "全文"], ["outline", "阶段大纲"], ["budget", "分段与时长预算"], ["diff", "差异"], ["side", "并排比较"]].filter(([t])=>t==="side" ? !!other : t==="diff" ? !!adoptedId && adoptedId!==current.id : true).map(([t, l]) => (
        <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => t === "side" ? openSide() : setTab(t)}>{l}</button>))}</div>
        <div className="row">{editScenes ? <><button className="btn" onClick={() => setEditScenes(null)}>取消</button>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await ctx.write("save_manual", { artifact_id: art.id, base_version_id: current.id, content: { ...current.content, scenes: editScenes } }); setEditScenes(null); await ctx.reload(); ctx.setFocus(r.versionId); })}>保存为手稿</button></>
          : <button className="btn" disabled={ctx.locked} onClick={() => setEditScenes(JSON.parse(JSON.stringify(scenes)))}>✎ 编辑</button>}</div></div>
      {editScenes && editScenes.map((sc: any, i: number) => (
        <div key={i} style={{ marginTop: 12 }}><div className="row"><input value={sc.id} onChange={(e) => { const x = [...editScenes]; x[i] = { ...sc, id: e.target.value }; setEditScenes(x); }} style={{ width: 90 }} />
          <input value={sc.heading ?? ""} onChange={(e) => { const x = [...editScenes]; x[i] = { ...sc, heading: e.target.value }; setEditScenes(x); }} placeholder="内外景 · 时段" /></div>
          <textarea value={sc.action} onChange={(e) => { const x = [...editScenes]; x[i] = { ...sc, action: e.target.value }; setEditScenes(x); }} />
          <div className="grid2">{Object.entries(sceneDetails).filter(([k])=>!["omit","watchPurpose","proposedSegments"].includes(k)).map(([key, label]) => <label className="col small" key={key}>{label}
            <input value={sc[key] ?? ""} onChange={e => setEditScenes(editScenes.map((row: any, j: number) => j === i ? {...row, [key]: e.target.value} : row))}/></label>)}</div></div>))}
      {!editScenes && tab === "full" && <div className="film-output script-scenes">{scenes.map((s: any, i: number) => { const old = baseScenes?.[s.id]; return (
        <article key={s.id} className="script-scene" data-script-scene={s.id}>
          <header><h2>{s.heading || `第 ${i+1} 场`}</h2><small>场次编号　{s.id}</small></header>
          {old !== undefined && old !== s.action ? <div className={`para ${ctx.sel?.data.scene === s.id ? "sel" : ""}`} style={{ lineHeight: 1.8 }} onClick={() => ctx.setSel({ label: `${s.id}（差异处）`, data: { scene: s.id } })}>{charDiff(old, s.action).map((d, k) => d.op === "same" ? <span key={k}>{d.text}</span> : d.op === "del" ? <del key={k} className="d">{d.text}</del> : <ins key={k} className="d">{d.text}</ins>)}</div> :
          s.action.split(/(?<=。)/).filter(Boolean).map((p: string, j: number) => (
            <div key={j} className={`para ${ctx.sel?.data.scene === s.id && ctx.sel?.data.sentence === j + 1 ? "sel" : ""}`} onClick={() => ctx.setSel({ label: `${s.id} 第${j + 1}句`, data: { scene: s.id, sentence: j + 1 } })}>{p}</div>))}
          {s.dialogue && <section className="script-lines" onClick={()=>ctx.setSel({label:s.id+" 对白",data:{scene:s.id,field:"dialogue"}})}><h3>对白</h3><p>{s.dialogue}</p></section>}
          <details><summary>表演、声音与制作说明</summary>
            <dl>{Object.entries(sceneDetails).filter(([key])=>key!=="dialogue" && s[key]).map(([key,title])=><div key={key}
              onClick={()=>ctx.setSel({label:s.id+" "+title,data:{scene:s.id,field:key}})}><dt>{title}</dt><dd>{s[key]}</dd></div>)}</dl>
            {s.source && <small>出处：故事 {rno(story?.versions.find((v:any)=>v.id===s.source.storyVersion)?.display_no)} · 节拍 {s.source.beat} · 原镜 {s.source.refShot}{s.source.pack ? ` · 参考包 ${s.source.pack}` : ""}</small>}
          </details>
        </article>); })}</div>}
      {tab === "budget" && !editScenes && <div>
        {scenes.filter((s: any) => s.proposedSegments || s.watchPurpose || s.omit || s.budgetNote).map((s: any) => <div className="card" key={s.id}><b>{s.id} · {s.heading}</b>
          {["proposedSegments", "watchPurpose", "omit", "budgetNote"].map(key => s[key] && <p key={key}>{sceneDetails[key]} · {s[key]}</p>)}</div>)}
        {current.id === adoptedId ? <ScriptBudget ctx={ctx} script={current} /> : <p className="muted">切换到已采用剧本可查看与制作方案的对应关系。</p>}
      </div>}
      {tab === "outline" && <table className="table" style={{ marginTop: 12 }}><tbody>{scenes.map((s: any) => <tr key={s.id}><td style={{ width: 80 }}>{s.id}</td><td className="muted">{s.heading}</td><td>{s.action.slice(0, 60)}…</td></tr>)}</tbody></table>}
      {tab === "diff" && (cmp ? <div className="field" style={{ marginTop: 12 }}>{(cmp.fields.scenes ?? []).map((d: any, i: number) => (
        d.op === "same" ? <span key={i}>{d.text}</span> : d.op === "del" ? <del key={i} className="d">{d.text}</del> : <ins key={i} className="d">{d.text}</ins>))}</div>
        : <div className="muted" style={{ marginTop: 12 }}>当前版本即采用版，没有差异。</div>)}
      {tab === "side" && other && (
        <div className="film-output">
          <label>对照版本 <select value={other.id} onChange={e=>setOtherId(e.target.value)}>{others.map(v=><option key={v.id} value={v.id}>{rno(v.display_no)}</option>)}</select></label>
          <div className="script-compare-pair">{[other,current].map((v,i)=><div className="card" key={v.id}>
            <strong>{i?"当前稿":"对照稿"} · {rno(v.display_no)}</strong>
            <div className="row">{["scenes","notes"].map(f=><button key={f} className={`pill ${picks[f]===v.id?"on":""}`} onClick={()=>setPicks({...picks,[f]:v.id})}>取{f==="scenes"?"场次":"说明"}</button>)}</div>
          </div>)}</div>
          <ScriptComparisonRows left={other} right={current}/>
          <div className="row" style={{ marginTop: 10 }}><button className="btn primary" disabled={ctx.locked || Object.keys(picks).length === 0} onClick={() => act.run(async () => {
            const r = await ctx.write("combine", { artifact_id: art.id, picks }); await ctx.reload(); ctx.setFocus(r.versionId); setTab("full"); })}>组合成新的待审稿</button>
            <button className="btn" onClick={closeSide}>关闭比较</button></div>
        </div>)}
      <ErrorLine error={act.error} />
      <AdoptBar ctx={ctx} art={art} version={current} next="stress" hint="采用前先看影响 · 压力测试可按本集需要跳过" />
    </div>
  );
}

function RhythmReview({version,save}:{version:string;save?:(text:string)=>Promise<void>}) {
  const [open,setOpen]=useState(false),[draft,setDraft]=useState<string|null>(null);
  useEffect(()=>setDraft(null),[version]);
  const audit=useQuery<any>(open?"rhythm_review":null,{version_id:version});
  const act=useAction();
  return <details className="card" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>镜头充分性与节奏核对</summary>
    {audit.error&&<p className="error">{audit.error}</p>}
    {audit.data&&<><p className="small">{save?"本版方案对照已采用提示词":"本版提示词对照已采用方案"}</p>
      {save?<><label>镜头充分性联合表<textarea aria-label="镜头充分性联合表" rows={7} value={draft??audit.data.markdown}
        onChange={e=>setDraft(e.target.value)}/></label>
        <div className="row"><button className="btn" disabled={act.busy||draft===null} onClick={()=>act.run(()=>save(draft!))}>将联合表保存为方案新版</button>
        <button className="btn" disabled={act.busy} onClick={()=>act.run(()=>save(""))}>由镜头字段重建</button></div></>:<pre className="field">{audit.data.markdown}</pre>}
      {save && audit.data.boardRows?.length>0 && <ShotBoard key={version} rows={audit.data.boardRows} save={save}/>}
      {!audit.data.hasPrompts&&<p>采用提示词后可核对每个时间窗和切镜。</p>}
      {audit.data.legacy.length>0&&<p>以下段尚无可核对的 readable_v3 时间窗：{audit.data.legacy.join("、")}</p>}
      {audit.data.errors.map((s:string)=><p key={s} className="error">{s}</p>)}
      {audit.data.report&&<><table className="table"><thead><tr><th>生成段</th><th>动作窗</th><th>实际镜头</th><th>切镜</th></tr></thead>
        <tbody>{Object.entries(audit.data.report.segment_shot_counts).map(([id,n])=><tr key={id}><td>{id}</td><td>{audit.data.report.segment_paragraph_counts[id]}</td><td>{String(n)}</td><td>{audit.data.report.segment_cut_counts[id]}</td></tr>)}</tbody></table>
        {audit.data.report.warnings.map((w:any)=><p key={w.id} className="small">节奏提醒 · {w.id} · {w.detail}</p>)}</>}
      <ErrorLine error={act.error}/></>}
  </details>;
}

// ---------------------------------------------------------------- 03 制作方案 (J09, J47, J72–J78)
function PlanView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "plan");
  const [tab, setTab] = useState("shots");
  const [shot, setShot] = useState<string | null>(null);
  const { hash } = useProjectRoute();
  const linkedShot = new URLSearchParams(hash.split("?")[1]).get("shot");
  useEffect(() => {
    const shots = current?.content.shots ?? [];
    if (linkedShot && shots.some((s:any)=>s.id === linkedShot)) {
      setTab("shots");
      setShot(linkedShot);
      document.getElementById("shot-"+ctx.eid+"-"+linkedShot)?.scrollIntoView({block:"nearest"});
    } else {
      setShot(prior=>shots.some((s:any)=>s.id===prior) ? prior : null);
    }
  }, [linkedShot, current?.id, ctx.eid]);
  const [trace, setTrace] = useState<any>(null);
  const act = useAction();
  const lay = useMemo(() => layout(current?.content), [current?.id]);
  const references = current?.id === art?.adopted_version_id ? adoptedReferences(ctx) : {};
  useEffect(() => {
    let active = true;
    setTrace(null);
    if (shot && current?.id && current.id === art?.adopted_version_id) {
      q("trace_shot", { episode_id: ctx.eid, shot_id: shot }).then((value) => {
        if (active) setTrace(value);
      }).catch(() => {});
    }
    return () => { active = false; };
  }, [shot, current?.id, art?.adopted_version_id, ctx.eid]);
  if (!art) return <div className="empty"><p>正式镜头表会引用已采用的剧本。</p><button className="btn" onClick={ctx.openTrial}>先试写本阶段</button></div>;
  if (!current) return <div><div className="stagehead"><h1>08 分镜</h1></div>
    <p className="muted">可在「创作对话」中查看标准提示词并起草本阶段稿件。</p></div>;
  const shots = current.content.shots ?? [];
  const scenes: string[] = [...new Set<string>(shots.map((s: any) => s.origin?.scene ?? "参考镜头"))];
  const segmentLimit = Number(ctx.view.kickoff.spec.segmentMaxSeconds ?? 15);
  const over = lay.segments.filter((s: any) => s.duration > segmentLimit * 1000);
  const belowTarget = ctx.view.kickoff.spec.targetDuration === "over_60" && lay.total <= 60000;
  return (
    <div>
      <div className="stagehead">
        <div><h1>08 分镜</h1><div className="muted small">{rno(current.display_no)} {current.id === art.adopted_version_id ? "已采用" : "草稿"} · {shots.length} 动作窗口 · {lay.segments.length} 段 · {Math.round(lay.total / 1000)} 秒 · 镜头时长加总等于段长</div></div>
        <div className="tabs">{[["shots", "镜头"], ["segments", "生成段"], ["acting", "表演与台词读秒"], ["table", "表格"]].map(([t, l]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div>
      </div>
      <VersionTabs ctx={ctx} art={art} current={current} />
      {belowTarget && <p className="card amber">本集目标严格超过 60 秒，当前设计 {lay.total / 1000} 秒。请在整集交付前调整镜头时长。</p>}
      {over.map((s: any) => (
        <div key={s.id} className="card amber" style={{ marginTop: 10 }}>
          <b>{s.id} 共 {s.duration / 1000} 秒，超过本集 {segmentLimit} 秒上限</b><span className="muted small"> · 须在动作完成处拆段，修复前阻断交付</span>
          <div className="row" style={{ marginTop: 8 }}>{s.shots.slice(1).map((sid: string) => (
            <button key={sid} className="btn" disabled={ctx.locked} onClick={() => act.run(async () => { const r = await ctx.write("split_segment", { version_id: current.id, segment_id: s.id, first_shot_of_second: sid }); await ctx.reload(); ctx.setFocus(r.versionId); })}>在 {sid} 前拆开</button>))}</div>
        </div>))}
      <fieldset className="edit-fields" disabled={ctx.locked}><RhythmReview version={current.id} save={async rhythmTable=>{
        const r=await ctx.write("save_manual",{artifact_id:art.id,base_version_id:current.id,content:{rhythmTable},note:"编辑镜头充分性联合表"});
        await ctx.reload(); ctx.setFocus(r.versionId);
      }}/></fieldset>
      {tab === "acting" && <PlanActing locked={ctx.locked} key={current.id} version={current} episodeId={ctx.eid} assets={ctx.assets} onSave={async shots=>{
        const r = await ctx.write("save_manual", {artifact_id:art.id, base_version_id:current.id, content:{shots}, note:"更新表演与台词时长"});
        await ctx.reload(); ctx.setFocus(r.versionId);
      }}/>}
      {tab === "shots" && scenes.map((sc) => (
        <div key={sc}>
          <h2 style={{ marginTop: 18 }}>{sc}</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(180px,1fr))", gap: 10 }}>
            {shots.filter((s: any) => (s.origin?.scene ?? "参考镜头") === sc).map((s: any) => (
              <div key={s.id} id={`shot-${ctx.eid}-${s.id}`} className={`shotcard ${shot === s.id ? "sel" : ""}`} onClick={() => { setShot(s.id); ctx.setSel({ label: `镜头 ${s.id}（${s.segment}）`, data: { shot: s.id, segment: s.segment, version: current.id } }); }}>
                <ShotPictures shot={s} assets={ctx.assets} refs={references[s.segment]?.refs ?? []} />
                <div className="body">
                  <div className="spread"><b>{s.id} <span className="muted small">{s.duration_ms / 1000}s</span></b><Tag tone="amber">{s.segment}</Tag></div>
                  <div className="muted tiny">{s.camera} · 全片 {fmtMs(lay.shots[s.id]?.start ?? 0)}–{fmtMs(lay.shots[s.id]?.end ?? 0)}</div>
                  <div className="small" style={{ marginTop: 4 }}>{s.action}</div>
                  {s.refs?.length > 0 && <div className="tiny" style={{ marginTop: 4 }}><Tag tone="violet">参考 {s.refs.map((r: any) => r.mode).join("/")}</Tag></div>}
                </div>
              </div>))}
          </div>
        </div>))}
      {tab === "segments" && <table className="table" style={{ marginTop: 12 }}><thead><tr><th>段</th><th>策略</th><th>全片时间</th><th>段长</th><th>镜头</th></tr></thead>
        <tbody>{lay.segments.map((s: any) => <tr key={s.id}><td>{s.id}</td><td><select disabled={ctx.locked} value={s.strategy} onChange={(e) => act.run(async () => {
          const r = await ctx.write("save_manual", { artifact_id: art.id, base_version_id: current.id, note: `${s.id} 改为${e.target.value === "grid" ? "九宫格" : "自由参考"}`,
            content: { ...current.content, segments: current.content.segments.map((g: any) => g.id === s.id ? { ...g, strategy: e.target.value } : g) } });
          await ctx.reload(); ctx.setFocus(r.versionId); })}><option value="flex">自由参考</option><option value="grid">九宫格</option></select></td><td>{fmtMs(s.start)}–{fmtMs(s.end)}</td><td>{s.duration / 1000}s</td><td>{s.shots.join("、")}</td></tr>)}</tbody></table>}
      {tab === "table" && <table className="table" style={{ marginTop: 12 }}><thead><tr><th>镜</th><th>段</th><th>段内</th><th>机位</th><th>动作</th><th>出处</th></tr></thead>
        <tbody>{shots.map((s: any) => { const t = lay.shots[s.id]; const seg = lay.segments.find((x: any) => x.id === s.segment);
          return <tr key={s.id}><td>{s.id}</td><td>{s.segment}</td><td>{((t.start - seg.start) / 1000).toFixed(1)}–{((t.end - seg.start) / 1000).toFixed(1)}s</td><td>{s.camera}</td><td>{s.action}</td><td className="muted">{s.refs?.length ? "参考镜头" : s.origin?.scene}</td></tr>; })}</tbody></table>}
      <div className="segstrip">{lay.segments.map((s: any) => (
        <div key={s.id} className={s.shots.includes(shot) ? "on" : ""} style={{ flex: s.duration }}>{s.id} · {Math.round(s.duration / 100) / 10}s</div>))}</div>
      {shot && <fieldset className="edit-fields" disabled={ctx.locked}><ShotEditor ctx={ctx} art={art} version={current} shotId={shot} onSaved={(vid: string) => ctx.setFocus(vid)} /></fieldset>}
      {trace && (
        <div className="card line" style={{ marginTop: 14 }}>
          <b>{trace.shot.id} 出处</b>
          {trace.refs.map((r: any, i: number) => <div key={i} className="small">原片 {r.source.name} · 参考包 v{r.packVersion} · {r.refShot} {fmtMs(r.start_ms)}–{fmtMs(r.end_ms)} · 用法 {r.mode} · {r.reason}</div>)}
          {trace.origin && <div className="small">原创镜：剧本 {trace.origin.scene}「{trace.origin.line ?? ""}」</div>}
        </div>)}
      <ErrorLine error={act.error} />
      <AdoptBar ctx={ctx} art={art} version={current} next="prompts" hint="分镜采用后，09 按本表编写提示词并检查本步结果" />
    </div>
  );
}

// ---------------------------------------------------------------- 04 空间锁定 (J39)
type SpaceLock = {id:string; label:string; relation:string; segments:string[]; sceneIds?:string[]; status:string; versionId:string|null; name:string|null;
  version:number|null; sha256:string|null; positions?:number[]};

function SpaceView({ ctx, scenesOnly=false }: { ctx: Ctx; scenesOnly?:boolean }) {
  const items = useQuery<SpaceLock[]>("space_locks", { episode_id: ctx.eid });
  const [form, setForm] = useState({ label: "", relation: "", source: "", depends: "" });
  const [rows, setRows] = useState<any[]>([]);
  const act = useAction();
  const load = async () => {
    const blocked = await q("blocked_segments", { episode_id: ctx.eid });
    setRows(Object.entries(blocked).map(([seg, rs]: any) => ({ seg, reasons: rs })));
  };
  useEffect(() => { load(); }, [ctx.eid, items.data]);
  const locks = items.data ?? [];
  return (
    <div>
      <div className="stagehead"><div><h1>空间与制作补充</h1><div className="muted small">逐条出处；未确认的用验证图，只阻塞依赖项</div></div></div>
      {items.data && !locks.some(l => l.status === "pending") && <div className="card"><span className="ok">没有待确认的空间关系</span>。</div>}
      {items.error && <p className="error">{items.error}</p>}
      {locks.map((t) => (
        <div key={t.id} className="card" style={{ marginBottom: 10 }}>
          <div className="row"><Tag tone={t.status==="pending"?"amber":"green"}>{t.status==="pending"?"待确认":"已确认"}</Tag><b>{t.label}</b> {(t.sceneIds?.length?t.sceneIds:t.segments).join("、")}</div>
          <p>{t.relation}</p>
          <div>{t.status==="confirmed" && (t.sha256 ? <span>{t.name} v{t.version} · <code>{t.sha256.slice(0,12)}</code></span> : "锁定图不可用，请重新选择")}</div>
          <div className="row" style={{ marginTop: 8 }}>
            <select key={t.versionId} defaultValue={t.versionId || undefined} aria-label={t.label+"的锁定图"} id={`lock-${t.id}`}>{ctx.assets.filter(a=>a.kind==="image").map((a) => <option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select>
            <button className="btn primary" onClick={() => act.run(async () => {
              const el = document.getElementById(`lock-${t.id}`) as HTMLSelectElement;
              await cmd("confirm_space_item", { item_id: t.id, lock_asset_version_id: el.value }); items.reload(); ctx.reload(); })}>用这张图确认锁定</button>
            <span className="muted small">确认后复核实际使用它的段</span>
          </div>
        </div>))}
      {rows.length > 0 && <div className="muted small" style={{ margin: "10px 0" }}>当前阻塞：{rows.map((r) => `${r.seg}（${r.reasons.join("；")}）`).join("、")}；其他段与身份图可以继续准备。</div>}
      <EpisodeSceneContact key={ctx.eid} eid={ctx.eid}/>
      <h2 style={{ marginTop: 20 }}>登记空间关系</h2>
      <div className="grid2">
        <input placeholder="地标 / 道具，例：桶的高度" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
        <input placeholder="相对位置，例：桶比柜台低一个爪子" value={form.relation} onChange={(e) => setForm({ ...form, relation: e.target.value })} />
        <input placeholder="出处，例：剧本阶段 2" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
        <input placeholder={scenesOnly?"适用场号，逗号分隔":"依赖段，逗号分隔，例：G04"} value={form.depends} onChange={(e) => setForm({ ...form, depends: e.target.value })} />
      </div>
      <button className="btn" style={{ marginTop: 10 }} onClick={() => act.run(async () => { await cmd("add_space_item", { episode_id: ctx.eid, label: form.label, relation: form.relation, source: form.source, scope:scenesOnly?"scenes":"segments", depends: form.depends.split(/[,，、\s]+/).filter(Boolean) }); items.reload(); })}>登记（待确认）</button>
      {!scenesOnly && <Supplements eid={ctx.eid} refresh={ctx.reload}/>}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 05 角色出镜 (J40)
function CastView({ ctx }: { ctx: Ctx }) {
  const m = useQuery<any>("cast_matrix", { episode_id: ctx.eid });
  const fixed = useQuery<any>("project_role_assets", {project_id:ctx.view.episode.project_id});
  const [role, setRole] = useState("");
  const [chosen,setChosen] = useState<Record<string,string>>({});
  const [observations,setObservations] = useState<Record<string,string>>({});
  const [positions,setPositions] = useState<Record<string,string>>({});
  const [openings,setOpenings] = useState<Record<string,boolean>>({});
  const [baselineDrafts, setBaselineDrafts] = useState<Record<string,{asset?:string; appearance?:string}>>({});
  const act = useAction();
  if (!m.data) return m.error ? <p className="error">{m.error}</p> : null;
  const roles: string[] = [...new Set<string>([...m.data.roles, ...(role.trim() ? [role.trim()] : [])])];
  const focused = role.trim() || roles[0] || "";
  const baseline = m.data.roster[focused] || {};
  const draft = baselineDrafts[focused] || {};
  const identityId = draft.asset ?? baseline.assetVersionId ?? "";
  const designArt=artifactFor(ctx.view,"asset_design");
  const design=designArt?.versions.find(v=>v.id===designArt.adopted_version_id)?.content.assets?.find(a=>a.roleId===focused)?.description;
  const standard=m.data.fixedStandards?.[focused.toLowerCase()]?.text;
  const appearance=standard ?? draft.appearance ?? baseline.appearance ?? design ?? "";
  const editBaseline = (change: {asset?:string; appearance?:string}) =>
    setBaselineDrafts(previous=>({...previous,[focused]:{...previous[focused],...change}}));
  const set = (seg: string, r: string, status: string) => act.run(async () => {
    const cur = m.data.rows.find((x: any) => x.segment === seg)?.cells[r];
    const key = seg+"|"+r, asset = chosen[key] ?? cur?.assetVersionId;
    const selected = ctx.assets.find((a:any)=>a.id===asset);
    const isGrid = selected?.grid;
    const same = (cur?.assetVersionId || "")===(asset || "");
    const assetKey=key+"|"+(asset || "");
    const raw = positions[assetKey] ?? (same ? cur?.cells ?? [] : []).join(",");
    await cmd("set_cast", { episode_id: ctx.eid, segment_id: seg, role: r, status, asset_version_id: status === "absent" ? null : asset || null,
      cells: status === "absent" || !isGrid ? [] : raw.trim().split(/[,，、\s]+/).filter(Boolean).map(Number),
      note: observations[assetKey] ?? (same && (status!=="absent" || cur?.status==="absent") ? cur?.note ?? "" : ""), opening_confirmed:status==="ok" && ["state","grid"].includes(selected?.category) &&
        (openings[key+"|"+asset] ?? (cur?.assetVersionId===asset && cur?.depictsMoment?.segment===seg)) });
    setChosen(old=>({...old,[key]:status==="absent"?"":asset || ""}));
    m.reload(); ctx.reload();
  });
  return (
    <div>
      <div className="stagehead"><div><h1>角色出镜与身份覆盖</h1><div className="muted small">逐段绑定。使用九宫格时交付本段的一张整图，缺图时可修格或改用自由参考。</div></div>
        <div className="row"><input placeholder="新增角色 ID" value={role} onChange={(e) => setRole(e.target.value)} style={{ width: 140 }} /></div></div>
      {fixed.error && <p className="error">{fixed.error}</p>}
      {fixed.data?.roles?.length > 0 && <div className="row"><span>本项目固定身份图</span>{fixed.data.roles.map((r:any)=><button key={r.id} className="btn"
        title={r.path+" · "+r.sha256} disabled={act.busy} onClick={()=>act.run(async()=>{
          await cmd("use_fixed_role",{episode_id:ctx.eid,role:r.id});ctx.reload();setRole(r.id);
        })}>登记 {r.name} {r.version}</button>)}</div>}
      {focused && <div className="panel col" style={{padding:14,marginBottom:14}}>
        <h2>本集角色设定</h2><div className="row"><label>角色 <select aria-label="角色" value={focused} onChange={e=>setRole(e.target.value)}>{roles.map(r=><option key={r}>{r}</option>)}</select></label>
        {baseline.identityRequired != null && <span>{baseline.identityRequired?"故事稿要求独立身份基准":"故事稿标记为无需独立身份基准"}</span>}</div>
        {baseline.description && <p>{baseline.description}</p>}
        <div className="row"><label>独立身份原图 <select aria-label="独立身份原图" value={identityId} onChange={e=>editBaseline({asset:e.target.value})}>
          <option value="">选择身份原图版本</option>{ctx.assets.filter(a=>a.category==="identity" && a.kind==="image" && !a.grid).map(a=><option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select></label>
        <label className="btn">上传身份原图<input hidden type="file" accept="image/png,image/jpeg" onChange={e=>{
          const file=e.target.files?.[0]; if(file)act.run(async()=>{const result=await upload("asset",file,{projectId:ctx.view.episode.project_id,episodeId:ctx.eid,category:"identity"});
          editBaseline({asset:result.versionId});ctx.reload();});}} /></label>
        {identityId && <img src={blob(ctx.assets.find(a=>a.id===identityId)?.sha256)} alt={focused+" 独立身份原图"} style={{maxWidth:96,maxHeight:96,objectFit:"contain"}}/>}</div>
        {standard ? <div><p>项目角色外观标准（只读） · {m.data.fixedStandards[focused.toLowerCase()].detail}</p><p>提示词引用 · {standard}</p></div> : <><label>外观短描述 <input value={appearance} onChange={e=>editBaseline({appearance:e.target.value})} /></label>
          <small>请对照图片核对，改成和图一致。</small>{baseline.appearance && design && baseline.appearance!==design && <details><summary>与素材设计不同，以此处为准</summary><p>{design}</p></details>}</>}
        <div><button className="btn" disabled={act.busy || !identityId || !appearance.trim()} onClick={()=>act.run(async()=>{
          await cmd("set_role_baseline",{episode_id:ctx.eid,role:focused,asset_version_id:identityId,appearance});
          m.reload();ctx.reload();
        })}>保存本集身份基准</button></div></div>}
      <table className="table"><thead><tr><th>段</th><th>策略</th>{roles.map((r) => <th key={r}>{r}</th>)}</tr></thead>
        <tbody>{m.data.rows.map((row: any) => (
          <tr key={row.segment}><td><b>{row.segment}</b></td><td>{row.strategy==="grid"?"九宫格":"自由参考"}</td>
            {roles.map((r) => { const c = row.cells[r];
              const selected=ctx.assets.find(a=>a.id===(chosen[row.segment+"|"+r] ?? c?.assetVersionId));
              const observationKey=row.segment+"|"+r+"|"+(selected?.id || "");
              const same = (c?.assetVersionId || "")===(selected?.id || "");
              const registered = same && c?.depictsMoment?.segment===row.segment;
              return (
              <td key={r}><div className="col">{c && <Tag tone={c.status === "ok" ? "green" : "amber"}>{c.status === "ok" ? "身份清楚" : c.status === "unclear" ? "看不清" : "未出镜"}</Tag>}
                <select aria-label={row.segment+" "+r+" 身份图"} value={chosen[row.segment+"|"+r] ?? c?.assetVersionId ?? ""} onChange={e=>setChosen({...chosen,[row.segment+"|"+r]:e.target.value})}>
                  <option value="">选择本段身份图版本</option>{ctx.assets.filter(a=>a.kind==="image").map(a=><option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select>
                {selected?.grid ? <label>格位 <input placeholder="例如 1、3、9" value={positions[observationKey] ?? (same ? c?.cells ?? [] : []).join("、")} onChange={e=>setPositions({...positions,[observationKey]:e.target.value})}/></label> : null}
                {registered ? <span>已核对本段开场状态</span> : selected && ["state","grid"].includes(selected.category) && <label><input type="checkbox"
                  checked={openings[observationKey] ?? false}
                  onChange={e=>setOpenings({...openings,[observationKey]:e.target.checked})}/> 已核对图中角色和本段开场状态</label>}
                <label>对照观察 <input value={observations[observationKey] ?? (same ? c?.note ?? "" : "")} onChange={e=>setObservations({...observations,[observationKey]:e.target.value})}/></label>
                <div className="row">{["ok","unclear","absent"].map(st=><button key={st} className="btn ghost" onClick={()=>set(row.segment,r,st)}>{st==="ok"?"身份清楚":st==="unclear"?"看不清":"未出镜"}</button>)}</div>
                {c?.options?.map((o:string)=><a key={o} href={o.includes("flex") ? `#/e/${ctx.eid}/plan` : `#/e/${ctx.eid}/assets`}>{o==="改用 flex" ? "改用自由参考" : o}</a>)}</div></td>); })}
          </tr>))}</tbody></table>
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 08 素材与选图 (J10, J14, J15, J18, J27, J41)
type ImageReference = {assetVersionId: string; label: string; kind: string; shots?: string[]};
const frameFields = [["startAssetId", "起幅"], ["endAssetId", "落幅"]] as const;

function ShotPictures({shot, assets, refs}: {shot: any; assets: any[]; refs: ImageReference[]}) {
  const pictures: {id:string; caption:string; url:string}[] = [];
  const add = (id:string, caption:string) => {
    const asset = assets.find(a => a.id === id && a.kind === "image");
    if (asset?.sha256) pictures.push({id, caption: caption+" · "+asset.name+" v"+asset.version_no, url:blob(asset.sha256)!});
  };
  for (const [field, label] of frameFields) if (shot[field]) add(shot[field], label);
  for (const ref of refs) {
    if (ref.kind !== "image" || (ref.shots?.length && !ref.shots.includes(shot.id))) continue;
    if (!pictures.some(p => p.id === ref.assetVersionId)) add(ref.assetVersionId, ref.shots?.length ? "本镜引用" : "本段引用");
  }
  return <div className="pic shot-pictures">{pictures.length ? pictures.map(p =>
    <figure key={p.caption+p.id}><img src={p.url} alt={p.caption} loading="lazy"/><figcaption title={p.caption}>{p.caption}</figcaption></figure>
  ) : <span className="muted tiny">尚无本镜或本段参考图</span>}</div>;
}

function adoptedReferences(ctx:Ctx): Record<string,{refs:ImageReference[]}> {
  const prompts=artifactFor(ctx.view,"prompts");
  return prompts?.versions.find((v:any)=>v.id===prompts.adopted_version_id)?.content?.segments ?? {};
}

function AssetsView({ ctx }: { ctx: Ctx }) {
  const { hash, go } = useProjectRoute();
  const requestedTab = new URLSearchParams(hash.split("?")[1]).get("tab") ?? "round";
  const tab=requestedTab==="needs" && !artifactFor(ctx.view,"plan")?.adopted_version_id?"round":requestedTab;
  const setTab = (t:string) => go(`#/e/${ctx.eid}/assets${t==="round"?"":"?tab="+encodeURIComponent(t)}`);
  const [mode, setMode] = useState("原图");
  const [pair,setPair]=useState(["",""]);
  const [required,setRequired] = useState<any>(null);
  const [spotlight,setSpotlight]=useState("");
  const [detail, setDetail] = useState<any>(null);
  const [newBatch, setNewBatch] = useState<any>(null);
  const [adoptPre, setAdoptPre] = useState<any>(null);
  const [upKind, setUpKind] = useState("state");
  const providers = useQuery<any>("image_providers");
  const channel = useQuery<any>("episode_image_channel",{episode_id:ctx.eid});
  const act = useAction();
  const cleanedImages = ctx.assets.filter((a) => a.kind === "image" && a.cleanedAt);
  const images = ctx.assets.filter((a) => a.kind === "image" && !a.cleanedAt).sort((a, b) => (b.grid ? 1 : 0) - (a.grid ? 1 : 0));
  const params = new URLSearchParams(hash.split("?")[1]);
  const editVersion = params.get("editImage");
  const libraryEntry = params.get("libraryEntry");
  const editBase = editVersion && images.find(a=>a.id===editVersion && !a.grid);
  const libraryBatch = editBase && libraryEntry ? {operation:"edit",base:editBase,libraryEntry} : null;
  function closeBatchForm() {
    setNewBatch(null);
    if (editVersion) { params.delete("editImage"); params.delete("libraryEntry"); go(hash.split("?")[0]+(params.size?"?"+params:"")); }
    void ctx.reload();
  }
  const marked = (a: any, m: string) => a.marks.includes(m);
  const sourceBatches=new Map<string,any>(ctx.batches.flatMap(b=>b.requests.filter((r:any)=>r.asset_version_id).map((r:any)=>[r.asset_version_id,{batch:b, request:r}])));
  const filter = tab === "round" ? images.filter((a) => !marked(a, "backup")) : tab === "backup" ? images.filter((a) => !marked(a, "adopted") && !marked(a, "required")) : images;
  const references=adoptedReferences(ctx);
  const cited=new Set(Object.values(references).flatMap(s=>s.refs.filter(r=>r.kind==="image").map(r=>r.assetVersionId)));
  const active=filter.find(a=>a.id===spotlight);
  const left=filter.find(a=>a.id===pair[0]) ?? filter[0];
  const right=filter.find(a=>a.id===pair[1] && a.id!==left?.id) ?? filter.find(a=>a.id!==left?.id);
  const shown=mode==="并排"?[left,right].filter(Boolean)
    :(active?[active,...filter.filter(a=>a.id!==spotlight)]:filter).slice(0,2);
  function chooseSide(side:number,id:string) {
    const otherSide=shown[1-side]?.id;
    const other=id===otherSide ? shown[side]?.id : otherSide;
    setPair(side===0 ? [id,other??""] : [other??"",id]);
  }
  const remaining=filter.filter(a=>!shown.some(s=>s.id===a.id));
  async function previewImage(a:any) {
    const r = await cmd("adopt_image", {episode_id:ctx.eid,version_id:a.id});
    setAdoptPre({id:a.id,name:a.name,version:a.version_no,refs:r.references,fingerprint:r.fingerprint});
  }
  const imgStyle = mode === "并排" ? {height:"min(32vh, 260px)",objectFit:"contain" as const} : mode === "灰度" ? { filter: "grayscale(1)" } : mode === "手机尺寸" ? {width:180,aspectRatio:"9/16",objectFit:"contain" as const,background:"#08090b",margin:"auto",border:"3px solid #494b54",borderRadius:16} : {};
  return (
    <div className="asset-stage">
      {!!cleanedImages.length && <details className="small"><summary>已清理的中间图片 · {cleanedImages.length} 张</summary>{cleanedImages.map(a=><p key={a.id}>{a.name} v{a.version_no} · {new Date(a.cleanedAt*1000).toLocaleString()} · 制作记录保留</p>)}</details>}
      <div className="stagehead">
        <div><h1>素材与选图</h1><div className="muted small">{images.length} 张 · 提示词已引用 {cited.size} 张 · 手动已采用 {images.filter((a) => marked(a, "adopted")).length} · 本集图像批次 {ctx.batches.length}</div></div>
        <div className="row"><div className="tabs">{[["needs", "需求"], ["round", "本轮出图"], ["backup", "补选区"], ["canvas", "画布"]].filter(([t])=>t!=="needs" || artifactFor(ctx.view,"plan")?.adopted_version_id).map(([t, l]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div>
          <button className="btn primary" onClick={() => setNewBatch({ operation: "generate" })}>出图</button>
          <select value={upKind} onChange={(e) => setUpKind(e.target.value)} style={{ padding: "4px 6px" }}><option value="state">场面 / 状态图</option><option value="grid">九宫格</option><option value="identity">身份图</option><option value="costume">服装参考</option><option value="scene">空景</option></select>
          <label className="btn">上传<input type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && act.run(async () => { await upload("asset", e.target.files![0], { projectId: ctx.view.episode.project_id, episodeId: ctx.eid, category: upKind, grid: upKind === "grid" }); ctx.reload(); })} /></label></div>
      </div>
      <nav className="row" aria-label="素材制作工具">
        <a className="btn" href={`#/e/${ctx.eid}/cast`}>角色出镜与身份覆盖</a>
        <a className="btn" href={`#/e/${ctx.eid}/worksheet?group=scene`}>场景空间与锁定图</a>
      </nav>
      <ProjectLibrary pid={ctx.view.episode.project_id} eid={ctx.eid} productsOnly refresh={ctx.reload} images={images} shots={artifactFor(ctx.view,"plan")?.versions.find((v:any)=>v.id===artifactFor(ctx.view,"plan")?.adopted_version_id)?.content.shots ?? []}/>
      {tab === "canvas" ? channel.data && <Canvas defaultProvider={channel.data.provider} key={ctx.eid} ctx={ctx} providers={providers.data ?? {}} openBatch={ctx.openBatch} /> : tab === "needs" ? <NeedsTable ctx={ctx} /> : (
        <div className="candidate-panel"><div className={"candidate-layout"+(shown.length<2?" single":"")}>
          {shown.map((a,i) => (
            <div key={i}>
              {mode==="并排" && <select aria-label={i?"右侧比较图":"左侧比较图"} value={a.id} onChange={e=>chooseSide(i,e.target.value)}>{filter.map(x=><option key={x.id} value={x.id}>{x.name} v{x.version_no}</option>)}</select>}
              {cited.has(a.id) && <p>已引用 · {Object.entries(references).filter(([,s])=>s.refs.some(r=>r.assetVersionId===a.id)).map(([sid])=>sid).join("、")}</p>}
              <PaletteTrace cards={sourceBatches.get(a.id)?.batch.spec.palettes} label="此图生成时参考"/>
              <button className="imgbox candidate-preview" aria-label={`放大 ${a.name} v${a.version_no}`} onClick={() => setDetail(a)}><img src={blob(a.sha256)} style={imgStyle} alt=""/></button>
              <div className="spread" style={{ marginTop: 6 }}><b>{a.name} · 候选 {String.fromCharCode(65 + i)} <span className="muted small">v{a.version_no}</span></b>
                <div className="row">{marked(a,"required") && <Tag tone="green">必用 · 你选定</Tag>}{marked(a,"recommended") && <Tag>推荐</Tag>}{marked(a,"backup") && <Tag>补选区</Tag>}</div></div>
              {sourceBatches.has(a.id) && (() => {
                const {batch, request} = sourceBatches.get(a.id);
                return <details className="candidate-receipt"><summary>本图生成输入</summary>
                  <div className="small">请求 {request.local_request_id}{request.replaces_id && "（补发）"}</div>
                  <OrderedImageInputs inputs={batch.inputGroups?.[request.sourceOrdinal - 1]}/>
                  <button className="btn" onClick={() => ctx.openBatch(batch)}>查看冻结批次</button>
                </details>;
              })()}
              <div className="row" style={{ marginTop: 6 }}>
                <button className="btn ghost" onClick={()=>setRequired(a)}>{marked(a,"required")?"编辑必用要求":"标必用"}</button>
                {["recommended", "backup"].map((m) => <button key={m} className="btn ghost" onClick={() => act.run(async () => { await cmd("mark_image", { episode_id: ctx.eid, version_id:a.id, mark:m,enabled:!marked(a,m) }); ctx.reload(); })}>{marked(a,m)?"取消":""}{m === "recommended" ? "推荐" : "补选"}</button>)}
                {a.kind === "image" && <button className="btn" onClick={() => setNewBatch({ operation: "edit", base: a })}>{a.grid ? "修一格" : "带图修改"}</button>}
                <button className="btn" onClick={() => act.run(()=>previewImage(a))}>采用</button>
              </div>
            </div>))}
          <div className="col">
            <span className="muted small">查看方式</span>
            {["原图", "灰度", "手机尺寸", "并排"].map((m) => <button key={m} className={`pill ${mode === m ? "on" : ""}`} onClick={() => setMode(m)}>{m}</button>)}
            {remaining.length>0&&<span className="muted small" style={{ marginTop: 10 }}>其余候选</span>}
            {remaining.map(a=><button className="director-open" key={a.id} aria-label={"查看 "+a.name+" v"+a.version_no}
              onClick={()=>{setSpotlight(a.id);if(mode==="并排")chooseSide(0,a.id);}}>
              <img src={blob(a.sha256)} alt="" loading="lazy" style={{width:"100%",borderRadius:6}}/>{a.name} v{a.version_no}</button>)}
          </div>
        </div></div>)}
      {adoptPre && <div className="card amber" style={{ marginTop: 12 }}><b>采用新图前的影响</b>
        <p>{adoptPre.name} · v{adoptPre.version}</p>
        {!adoptPre.refs.length && <p className="small">当前采用的提示词没有引用这张图的任何版本。</p>}
        {adoptPre.refs.map((r:any)=><div className="card" key={r.segment+"-"+r.label}>
          <b>{r.segment} · {r.label} · 当前引用 v{r.imageVersion}</b>
          <div>{r.shots.length?"作用镜头 "+r.shots.join("、"):"本段未登记逐镜范围"}{r.purpose && " · "+r.purpose}</div>
          <details><summary>查看 {r.segment} 采用的 TXT · v{r.promptVersion}</summary><pre style={{whiteSpace:"pre-wrap"}}>{r.txt}</pre></details>
          {r.current!==adoptPre.id && <small>采用后此段需更新引用并复审</small>}
        </div>)}
        <div className="row"><button className="btn primary" disabled={act.busy} onClick={() => act.run(async () => {
          await cmd("adopt_image", {episode_id:ctx.eid,version_id:adoptPre.id,confirm:true,expected:adoptPre.fingerprint});
          setAdoptPre(null); ctx.reload();
        })}>确认采用</button><button className="btn" disabled={act.busy} onClick={()=>act.run(()=>previewImage({id:adoptPre.id,name:adoptPre.name,version_no:adoptPre.version}))}>重新核对引用</button>
        <button className="btn" onClick={() => setAdoptPre(null)}>取消</button></div></div>}
      {required && <RequiredImage ctx={ctx} asset={required} close={()=>setRequired(null)}/>}
      {detail && <Modal width={820} onClose={()=>setDetail(null)}><div className="spread">
        <h2>{detail.name} · v{detail.version_no}</h2><div className="row"><a href={blob(detail.sha256)} target="_blank" rel="noreferrer">打开原文件</a>
          <button className="btn" onClick={()=>setDetail(null)}>关闭原图</button></div>
        </div><OriginalZoom name={detail.name} sha={detail.sha256}/>{!!detail.grid && <GridDetail key={detail.id} a={detail} ctx={ctx}/>}</Modal>}
      {ctx.batches.length > 0 && <h2 style={{ marginTop: 24 }}>图像批次</h2>}
      {ctx.batches.map((b) => (
        <div key={b.id} className="card line" style={{ marginBottom: 8 }}>
          <div className="spread"><span>{b.provider} · {b.operation === "edit" ? "局部重画" : "生成"} · {b.count} 张 · {b.size} · {BATCH_LABEL[b.state] ?? b.state}</span><button className="btn" onClick={() => ctx.openBatch(b)}>打开</button></div>
          <div className="row small" style={{ marginTop: 6 }}>{b.requests.map((r: any) => <Tag key={r.id} tone={r.state === "succeeded" ? "green" : r.state === "unknown" ? "amber" : r.state === "failed" ? "red" : ""}>#{r.ordinal} {REQ[r.state] ?? r.state}</Tag>)}</div>
        </div>))}
      {(libraryBatch || newBatch) && providers.data && channel.data && <NewBatch defaultProvider={channel.data.provider} key={editVersion || "new"} ctx={ctx} init={libraryBatch || newBatch} providers={providers.data} close={closeBatchForm}/>}
      {editVersion && !editBase && <p className="muted">正在查找本集的底图版本。<button className="btn" onClick={ctx.reload}>刷新素材</button><button className="btn" onClick={closeBatchForm}>返回选图</button></p>}
      <ErrorLine error={act.error} />
    </div>
  );
}

const DEC: Record<string, string> = { pass: "通过", return: "退回", uncertain: "待判断" };
const REQ: Record<string, string> = { pending: "待发", submitted: "已发送", succeeded: "已返回", unknown: "待核对", failed: "失败", confirmed_not_executed: "确认未执行" };

function NeedsTable({ ctx }: { ctx: Ctx }) {
  const plan = artifactFor(ctx.view, "plan");
  const content = plan?.versions.find((v: any) => v.id === plan.adopted_version_id)?.content;
  const lay = layout(content);
  const refs=adoptedReferences(ctx);
  const cast = useQuery<any>("cast_matrix", {episode_id:ctx.eid});
  const images = new Map(ctx.assets.filter(a => a.kind === "image").map(a => [a.id, a]));
  return <><table className="table"><thead><tr><th>段</th><th>策略</th><th>镜号</th><th>素材用途</th><th>图片引用</th><th>角色身份覆盖</th></tr></thead>
    <tbody>{lay.segments.map((s: any) => {
      const used = (refs[s.id]?.refs ?? []).filter(r => r.kind === "image");
      const bound = new Set(used.filter(r => images.has(r.assetVersionId)).map(r => r.assetVersionId));
      const cells = cast.data?.rows.find((row:any) => row.segment === s.id)?.cells ?? {};
      return <tr key={s.id}><td>{s.id}</td><td>{s.strategy==="grid"?"九宫格":"自由参考"}</td><td>{s.shots.join("、")}</td>
        <td>{s.strategy === "grid" ? "本段九宫格" : "角色 / 场景 / 道具参考"}</td>
        <td>{bound.size > 0 && <Tag tone="green">已引用 {bound.size} 张</Tag>}
          {(!used.length || used.some(r => !bound.has(r.assetVersionId))) && <Tag tone="amber">待绑定参考</Tag>}</td>
        <td>{!cast.data ? cast.error || "读取中…" : cast.data.roles.length === 0 ? "未登记角色" : cast.data.roles.map((role:string) => {
          const cell = cells[role];
          const name = images.get(cell?.assetVersionId)?.name;
          let state = "待核对出镜";
          if (cell?.status === "absent") state = "未出镜";
          if (cell?.status === "unclear") state = "身份看不清";
          if (cell?.status === "ok") state = !name ? "待绑定身份图" : bound.has(cell.assetVersionId) ? "已引用 "+name : "已绑定 "+name+"，提示词未引用";
          return <div key={role}>{role} · {state}</div>;
        })}</td></tr>;
    })}</tbody></table>
    {lay.segments.some((s:any)=>s.strategy==="grid") && <GridMethods eid={ctx.eid}/>}</>;
}

function GridMethods({eid}:{eid:string}) {
  const content = useQuery<any>("stage_methods", {episode_id:eid,stage:"grid_examples"});
  if (content.error) return <p role="alert" className="error">{content.error} <button className="btn" onClick={content.reload}>重新读取九格参考</button></p>;
  if (!content.data?.sections.length) return null;
  return <details className="card"><summary>九格状态与镜头示意</summary>
    {content.data.sections.map((entry:any)=><details key={entry.name}><summary>{entry.name.replace(/\.md$/,"")}</summary>
      <p className="muted tiny">{content.data.ref} · {entry.methodSource.source.path} · 行 {entry.methodSource.source.lines}</p>
      <pre style={{whiteSpace:"pre-wrap"}}>{entry.text}</pre></details>)}</details>;
}

type GridBinding = {cell:number; plan_version_id:string|null; shot_id:string|null; offset_ms:number|null; revision:number};

function GridDetail({ a, ctx }: { a:any; ctx:Ctx }) {
  const bindings = useQuery<GridBinding[]>("grid_bindings", {episode_id:ctx.eid,version_id:a.id});
  const art = artifactFor(ctx.view,"plan");
  const plans = art?.versions ?? [];
  const adopted = plans.find((v:any)=>v.id === art.adopted_version_id);
  const [editing,setEditing] = useState<{cell:number; revision:number}|null>(null);
  const [shotId,setShotId] = useState("");
  const [seconds,setSeconds] = useState("");
  const act = useAction();
  const shot = adopted?.content.shots.find((s:any)=>s.id === shotId);
  function edit(cell:number, binding?:GridBinding) {
    const keep = !!adopted && adopted.id === binding?.plan_version_id;
    setEditing({cell,revision:binding?.revision ?? 0});
    setShotId(keep ? binding!.shot_id ?? "" : "");
    setSeconds(keep ? String((binding!.offset_ms ?? 0)/1000) : "0");
    act.clear();
  }
  async function save(clear=false) {
    if (!editing) return;
    await cmd("set_grid_cell", {episode_id:ctx.eid,version_id:a.id,cell:editing.cell,expected_revision:editing.revision,
      plan_version_id:clear ? null : adopted.id,shot_id:clear ? null : shotId,
      offset_ms:clear ? null : Math.round(Number(seconds)*1000)});
    await bindings.reload();
    setEditing(null);
  }
  return <div className="card line" style={{marginTop:14}}>
    <b>九宫格详情 · {a.name} v{a.version_no}</b>{a.changed_cells && <span className="muted small"> · 请求修改第 {a.changed_cells.join("、")} 格</span>}
    <GridMethods eid={ctx.eid}/>
    {a.source_version_id && <GridComparison key={a.id} id={a.id}/>}
    {bindings.error && <p className="error">{bindings.error} <button className="btn" onClick={bindings.reload}>重新读取</button></p>}
    <div style={{display:"grid",gridTemplateColumns:"repeat(3,minmax(0,1fr))",gap:6,marginTop:10}}>
      {Array.from({length:9},(_,i)=>{
        const cell=i+1;
        const meta=Array.isArray(a.cells) ? a.cells.find((c:any)=>c?.cell===cell) : null;
        const binding=bindings.data?.find(b=>b.cell===cell);
        const plan=plans.find((v:any)=>v.id===binding?.plan_version_id);
        const sh=plan?.content.shots.find((s:any)=>s.id===binding?.shot_id);
        const lay=sh && layout(plan.content);
        const segment=sh && lay.segments.find((s:any)=>s.id===sh.segment);
        const time=segment && lay.shots[sh.id].start-segment.start+(binding?.offset_ms ?? 0);
        return <div key={cell} className={`card ${a.changed_cells?.includes(cell) ? "amber" : "line"}`} style={{padding:8}}>
          <div role="img" aria-label={`第 ${cell} 格`} style={{aspectRatio:`${a.width||1}/${a.height||1}`,backgroundImage:`url(${blob(a.sha256)})`,backgroundSize:"300% 300%",backgroundPosition:`${i%3*50}% ${Math.floor(i/3)*50}%`}}/>
          <b>第 {cell} 格</b>{typeof meta?.moment==="string" && <div className="muted tiny">{meta.moment}</div>}
          {segment ? <div className="small"><a href={`#/e/${ctx.eid}/plan?${new URLSearchParams({v:plan.id,shot:sh.id})}`}>{rno(plan.display_no)} · {sh.segment} · {sh.id}</a>
            <div>段内 {(time/1000).toFixed(3)} 秒</div></div> : <div className="muted tiny">{bindings.data ? "未关联镜头" : "正在读取镜头"}</div>}
          {bindings.data && (adopted || binding?.shot_id) && <button className="btn" aria-label={`设置第 ${cell} 格镜头与时刻`} onClick={()=>edit(cell,binding)}>设置镜头与时刻</button>}
        </div>;
      })}
    </div>
    {!adopted && <p className="muted small">采用本集制作方案后，即可给每格关联镜头与时刻。</p>}
    {editing && <form className="card" onSubmit={e=>{e.preventDefault();void act.run(()=>save());}}>
      <b>第 {editing.cell} 格镜头与时刻</b><div className="row">
        <label>制作方案 {adopted && rno(adopted.display_no)} · 镜头 <select required value={shotId} onChange={e=>{setShotId(e.target.value);setSeconds("0");}}>
          <option value="">选择镜头</option>{adopted?.content.shots.map((s:any)=><option key={s.id} value={s.id}>{s.segment} · {s.id} · {s.action}</option>)}
        </select></label>
        <label>距镜头开始（秒） <input type="number" min="0" step="0.001" max={shot ? shot.duration_ms/1000 : undefined} required value={seconds} onChange={e=>setSeconds(e.target.value)}/></label>
      </div><div className="row"><button className="btn primary" disabled={!shot || act.busy}>保存关联</button>
        {bindings.data?.some(b=>b.cell===editing.cell && b.shot_id) && <button type="button" className="btn" disabled={act.busy} onClick={()=>act.run(()=>save(true))}>解除关联</button>}
        <button type="button" className="btn" onClick={()=>setEditing(null)}>取消</button></div>
      <ErrorLine error={act.error}/>{act.error && <button type="button" className="btn" onClick={async()=>{await Promise.all([ctx.reload(),bindings.reload()]);setEditing(null);}}>刷新镜头与关联</button>}
    </form>}
  </div>;
}

function GridComparison({ id }: { id: string }) {
  const cmp = useQuery<any>("compare_cells", { new_version_id: id });
  if (!cmp.data) return <ErrorLine error={cmp.error ? { message: cmp.error } : null} />;
  if (!cmp.data.comparable) return <p className="small">底图与返回图无法按相同尺寸逐格比较。</p>;
  return <p className="small">像素有变化的格：{cmp.data.changed.join("、") || "无"} · 像素完全一致的格：{cmp.data.unchanged.join("、") || "无"}</p>;
}

function NewBatch({ ctx, init, providers, close, defaultProvider }: { defaultProvider:string; ctx: Ctx; init: any; providers: any; close: () => void }) {
  const names = Object.keys(providers).filter(name=>providers[name].capabilities.operations.includes(init.operation));
  const [provider, setProvider] = useState(names.includes(defaultProvider)?defaultProvider:names[0] ?? "");
  const [prompt, setPrompt] = useState(init.base?.grid ? `局部重画 ${init.base.name}` : "");
  const [assetName, setAssetName] = useState("");
  const [category, setCategory] = useState("state");
  const [count, setCount] = useState(1);
  const [size, setSize] = useState("");
  const [quality, setQuality] = useState("low");
  const [model, setModel] = useState("");
  const [cells, setCells] = useState<number[]>(init.base ? [5] : []);
  const [ref, setRef] = useState("");
  const [segs, setSegs] = useState("");
  const locks = useQuery<SpaceLock[]>("space_locks", {episode_id:ctx.eid});
  const scoped = segs.split(/[,，、\s]+/).filter(Boolean);
  const relevant = (locks.data || []).filter(l => l.status==="confirmed" && l.versionId && l.segments.some(s=>scoped.includes(s)));
  const [chosen,setChosen] = useState<string[]>([]);
  const ordered = chosen.filter(id=>relevant.some(l=>l.versionId===id));
  const act = useAction();
  const caps = providers[provider]?.capabilities;
  const completedImages=ctx.batches.filter(b=>b.provider===provider).flatMap(b=>b.requests).filter(r=>r?.state==="succeeded" && r.asset_version_id).length;
  useEffect(() => {
    if (!caps) return;
    setSize(value=>caps.sizes.includes(value) ? value : caps.sizes[0]);
    setQuality(value=>caps.quality.includes(value) ? value : caps.quality[0]);
    setModel(value=>caps.models?.includes(value) ? value : caps.models?.[0] || "");
  }, [provider,caps]);
  const create = () => act.run(async () => {
    const args = {episode_id:ctx.eid,provider,size,quality,...(model?{model}:{})};
    const extraInputs = Array.from(new Set([ref,...ordered].filter(id=>id && id!==init.base?.id)));
    let r;
    if (init.base?.grid) {
      r=await cmd("redraw_cells",{...args,base_version_id:init.base.id,cells,reference_version_ids:extraInputs,segments:scoped,prompt});
    } else if (init.base) {
      r=await cmd("create_batch",{...args,operation:"edit",items:[{prompt,segments:scoped,inputs:[init.base.id,...extraInputs],
        libraryEntryId:init.libraryEntry,target:{name:init.base.name,category:init.base.category,assetId:init.base.asset_id,roles:init.base.roles}}]});
    } else {
      r=await cmd("create_batch",{...args,operation:"generate",items:Array.from({length:count},(_,i)=>({
        prompt:category==="grid" ? "将画面等分为三行三列，共九格，按从左到右、从上到下排列。\n"+prompt : prompt,segments:scoped,inputs:ordered,target:{name:`${assetName.trim() || prompt.slice(0,10)}${count > 1 ? " " + (i+1) : ""}`,category,...(category==="grid"?{grid:true}:{})}}))});
    }
    ctx.openBatch(await q("batch_view", { batch_id: r.batchId }));
    close();
  });
  return (
    <Modal onClose={close} width={620}><div className="image-batch-form">
      <div className="row"><h2 style={{ margin: 0 }}>{init.base?.grid ? `局部重画 ${init.base.name}` : init.base ? `垫图改图 · ${init.base.name} v${init.base.version_no}` : "发起图像批次"}</h2></div>
      <label className="formrow"><span>出图工具</span><select value={provider} onChange={e=>setProvider(e.target.value)}>{names.map(n=><option key={n} value={n}>{providers[n].capabilities.label || (n==="openai-image-mcp"?"OpenAI Image MCP":n)}</option>)}</select></label>
      {provider && <p className="muted small batch-history">{providers[provider]?.verified==="simulated" ? "模拟通道" : completedImages ? `此工具已为本集生成 ${completedImages} 张图片` : "此工具在本集暂无成功出图记录"}</p>}
      {caps?.models?.length>0 && <>
        <label className="formrow"><span>图像任务创作型号</span><select value={model} onChange={e=>setModel(e.target.value)}>{caps.models.map((value:string)=><option key={value}>{value}</option>)}</select></label>
        {caps.effort && <label className="formrow"><span>推理强度</span><select value={caps.effort} disabled><option>{caps.effort}</option></select></label>}
      </>}
      <div className="formrow"><span className="muted">要求</span><textarea aria-label="绘图要求" value={prompt} onChange={(e) => setPrompt(e.target.value)} /></div>
      {init.base ? <>
        {init.base.grid ? <><GridMethods eid={ctx.eid}/>
        <div className="formrow"><span className="muted">只改格</span><div className="row">{Array.from({ length: 9 }, (_, i) => i + 1).map((c) => <button key={c} className={`pill ${cells.includes(c) ? "on" : ""}`} onClick={() => setCells(cells.includes(c) ? cells.filter((x) => x !== c) : [...cells, c])}>{c}</button>)}</div></div></> : <div className="row"><img width="80" src={blob(init.base.sha256)} alt="本批底图"/><span>第一张输入 · {init.base.name} v{init.base.version_no}</span></div>}
        <div className="formrow"><span className="muted">参考（仅绘图）</span><select value={ref} onChange={(e) => setRef(e.target.value)}><option value="">无</option>{ctx.assets.filter((a) => a.kind === "image" && a.id !== init.base.id).map((a) => <option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select></div>
      </> : <>
        <label className="formrow"><span>素材名称</span><input value={assetName} onChange={e=>setAssetName(e.target.value)} /></label>
          <label className="formrow"><span>素材分类</span><select value={category} onChange={e=>setCategory(e.target.value)}>
            <option value="state">状态图</option><option value="grid">九宫格</option><option value="identity">角色身份图</option><option value="scene">场景图</option>
            <option value="costume">服装图</option><option value="prop">道具图</option><option value="product">商品图</option><option value="reference">参考图</option>
          </select></label>
        <div className="formrow"><span className="muted">张数</span><input type="number" min={1} max={9} value={count} onChange={(e) => setCount(+e.target.value)} /></div>
      </>}
        <div className="formrow"><span className="muted">用于段</span><input placeholder="例：G04（依赖未确认空间锁定的段会被拦下）" value={segs} onChange={(e) => setSegs(e.target.value)} /></div>
        {relevant.map(l=><label className="formrow" key={l.id}><span className="muted">空间参考</span><span>
          <input type="checkbox" checked={chosen.includes(l.versionId!)} onChange={e=>setChosen(e.target.checked?[...chosen,l.versionId!]:chosen.filter(id=>id!==l.versionId))}/>
          加入{l.label}锁定图 · {l.name} v{l.version}</span></label>)}
        {locks.error && <p className="error">{locks.error}</p>}
      <div className="formrow"><span className="muted">尺寸</span><select value={size} onChange={(e) => setSize(e.target.value)}>{(caps?.sizes ?? []).map((s: string) => <option key={s}>{s}</option>)}</select></div>
      <div className="formrow"><span className="muted">质量</span><select value={quality} onChange={(e) => setQuality(e.target.value)}>{(caps?.quality ?? []).map((v: string) => <option key={v}>{v}</option>)}</select></div>
      {!names.length && <p className="error">当前没有支持此操作的图像通道。</p>}
      <p className="muted small">只执行这一批；结果不明会标为“待核对”，不会自动重发。</p>
      <ErrorLine error={act.error} />
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={close}>取消</button><button className="btn primary" disabled={act.busy || !prompt.trim() || !size || !names.length} onClick={create}>生成摘要，去确认</button></div>
    </div></Modal>
  );
}

const BATCH_LABEL: Record<string, string> = { awaiting_authorization: "待确认", authorized: "已授权", completed: "完成", partial: "部分待发",
  pending_reconcile: "待核对", partially_succeeded: "部分成功", failed: "失败", cancelled: "已取消" };

function BatchModal({ batch, assets, close }: { batch: any; assets: any[]; close: () => void }) {
  const b = useQuery<any>("batch_view", { batch_id: batch.id });
  const [dispatched, setDispatched] = useState(false);
  const act = useAction();
  usePoll(!!b.data && !["awaiting_authorization", "cancelled"].includes(b.data.state)
    && b.data.requests.some((r: any) => r.state === "submitted" || (dispatched && r.state === "pending")), b.reload, 1000);
  if (!b.data) return null;
  const d = b.data;
  const pending = d.requests.filter((r: any) => r.state === "pending").length;
  const sending = d.requests.some((r: any) => r.state === "submitted");
  return (
    <Modal onClose={close} width={640}>
      <div className="row"><h2 style={{ margin: 0 }}>{d.operation === "edit" ? (d.spec.items[0]?.cells?.length ? "局部重画" : "垫图改图") : "图像批次"} · {d.count} 张</h2><Tag>{BATCH_LABEL[d.state] ?? d.state}</Tag></div>
      {d.operation === "edit" && !!d.spec.items[0]?.cells?.length && (() => { const it = d.spec.items[0]; const base = assets.find((a) => a.id === it.inputs?.[0]); return base ? (
        <div className="row" style={{ marginTop: 12, flexWrap: "nowrap" }}>
          <div style={{ position: "relative", width: 96 }}><img src={blob(base.sha256)} style={{ width: 96, borderRadius: 6, display: "block" }} />
            <div style={{ position: "absolute", inset: 0, display: "grid", gridTemplateColumns: "repeat(3,1fr)", gridTemplateRows: "repeat(3,1fr)" }}>
              {Array.from({ length: 9 }, (_, i) => <div key={i} style={{ border: it.cells?.includes(i + 1) ? "2px solid #f5b13d" : "none" }} />)}</div></div>
          <span className="muted" style={{ fontSize: "1.54rem" }}>→</span>
          <div className="small">基础图 {base.name}（{base.marks.includes("required") ? "必用" : "候选"}）<br />只改：第 {it.cells?.join("、")} 格；其余 {9 - (it.cells?.length ?? 0)} 格保持不变<br />参考：{it.inputs.slice(1).map((x: string) => assets.find((a) => a.id === x)?.name).join("、") || "无"}</div>
        </div>) : null; })()}
      <table className="table" style={{ margin: "12px 0" }}><tbody>
        {d.spec.librarySources && <tr><td>资产库底图</td><td>{Object.entries(d.spec.librarySources).map(([id,value])=>{
          const source=value as {name:string;versionNo:number;sha256:string;limits:string};
          return <div key={id}>{source.name} v{source.versionNo} · {source.sha256.slice(0,12)}{source.limits && " · "+source.limits}</div>;
        })}</td></tr>}
        {d.spec.palettes?.length>0&&<tr><td>风格来源</td><td><PaletteTrace cards={d.spec.palettes} label="本批次冻结色卡"/></td></tr>}
        <tr><td className="muted">服务</td><td>{d.provider}{d.model ? ` · ${d.model}` : ""} · {d.operation === "edit" ? "编辑（带基础图）" : d.spec.items.some((it: any) => it.inputs?.length) ? "带参考图生成（编辑接口）" : "文生图"}</td></tr>
        <tr><td className="muted">实际输入</td><td>{d.spec.items.map((it: any, i: number) => <div key={i} className="small">#{i + 1} {it.target?.needKey && <strong>{it.target.name} · </strong>}{it.prompt}{it.cells ? ` · 目标格 ${it.cells.join("、")}` : ""}<OrderedImageInputs inputs={d.inputGroups?.[i]}/></div>)}</td></tr>
        {d.spec.sceneLocks && <tr><td>空间锁定核对</td><td>{d.spec.sceneLocks.map((locks: SpaceLock[], i:number)=>locks.map(l=>
          <div key={i+l.id}>第 {i+1} 张 · {l.label} · {l.name ? l.name+" v"+l.version : "原锁定图不可用"} · {l.positions?.length ? "已列入输入 "+l.positions.join("、") : "未列入本张输入"}</div>))}</td></tr>}
        <tr><td className="muted">尺寸 / 质量</td><td>{d.size} · {d.spec.quality ?? "通道默认（历史批次）"}</td></tr>
      </tbody></table>
      {d.authorization && <details open><summary>授权回执</summary>
        <div className="small">授权时间 {new Date(d.authorization.at * 1000).toLocaleString()}</div>
        <div className="small" style={{overflowWrap:"anywhere"}}>确认摘要 {d.authorization.summaryHash}</div>
      </details>}
      {d.requests.map((r: any) => (
        <div key={r.id} className="spread small" style={{ margin: "4px 0" }}>
          <span>#{r.ordinal} <span className="mono">{r.local_request_id}</span> {r.replaces_id ? "（补发）" : ""}
            {r.submitted_at && <small> · 发送于 {new Date(r.submitted_at * 1000).toLocaleString()}</small>}
            {r.provider_request_id && <small> · 服务回执 {r.provider_request_id}</small>}</span>
          <span className="row"><Tag tone={r.state === "succeeded" ? "green" : r.state === "unknown" ? "amber" : r.state === "failed" ? "red" : ""}>{REQ[r.state] ?? r.state}</Tag>
            {r.state === "unknown" && <>
              <button className="btn" onClick={() => act.run(async () => { await cmd("reconcile_request", { request_id: r.id, finding: "not_executed" }); b.reload(); })}>核实未执行</button>
              <label className="btn">登记已完成图<input type="file" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && act.run(async () => { await upload("reconcile", e.target.files![0], { requestId: r.id }); b.reload(); })} /></label></>}
            {d.billingCurrent && !r.replaced && ["confirmed_not_executed", "failed"].includes(r.state) && <button className="btn" onClick={() => act.run(async () => { await cmd("resubmit_request", { request_id: r.id }); setDispatched(false); b.reload(); })}>按原授权重新提交</button>}
          </span>
        </div>))}
      <ErrorLine error={act.error} />
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        {["awaiting_authorization", "authorized"].includes(d.state) && !sending && !dispatched && <button className="btn" disabled={act.busy} onClick={() => act.run(async () => { await cmd("cancel_batch", { batch_id: d.id }); close(); })}>取消批次</button>}
        {d.state === "awaiting_authorization" && d.billingCurrent && <button className="btn primary" disabled={act.busy} onClick={() => act.run(async () => {
          await cmd("authorize_batch", { batch_id: d.id, summary_hash: d.summary_hash });
          if(d.spec.items.some((it:any)=>it.target?.needKey)){await cmd("execute_batch",{batch_id:d.id});setDispatched(true);}
          await b.reload();
        })}>确认授权 {d.count} 张</button>}
        {d.billingCurrent && ["authorized","partial"].includes(d.state) && pending > 0 && <button className="btn primary" disabled={act.busy || sending || dispatched} onClick={() => act.run(async () => {
          await cmd("execute_batch", { batch_id: d.id }); setDispatched(true); b.reload();
        })}>发送已授权的 {pending} 张</button>}
        <button className="btn" onClick={close}>关闭</button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- 07 提示词 (J11)
function PromptsView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "prompts");
  const plan = artifactFor(ctx.view, "plan");
  const planContent = plan?.versions.find((v:any)=>v.id===plan.adopted_version_id)?.content;
  const lay = layout(planContent);
  const supplements=useQuery<any[]>("production_supplements",{episode_id:ctx.eid});
  const sourcePackets=useQuery<any>("prompt_scene_sources",{episode_id:ctx.eid},[plan?.adopted_version_id,artifactFor(ctx.view,"script")?.adopted_version_id]);

  const {hash,go}=useProjectRoute();
  const trialRequested = new URLSearchParams(hash.split("?")[1]).get("trial") === "h3";
  const [trial, setTrial] = useState<any>(null);
  const [trialError, setTrialError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setTrial(null);
    setTrialError("");
    fetch("/api/h3-skill-trial/" + encodeURIComponent(ctx.eid), {signal: controller.signal})
      .then(response => response.json()).then(data => {
        if (!controller.signal.aborted) setTrial(data);
        if (!data && !controller.signal.aborted) setTrialError("本集暂无试验稿");
      }).catch(() => { if (!controller.signal.aborted) setTrialError("试验稿读取失败"); });
    return () => controller.abort();
  }, [ctx.eid, trialRequested]);
  const requestedSegment=new URLSearchParams(hash.split("?")[1]).get("segment");
  const [seg, setSeg] = useState<string>("");
  const [txt, setTxt] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [sound, setSound] = useState<any>(null);
  const act = useAction();
  const [includeImages, setIncludeImages] = useState(true);
  const latest = art?.tasks.flatMap((t:any)=>t.runs).filter((r:any)=>r.purpose==="create").at(-1);
  const progress = latest?.segmentProgress;
  const writing = !!progress && ["running","queued"].includes(latest.status);
  const savedCount = Object.keys(progress?.saved ?? {}).length;
  const entry = current?.content.segments?.[seg];
  const palette=useQuery<any>(entry?"segment_palette_trace":null,{version_id:current?.id,segment:seg});
  useEffect(() => { if (!seg && lay.segments[0]) setSeg(lay.segments[0].id); }, [lay.segments.length]);
  const [amb, setAmb] = useState("");
  const [music, setMusic] = useState("");
  const [dia, setDia] = useState("");
  const editor = useRef<HTMLTextAreaElement>(null);
  const drafts = useRef(new Map<string,{txt:string;amb:string;dia:string;music:string}>());
  const [jump,setJump] = useState<{version:string;sid:string;phrase:string}|null>(null);
  const chooseSegment = (next:string) => {
    if (next === seg) return;
    if (current && entry) drafts.current.set(current.id + "/" + seg,{txt,amb,dia,music});
    setSeg(next);
  };
  useEffect(() => {
    if (requestedSegment && lay.segments.some((s:any)=>s.id===requestedSegment)) chooseSegment(requestedSegment);
  }, [requestedSegment,current?.id]);
  useLayoutEffect(() => {
    const draft = drafts.current.get(current?.id + "/" + seg);
    setTxt(draft?.txt ?? entry?.txt ?? "");
    setAmb(draft?.amb ?? (entry?.sound?.ambience ?? []).join("、"));
    setDia(draft?.dia ?? (entry?.sound?.dialogue ?? []).join("；"));
    setMusic(draft?.music ?? ""); setPreview(null); setSound(null);
  }, [current?.id, seg]);
  useEffect(() => {
    if (!jump || jump.version !== current?.id || jump.sid !== seg) return;
    const frame = requestAnimationFrame(() => {
      const field = editor.current;
      if (field) {
        const at = field.value.indexOf(jump.phrase);
        field.focus();
        field.setSelectionRange(Math.max(0,at), at < 0 ? 0 : at + jump.phrase.length);
        field.scrollIntoView({block:"center"});
      }
      setJump(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [jump,seg,current?.id,txt]);
  if (!art) return <div className="empty"><p>正式分段提示词会引用已采用的制作方案。</p><button className="btn" onClick={ctx.openTrial}>先试写本阶段</button></div>;
  const create = art?.executorSettings?.create ?? ctx.view.kickoff.executor_plan.create;
  const modelDraft = () => act.run(async () => {
    await cmd("create_task", {episode_id:ctx.eid, stage:"prompts", prompt_images:includeImages,
      goal:FIRST_DRAFT.prompts.goal});
    ctx.setFocus(null); ctx.reload();
  });
  const segInfo = lay.segments.find((s: any) => s.id === seg);
  const segmentShots = (planContent?.shots ?? []).filter((shot:any)=>shot.segment===seg).sort((a:any,b:any)=>a.order-b.order);
  const packet=sourcePackets.data?.[seg];
  const errors = (current?.gate.formatErrors ?? []).filter((e: string) => e.startsWith(seg + "："));
  const save = (content: any) => act.run(async () => { const r = await ctx.write("save_manual", { artifact_id: art.id, base_version_id: current?.id ?? null, content }); await ctx.reload(); ctx.setFocus(r.versionId); });
  const ambience=entry?.sound?.ambience ?? [];
  const dialogue=entry?.sound?.dialogue ?? [];
  const draft = {txt, ambience:amb === ambience.join("、") ? ambience : amb.split(/[,，、]/).map(x=>x.trim()).filter(Boolean),
    dialogue:dia === dialogue.join("；") ? dialogue : dia.split(/[；;\n]/).map(x=>x.trim()).filter(Boolean)};
  const editReference = (action:string, asset_id?:string, previous_id?:string, purpose?:string) => act.run(async()=>{
    const r = await ctx.write("edit_prompt_reference",{version_id:current.id, segment_id:seg, action, asset_id, previous_id, purpose, draft});
    await ctx.reload(); ctx.setFocus(r.versionId); return r;
  });
  const applySound = (change:{silent:boolean;retained_notes?:string}|{bgm:boolean;music_description:string}, confirm=false) => act.run(async () => {
    const r = await ctx.write("set_sound", {version_id:current.id, segment_id:seg, change, confirm, draft});
    if (r.needsConfirm) setSound(r);
    else {setSound(null); await ctx.reload(); ctx.setFocus(r.versionId);}
  });
  const move = (i: number, d: number) => act.run(async () => {
    const order = entry.refs.map((r: any) => r.assetVersionId);
    [order[i], order[i + d]] = [order[i + d], order[i]];
    setPreview({ order, ...(await ctx.write("reorder_refs", { version_id: current.id, segment_id: seg, new_order: order, draft })) });
  });
  return (
    <div>
      <div className="stagehead"><div><h1>提示词</h1><div className="muted small">{current ? `${rno(current.display_no)} ${current.id === art.adopted_version_id ? "已采用" : "草稿"}` : "尚无"} · 分段参考与提示词</div></div>
        {current && <VersionTabs ctx={ctx} art={art} current={current} />}</div>
      <div className="row">{lay.segments.map((s: any) => <button key={s.id} className={`pill ${seg === s.id ? "on" : ""}`} onClick={() => {
        const params=new URLSearchParams(hash.split("?")[1]);
        params.set("segment",s.id);
        go(hash.split("?")[0]+"?"+params.toString());
      }}>{s.id} · {s.duration / 1000}s{progress?.saved[s.id] ? " · 已完成" : writing && progress.current===s.id ? " · 正在写" : ""}</button>)}</div>
      {progress && !current && <p role="status">已完成 {savedCount}/{progress.total} 段 · {writing ? (savedCount===progress.total ? "正在汇总" : "正在写 "+progress.current) : latest.status==="stopped" ? "已停止" : "停在 "+progress.current}</p>}
        {(trial?.segments?.[seg] || trialRequested) && <section aria-label="官方 H3 技能对照稿">
          <h2>MiniMax 官方 H3 提示词 · {seg}</h2>
          {trial?.segments?.[seg] ? <textarea className="prompt-main-text" readOnly
            aria-label={seg+" 官方 H3 提示词"} value={trial.segments[seg]} /> : <p>{trialError || "正在读取…"}</p>}
        </section>}
      {segInfo && <details className="prompt-scene" aria-label="场景拍摄简述">
        <summary>场景拍摄简述</summary>
        {sourcePackets.error && <p className="error">{sourcePackets.error}</p>}
        {packet?.scenes.map((group:any)=><div key={group.sceneId ?? "missing"}>
          <b>{group.original?.heading || group.sceneId || "镜头未登记场次"}</b>
          <p data-scene-action={group.sceneId}>{group.original?.action || "缺少可核对的剧本场次原文。"}</p>
          {group.original?.dialogue && <p>对白 · {String(group.original.dialogue)}</p>}
          {supplements.data?.filter(s=>["proposed","confirmed"].includes(s.state)&&s.scenes.includes(group.sceneId)).map(s=><p key={s.id}>制作补充 {s.code} · {s.statement}</p>)}
          {group.original && <a className="small" href={`#/e/${ctx.eid}/script?v=${packet.scriptVersion}&scene=${encodeURIComponent(group.sceneId)}`}>核对当前采用剧本 {rno(packet.scriptRevision)} · {group.sceneId} 原文</a>}
        </div>)}
        <span className="small"><b>{seg} 镜头方案</b>　{segmentShots.map((shot:any)=>shot.txtAnchor || shot.origin?.line || shot.action).filter(Boolean).join(" → ") || segInfo.combinationReason || "当前方案未登记镜头动作。"}</span>
      </details>}
      {entry && <div className="prompt-pair">
      <section aria-label="本段提示词">
        <h2>{entry.prompt_format === "h3_official" ? "MiniMax 官方 H3 提示词 · " + seg : seg + " 提示词正文"}</h2>
        <textarea readOnly={ctx.locked} ref={editor} data-version={current.id} aria-label={seg+" 提示词正文"} className="prompt-main-text" value={txt} onChange={(e) => {setTxt(e.target.value);setSound(null);setPreview(null);}} />
        {entry.prompt_format === "h3_official" && <details><summary>中文逐窗分镜对照</summary>
          {segmentShots.map((shot:any) => <p key={shot.id}><b>{shot.id} · {shot.duration_ms/1000}秒</b>　{shot.camera}　{shot.action}　{shot.sound}</p>)}
        </details>}
        {typeof entry.notes==="string" && entry.notes.trim() && <details><summary>本段制作备注与核对依据</summary><p className="field">{entry.notes}</p></details>}
      </section>
        <section aria-label="本段参考素材"><h2>对应参考素材</h2>
        <div className="prompt-ref-grid">
          {entry.refs.map((r: any, i: number) => { const a = ctx.assets.find((x) => x.id === r.assetVersionId); return (
            <div key={r.assetVersionId} className="card line" style={{ minWidth:0, padding:8 }}>
              {a && (a.kind === "image" ? <img src={blob(a.sha256)} alt={a.name} style={{width:"100%",height:72,objectFit:"contain",borderRadius:6}}/>
                : <MediaPreview episode={ctx.eid} asset={a} label={r.label}/>)}
              <div className="spread"><b>{r.label}</b><span className="row">{i > 0 && <button className="btn ghost" aria-label={r.label+"前移"} disabled={act.busy || ctx.locked} onClick={() => move(i, -1)}>←</button>}{i < entry.refs.length - 1 && <button className="btn ghost" aria-label={r.label+"后移"} disabled={act.busy || ctx.locked} onClick={() => move(i, 1)}>→</button>}</span></div>
              <div className="muted tiny">{a?.name} · {r.role || "用途未写"}{r.grid ? " · 九宫格" : ""}</div>
              {r.shots?.length>0 && <div className="muted tiny">{r.shots.join("、")}</div>}
              <select aria-label={"替换"+r.label} disabled={act.busy || ctx.locked} style={{width:"100%",marginTop:4}} value="" onChange={e=>editReference("replace",e.target.value,r.assetVersionId)}>
                <option value="">{segInfo?.strategy === "grid" ? "替换本段九宫格…" : "替换此参考…"}</option>
                {ctx.assets.filter(x=>x.kind===r.kind && (segInfo?.strategy!=="grid" || x.grid) && !entry.refs.some((ref:any)=>ref.assetVersionId===x.id)).map(x=><option key={x.id} value={x.id}>{x.name} v{x.version_no}{x.marks.includes("required") ? "（必用）" : ""}</option>)}
              </select>
              {segInfo?.strategy!=="grid" && (r.kind!=="image" || entry.refs.filter((ref:any)=>ref.kind==="image").length>1) &&
                <button className="btn ghost" aria-label={"移出"+r.label} disabled={act.busy || ctx.locked} onClick={()=>editReference("remove",undefined,r.assetVersionId)}>移出本段</button>}
            </div>); })}
          {segInfo?.strategy !== "grid" && <select aria-label="添加图片参考" disabled={act.busy || ctx.locked} style={{alignSelf:"center",width:130}} value="" onChange={e=>{
            const v=ctx.assets.find(x=>x.id===e.target.value);
            if (v) void editReference("add",v.id,undefined,v.name.replace(/\.png$/i,""));
          }}><option value="">＋ 添加图片…</option>{ctx.assets.filter(x=>x.kind==="image" && !entry.refs.some((r:any)=>r.assetVersionId===x.id)).map(x=><option key={x.id} value={x.id}>{x.name} v{x.version_no}</option>)}</select>}
        </div>
        </section>
      </div>}
      {current && <VersionSignals key={current.id} version={current.id} number={current.display_no} locate={(sid,phrase)=>{chooseSegment(sid);setJump({version:current.id,sid,phrase});}}/>}
      {entry ? <>
        <RhythmReview key={current.id} version={current.id}/>
        {palette.error&&<p className="error">{palette.error}</p>}
        <fieldset className="edit-fields" disabled={ctx.locked}><ReferenceReview ctx={ctx} version={current.id} sid={seg} entry={entry} draft={draft} save={ctx.setFocus}/></fieldset>
        {palette.data&&<PaletteTrace cards={palette.data.cards} label={palette.data.direct?`${seg} 所在稿起草时参考`:`${seg} 上游稿 R${palette.data.displayNo} 起草时参考`}/>}
        {segInfo?.strategy!=="grid" && <PromptMedia key={ctx.eid+"/"+seg+"/"+current.id} episode={ctx.eid} reload={ctx.reload} busy={act.busy || ctx.locked}
          assets={ctx.assets.filter(x=>(x.kind==="audio" || x.kind==="video") && !entry.refs.some((r:any)=>r.assetVersionId===x.id))}
          attach={(id,purpose)=>editReference("add",id,undefined,purpose)}/>}
        {preview && <div className="card amber" style={{ marginBottom: 10 }}><b>重排预览</b> <span className="small">{Object.entries(preview.preview.mapping).map(([a, b]) => `${a}→${b}`).join("，")}</span>
          {preview.preview.unclearRoles.length > 0 && <div className="small">用途不明确，请核对：{preview.preview.unclearRoles.join("、")}</div>}
          <div className="field small" style={{ marginTop: 6 }}>{preview.preview.txtDiff.map((d: any, i: number) => d.op === "same" ? <span key={i}>{d.text}</span> : d.op === "del" ? <del key={i} className="d">{d.text}</del> : <ins key={i} className="d">{d.text}</ins>)}</div>
          <div className="row" style={{ marginTop: 6 }}><button className="btn primary" onClick={() => act.run(async () => { const r = await ctx.write("reorder_refs", { version_id: current.id, segment_id: seg, new_order: preview.order, draft, confirm: true }); await ctx.reload(); ctx.setFocus(r.versionId); setPreview(null); })}>确认重排</button><button className="btn" onClick={() => setPreview(null)}>取消</button></div></div>}

        {errors.length > 0 && <div className="error">{errors.join("；")}</div>}
        <div className="grid2" style={{ marginTop: 8 }}>
          <input readOnly={ctx.locked} placeholder="环境声（逗号分隔）" value={amb} onChange={(e) => {setAmb(e.target.value);setSound(null);setPreview(null);}} />
          <input readOnly={ctx.locked} placeholder="对白（多条用；分隔，英语）" value={dia} onChange={(e) => {setDia(e.target.value);setSound(null);setPreview(null);}} /></div>
        <div className="spread" style={{ marginTop: 10 }}>
          <div className="row small">声音：<Tag tone={entry.sound?.bgm ? "green" : ""}>{entry.sound?.bgm ? "有背景音乐" : "无背景音乐"}</Tag>
            {(entry.sound?.ambience ?? []).map((x: string) => <Tag key={x}>{x}</Tag>)}{(entry.sound?.dialogue ?? []).map((x: string) => <Tag key={x} tone="blue">{x}</Tag>)}
            {entry.sound?.silent && <Tag tone="amber">全静音</Tag>}</div>
          <div className="row">
            {["h3_official","readable_v3"].includes(entry.prompt_format) && !entry.sound?.bgm && <input readOnly={ctx.locked} aria-label="本段配乐描述" placeholder="具体配乐描述" value={music} onChange={e=>setMusic(e.target.value)}/>}
            <button className="btn" disabled={act.busy || ctx.locked} onClick={() => applySound({bgm:!entry.sound?.bgm,music_description:music})}>{entry.sound?.bgm ? "去掉背景音乐" : "加背景音乐"}</button>
            <button className="btn" disabled={act.busy || ctx.locked} onClick={() => applySound({silent:!entry.sound?.silent})}>{entry.sound?.silent ? "解除全静音" : "全静音"}</button>
            <button className="btn primary" disabled={ctx.locked} onClick={() => save({ ...current.content, segments: { ...current.content.segments, [seg]: { ...entry, txt,
              sound: { ...(entry.sound ?? {}), ambience:draft.ambience, dialogue:draft.dialogue } } } })}>保存本段</button>
          </div>
        </div>
        {sound && <div className="card amber" style={{ marginTop: 10 }}><b>全静音会与以下内容冲突</b>{sound.conflicts.map((c: any) => <div key={c.item} className="small">{c.item}：{Array.isArray(c.detail) ? c.detail.join("、") : c.detail}</div>)}
          {typeof sound.retainedNotes==="string" && <label>静音后保留的画面文字及固定说明<textarea value={sound.retainedNotes} onChange={e=>setSound({...sound,retainedNotes:e.target.value})}/></label>}
          <div className="row" style={{ marginTop: 6 }}><button className="btn danger" disabled={act.busy || ctx.locked} onClick={() => applySound({silent:true,retained_notes:sound.retainedNotes??undefined},true)}>仍然全静音</button><button className="btn" onClick={() => setSound(null)}>保留</button></div></div>}
      </> : progress?.saved[seg] ? <section><h2>{seg} 提示词</h2><textarea className="prompt-main-text" readOnly value={progress.saved[seg]} /></section>
      : writing ? <p className="muted">{progress.current===seg ? "本段正在起草，完成后将在这里显示。" : "本段等待起草。"}</p>
      : <div style={{ marginTop: 14 }}><p className="muted">该段还没有提示词。创作执行器为 {PROVIDER_LABEL[create.provider] ?? create.provider} · {create.model ?? "默认型号"}。</p>
        <div className="row"><button className="btn primary" disabled={act.busy || ctx.locked} onClick={modelDraft}>{FIRST_DRAFT.prompts.label}</button>
          <label><input type="checkbox" checked={includeImages} onChange={e => setIncludeImages(e.target.checked)}/>同时发送已绑定参考原图</label></div></div>}
      <ErrorLine error={act.error} />
      {current && <AdoptBar ctx={ctx} art={art} version={current} next="delivery" hint="完成提示词本步检查并采用后，进入交付" />}
    </div>
  );
}

// ---------------------------------------------------------------- 08 分镜审核
function ReviewInputs({runId,label}:{runId:string;label:string}) {
  const [open,setOpen] = useState(false);
  const packet = useQuery(open?"run_manifest":null,{run_id:runId});
  return <details onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>{label}送审文件</summary>
    {packet.error && <p role="alert">{packet.error}</p>}
    {packet.data && (packet.data.matches ? <InputGroups groups={packet.data.groups}/> : <p className="error">送审清单指纹与运行记录不符</p>)}
  </details>;
}

function ReviewView({ ctx }: { ctx: Ctx }) {
  const { go } = useProjectRoute();
  const { art, current } = useVersion(ctx, "plan");
  const act = useAction();
  if (!art || !current) return <div className="empty">先完成08分镜；表演与摄影在同一张表中设计。</div>;
  const latest=current.reviews?.[current.reviews.length-1];
  return <div>
    <div className="stagehead"><div><h1>08 分镜审核</h1><div className="muted small">检查本版分镜和实际参考用途，沿用已接受素材差异。09仍检查自己的提示词结果，不再整体独审。</div></div>
      <div className="row"><button className="btn" disabled={act.busy || ctx.locked} onClick={()=>act.run(async()=>{
        await cmd("submit_review",{version_id:current.id}); await ctx.reload();
      })}>审核当前分镜</button><button className="btn primary" onClick={()=>go(`#/e/${ctx.eid}/prompts`)}>进入09提示词</button></div>
    </div>
    <VersionTabs ctx={ctx} art={art} current={current}/>
    {latest ? <StoryReview ctx={ctx} version={current} review={latest}/> : <p className="muted">本版未执行独立审核，不标记为已审通过。</p>}
    <ErrorLine error={act.error}/>
  </div>;
}

// ---------------------------------------------------------------- 09 发布包 (J42)
function PublishView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "publish");
  const [form, setForm] = useState<any>(null);
  const settings = useQuery<any>("publish_settings", {project_id: ctx.view.episode.project_id});
  const composed = useQuery<any>(current ? "compose_publish" : null, { episode_id: ctx.eid, version_id: current?.id }, [current?.id]);
  const act = useAction();
  useEffect(() => { setForm(current?.content ?? { title: "", body: "", tags: [], extraFields: {}, cues: [] }); }, [current?.id]);
  if (!art) return <div className="empty"><p>正式发布包会引用已采用的提示词。</p>{artifactFor(ctx.view,"prompts")?.adopted_version_id &&
    <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("open_stage",{episode_id:ctx.eid,stage:"publish"});await ctx.reload();})}>开始写发布文案</button>}<ErrorLine error={act.error}/><button className="btn" onClick={ctx.openTrial}>先试写本阶段</button></div>;
  if (!form) return null;
  const copy = (t: string) => navigator.clipboard.writeText(t).catch(() => alert("复制失败，请手动选取原文"));
  const adoptedContent = (stage:string) => {
    const a=artifactFor(ctx.view,stage);
    return a?.versions.find((v:any)=>v.id===a.adopted_version_id)?.content;
  };
  const segments=layout(adoptedContent("plan")).segments;
  const bgm=Object.values(adoptedContent("prompts")?.segments ?? {}).some((entry:any)=>entry.sound?.bgm);
  const f = composed.data?.fields;
  return (
    <div>
      {ctx.view.completion?.completed && <div className="ok small">本集项目已完成 · 中间文件按完成后 10 天规则管理</div>}
      <div className="stagehead"><div><h1>发布文案</h1><div className="muted small">最后一步：完成并采用发布文案后，本集项目制作完成</div></div>
        {current && <VersionTabs ctx={ctx} art={art} current={current} />}</div>
      <div className="grid2">
        <fieldset disabled={ctx.locked} className="col edit-fields">
          {[["title", "发布标题"], ["body", settings.data?.body_fixed_lines?.length ? "正文第一行" : "正文"],
            ...(settings.data?.additional_fields ?? []).map((v: any) => [v.key, v.label])].map(([k, l]) => {
              const nested = !["title","body","suno_title","suno_style","suno_exclude"].includes(k);
              return <label key={k} className="col" style={{gap:4}}><span className="muted small">{l}</span>
                <textarea rows={k==="body"?3:1} value={(nested ? form.extraFields?.[k] : form[k]) ?? ""}
                  onChange={e => setForm(nested ? {...form, extraFields:{...form.extraFields,[k]:e.target.value}} : {...form,[k]:e.target.value})}/></label>;
            })}
          <label className="col" style={{ gap: 4 }}><span className="muted small">本集话题标签（逗号分隔）</span><input value={(form.tags ?? []).join(",")} onChange={(e) => setForm({ ...form, tags: e.target.value.split(/[,，]/).map((x) => x.trim()).filter(Boolean) })} /></label>
          <PublishCues form={form} segments={segments} bgm={bgm} change={patch=>setForm({...form,...patch})}/>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await ctx.write("save_manual", { artifact_id: art.id, base_version_id: current?.id ?? null, content: form }); await ctx.reload(); ctx.setFocus(r.versionId); })}>保存为新版本</button>
        </fieldset>
        <div className="col">
          {f ? Object.entries(f).map(([k, v]) => (
            <div key={k} className="card"><div className="spread"><span className="muted small">{composed.data.fieldLabels[k]}</span><button className="btn ghost" onClick={() => copy(v as string)}>复制</button></div><div className="small" style={{ whiteSpace: "pre-wrap", userSelect: "all" }}>{v as string}</div></div>))
            : <div className="error small">{composed.error ?? "保存后预览拼接后的平台文案"}</div>}
          {composed.data && <div className="card"><div className="spread"><span className="muted small">音乐</span><Tag tone={composed.data.music === "启用" ? "green" : ""}>{composed.data.music}</Tag></div>
            {composed.data.basisFrozen===false && <p className="muted small">此旧版没有保存当时的分段时间依据。此处保留已登记的对位行。</p>}
            <div className="muted small" style={{ marginTop: 6 }}>英文交付标题：{composed.data.englishTitle}</div>
            {composed.data.cues.map((c: any, i: number) => <div key={i} className="small">{c.segment ?? c.scene ?? "全片"} · {c.at} · {c.action}</div>)}</div>}
        </div>
      </div>
      <ErrorLine error={act.error} />
      {current && <AdoptBar ctx={ctx} art={art} version={current} next="delivery" hint="采用后进入交付核验" />}
    </div>
  );
}

// ---------------------------------------------------------------- 10 交付核验 (J12, J19, J55)
function DeliveryRecord({d,eid,projectId}:{d:any;eid:string;projectId:string}) {
  const [verify,setVerify] = useState<any>(null);
  const act = useAction();
  return (
    <div className="card line" style={{ marginBottom: 10 }}>
      <div className="spread"><span><Tag tone={d.scope === "full" ? "green" : "blue"}>{d.scope === "full" ? (d.versions?.publish ? "整集" : "交接 · 待发布文案") : "试验"}</Tag> {d.state==="needs_recheck" && <Tag tone="amber">待复核</Tag>} 交付号 <span className="mono">{d.id.slice(0, 8)}</span> · {d.segments.join("、")} · {Object.keys(d.files).length} 个文件</span>
        <div className="row">{d.cleanedAt ? <span className="muted">中间交付文件已清理，制作记录保留</span> : <><a className="btn" target="_blank" href={deliveryFile(d.id, "00_图文对照.html")}>打开图文页</a>
          <button className="btn" onClick={() => navigator.clipboard.writeText(d.html)}>复制 HTML 绝对路径</button>
          <button className="btn" onClick={() => act.run(async () => setVerify(await q("verify_delivery", { delivery_id: d.id })))}>离线验包</button>
          {d.scope === "full" && <a className="btn primary" href={`#/p/${projectId}/closure-start/${d.id}`}>整理本集设定</a>}</>}</div></div>
      <div className="muted tiny mono" style={{ marginTop: 6 }}>{d.html}</div>
      <p className="small">图文包记录计划时长。实际生成视频后，在制作和剪辑工具中核对片长、画幅、动作与声音。</p>
      {!d.cleanedAt && <a className="btn" href={`#/e/${eid}/package?delivery=${encodeURIComponent(d.id)}`}>将这次交付打包给 H3</a>}
      {verify && <div className={verify.ok ? "ok small" : "error small"}>{verify.ok ? "验包通过：文件与指纹一致" : verify.errors.map((e: any) => `${e.file} ${e.problem}`).join("；")}</div>}
      <ErrorLine error={act.error}/>
    </div>
  );
}

function SelectedDelivery({ctx,id}:{ctx:Ctx;id:string}) {
  const result = useQuery<any>("delivery_view",{delivery_id:id});
  const record = result.data;
  return <div>
    <div className="stagehead"><h1>交付记录</h1><a href={`#/e/${ctx.eid}/delivery`}>查看本集全部交付</a></div>
    {result.error ? <ErrorLine error={{message:result.error}}/> : !record ? <p>正在读取交付记录…</p>
      : record.episode_id === ctx.eid ? <DeliveryRecord key={id} d={record} eid={ctx.eid} projectId={ctx.view.episode.project_id}/>
      : <p>此交付不属于当前单集。</p>}
  </div>;
}

function DeliveryView({ ctx }: { ctx: Ctx }) {
  const { hash, go }=useProjectRoute();
  const params=new URLSearchParams(hash.split("?")[1]);
  const scope=params.get("scope") || (ctx.view.kickoff.spec.packaging==="trial" ? "trial" : "full");
  const segs=params.getAll("segment");
  function setScope(value:string) {params.set("scope",value);go(hash.split("?")[0]+"?"+params);}
  function setSegs(values:string[]) {
    params.delete("segment");values.forEach(id=>params.append("segment",id));go(hash.split("?")[0]+"?"+params);
  }
  const choosingSegments = scope === "trial" && segs.length === 0;
  const problems = useQuery<any>(choosingSegments ? null : "delivery_problems", { episode_id: ctx.eid, scope, segments: scope === "trial" ? segs : null, require_publish: false }, [scope, segs.join()]);
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const act = useAction();
  const plan = artifactFor(ctx.view, "plan");
  const adoptedPlan = plan?.versions.find((v: any) => v.id === plan.adopted_version_id)?.content;
  const lay = layout(adoptedPlan);
  const images = new Set(ctx.assets.filter(a => a.kind === "image").map(a => a.id));
  const missingFrames = (adoptedPlan?.shots ?? []).filter((shot:any) => scope === "full" || segs.includes(shot.segment))
    .flatMap((shot:any) => {
      const missing = frameFields.filter(([field]) => !images.has(shot[field])).map(([, label]) => label);
      return missing.length ? [shot.segment+" "+shot.id+" · 缺"+missing.join("、")] : [];
    });
  const load = async () => {
    const ov = await q("project_overview", { project_id: ctx.view.episode.project_id });
    const ep = ov.episodes.find((e: any) => e.id === ctx.eid);
    setDeliveries(await Promise.all((ep?.deliveries ?? []).map((d: any) => q("delivery_view", { delivery_id: d.id }))));
  };
  useEffect(() => { load(); }, [ctx.eid]);
  const list = problems.data?.[0] ?? [];
  const byStage: Record<string, any[]> = {};
  list.forEach((p: any) => { (byStage[p.stage] ??= []).push(p); });
  return (
    <div>
      <div className="stagehead"><div><h1>交付核验</h1><div className="muted small">核验实际读取文件、指纹和内容；只有核验通过且文件完整落盘才登记交付号</div></div>
        <Pills value={scope} onChange={setScope} options={[["full", "整集完整交付"], ["trial", "限定试验范围"]]} /></div>
      {scope === "trial" && <div className="row">{lay.segments.map((s: any) => <button key={s.id} className={`pill ${segs.includes(s.id) ? "on" : ""}`} onClick={() => setSegs(segs.includes(s.id) ? segs.filter((x) => x !== s.id) : [...segs, s.id])}>{s.id}</button>)}<span className="muted small">{choosingSegments ? "请选择要核验的拍摄段。" : "试验范围从零计时，不更新整集完成状态"}</span></div>}
      {missingFrames.length > 0 && <details className="card">
        <summary>未单独绑定起落幅 · {missingFrames.length} 个动作窗口（可选）</summary>
        <p className="small">{missingFrames.join("；")}</p>
        <a href={`#/e/${ctx.eid}/plan?v=${plan.adopted_version_id}`}>查看采用的分镜</a>
      </details>}
      {list.length > 0 ? (
        <div className="card red" style={{ marginTop: 14 }}>
          <b className="error">结构阻断 · {list.length} 项</b><span className="muted small"> · 不能放行，逐条定位到出错的阶段</span>
          {Object.entries(byStage).map(([stg, ps]) => (
            <div key={stg} style={{ marginTop: 10 }}><div className="row"><Tag tone="red">{stg}</Tag><a className="btn ghost" href={`#/e/${ctx.eid}/${stg === "recheck" || stg === "review" ? "prompts" : stg === "assets" ? "assets" : stg === "space" ? "space" : stg}`}>去修复</a></div>
              {ps.slice(0, 8).map((p: any, i: number) => <div key={i} className="small" style={{ margin: "3px 0" }}>{p.segment ?? ""} {p.file ? `· ${p.file}` : ""} · {p.problem}</div>)}</div>))}
        </div>) : problems.data && <div className="card" style={{ marginTop: 14, background: "#13261f" }}><Tag tone="green">核验通过</Tag> <span className="small">可以写入交付目录（先写临时目录，校验后改名）。</span></div>}
      <button className="btn primary" style={{ marginTop: 12 }} disabled={!problems.data || !!problems.error || list.length > 0 || act.busy} onClick={() => act.run(async () => { await cmd("deliver", { episode_id: ctx.eid, scope, segments: scope === "trial" ? segs : null, pre_publication: true }); await load(); ctx.reload(); problems.reload(); })}>生成图文交付</button>
      <h2 style={{ marginTop: 24 }}>已登记的交付</h2>
      {deliveries.length === 0 && <div className="muted">尚无。</div>}
      {deliveries.map((d) => (
        <DeliveryRecord key={d.id} d={d} eid={ctx.eid} projectId={ctx.view.episode.project_id}/>))}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 11 H3 出包 (J12)
const emptyPackageParams={resolution:"",audio:"",candidates:"",instanceType:"",seed:""};
function packageOverrides(params:typeof emptyPackageParams) {
    const overrides: Record<string, string|boolean|number> = {};
    if (params.resolution) overrides.resolution=params.resolution;
    if (params.instanceType) overrides.instanceType=params.instanceType;
    if (params.candidates) overrides.candidates=Number(params.candidates);
    if (params.audio) overrides.audio=params.audio==="on";
    if (params.seed !== "") {
      const seed=Number(params.seed);
      if (!/^\d+$/.test(params.seed) || !Number.isSafeInteger(seed) || seed>9007199254740987)
        throw new Error("种子须为 0–9007199254740987 的整数");
      overrides.seed=seed;
    }
    return overrides;
  }

function PackageView({ ctx,panel }: { ctx: Ctx; panel?:HTMLElement|null }) {
  const [wide,setWide]=useState(()=>matchMedia("(min-width:1121px)").matches);
  useEffect(()=>{
    const media=matchMedia("(min-width:1121px)");
    const changed=()=>setWide(media.matches);
    media.addEventListener("change",changed);
    return ()=>media.removeEventListener("change",changed);
  },[]);
  const { hash, go } = useProjectRoute();
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const [did, setDid] = useState("");
  const [segs, setSegs] = useState<string[]>([]);
  const [batch,setBatch]=useState(emptyPackageParams);
  const [individual,setIndividual]=useState<Record<string,typeof emptyPackageParams>>({});
  const [target,setTarget]=useState("");
  const params=target?(individual[target]??emptyPackageParams):batch;
  const setParams=(next:typeof emptyPackageParams)=>target?setIndividual(old=>({...old,[target]:next})):setBatch(next);
  const [out, setOut] = useState<any>(null);
  const [preview,setPreview]=useState<any>(null);
  const inputKey=JSON.stringify([ctx.eid,did,segs,batch,individual]);
  const result=preview?.inputKey===inputKey?preview:null;
  const act = useAction();
  const requestedDelivery = new URLSearchParams(hash.split("?")[1]).get("delivery");
  useEffect(() => { let active = true; (async () => {
    const ov = await q("project_overview", { project_id: ctx.view.episode.project_id });
    const ds = await Promise.all((ov.episodes.find((e: any) => e.id === ctx.eid)?.deliveries ?? []).map((d: any) => q("delivery_view", { delivery_id: d.id })));
    if (!active) return;
    const pick = requestedDelivery ? ds.find(x => x.id === requestedDelivery) : ds[ds.length - 1];
    setBatch(emptyPackageParams);setIndividual({});setTarget("");setDeliveries(ds); setDid(pick?.id ?? ""); setSegs(pick?.segments ?? []); setOut(null);
  })(); return () => { active = false; }; }, [ctx.eid, requestedDelivery]);
  const d = deliveries.find((x) => x.id === did);
  function packageArgs(){
    const entries=segs.filter(s=>individual[s]).map(s=>[s,packageOverrides(individual[s])]);
    return {delivery_id:did,segments:segs,overrides:packageOverrides(batch),segment_overrides:Object.fromEntries(entries)};
  }
  const [copied,setCopied]=useState("");
  const controls=d && d.state!=="needs_recheck" && <div className="package-controls">
    <h2>交给 H3 打包工具</h2>
    <p>{d.scope==="full"?"整集交付":"试验交付"} · 本次选择 {segs.length} / {d.segments.length} 段</p>
        <div className="row" style={{ marginTop: 12 }}>{d.segments.map((s: string) => <button key={s} className={`pill ${segs.includes(s) ? "on" : ""}`} onClick={() => {if (target===s)setTarget("");setSegs(segs.includes(s) ? segs.filter((x) => x !== s) : [...segs, s]);setOut(null);}}>{s}</button>)}</div>
        <label className="row small">参数应用范围<select aria-label="参数应用范围" value={target} onChange={e=>setTarget(e.target.value)}>
          <option value="">本次选中的全部段</option>{segs.map(s=><option key={s} value={s}>仅 {s}</option>)}</select></label>
        <div className="grid3" style={{ marginTop: 12 }} onChange={()=>setOut(null)}>
          <label className="col small">分辨率<select aria-label="出包分辨率" value={params.resolution} onChange={(e) => setParams({ ...params, resolution: e.target.value })}><option value="">{target?"沿用本次整包设置":"沿用各段设置"}</option>{["default", "480p", "768p", "1080p"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="col small">候选<select aria-label="出包候选数" value={params.candidates} onChange={(e) => setParams({ ...params, candidates: e.target.value })}><option value="">{target?"沿用本次整包设置":"沿用各段设置"}</option>{[1, 2, 4].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="col small">档位<select value={params.instanceType} onChange={(e) => setParams({ ...params, instanceType: e.target.value })}><option value="">{target?"沿用本次整包设置":"沿用各段设置"}</option>{["default", "plus", "ultra"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="col small">声音<select aria-label="出包声音" value={params.audio} onChange={(e) => setParams({ ...params, audio: e.target.value })}><option value="">{target?"沿用本次整包设置":"沿用各段设置"}</option><option value="on">开</option><option value="off">关</option></select></label>
          <label className="col small">种子（留空沿用各段设置）<input value={params.seed} onChange={(e) => setParams({ ...params, seed: e.target.value })} /></label>
        </div>
        <button className="btn" style={{marginTop:12}} disabled={!segs.length||act.busy} onClick={()=>act.run(async()=>{
          setPreview(null);
          setPreview({...await cmd("package_preview",packageArgs()),inputKey});
        })}>预览所选段</button>
    {result && <button className="btn" onClick={()=>act.run(async()=>{
      const lines=result.segments.map((s:any)=>s.id+" · "+s.params.duration+" 秒 · "+s.params.ratio+
        " · "+s.params.resolution+" · 声音"+(s.params.audio?"开":"关")+" · "+s.params.candidates+" 个候选 · "+s.params.instanceType+
        " · 种子 "+(s.params.seed??"随机")+"\n"+s.refs.map((r:any)=>r.label+" "+r.name+" "+r.version+" "+r.sha256).join("\n"));
      await navigator.clipboard.writeText([d.html,"交付 "+d.id,...lines].join("\n\n"));setCopied(result.previewHash);
    })}>{copied===result.previewHash?"已复制出包说明":"复制出包说明"}</button>}
    {d.scope==="full" && <div><a href={`#/p/${ctx.view.episode.project_id}/closure-start/${d.id}`}>整理本集设定</a></div>}
  </div>;
  return (
    <div>
      <div className="stagehead"><div><h1>H3 出包</h1><div className="muted small">交给本地打包器离线导出 ZIP；更换清单、素材或范围后重新验包</div></div></div>
      {!d ? <div className="muted">{requestedDelivery ? "未找到指定交付，请从本集交付记录重新选择。" : "先完成图文交付。"}</div> : d.cleanedAt ? <div className="muted">这份中间交付文件已清理，请返回交付记录选择最终版本。</div> : <>
        <div className="row"><select aria-label="选择交付版本" value={did} onChange={e=>go(`#/e/${ctx.eid}/package?delivery=${encodeURIComponent(e.target.value)}`)}>{deliveries.map((x) => <option key={x.id} value={x.id}>{x.scope === "full" ? "整集" : "试验"} {x.id.slice(0, 8)}</option>)}</select>
          <button className="btn" onClick={()=>act.run(()=>navigator.clipboard.writeText(d.html))}>复制图文页路径</button>
          <details className="small"><summary>图文页位置</summary><code>{d.html}</code></details></div>
        <p>{d.state==="needs_recheck"?"待复核":d.scope==="full"?(d.versions?.publish ? "整集图文已交付" : "H3 交接已就绪 · 待完成发布文案"):"试验图文已交付"} · {d.segments.length} 段</p>
        {!!d.packages?.length && <section aria-label="已生成的 H3 包"><h3>已生成的 H3 包</h3>{d.packages.map((p:any)=><p key={p.id}>{p.cleanedAt ? <span className="muted">{p.filename} · 旧包已清理</span> : <a download href={"/api/package-download/"+encodeURIComponent(p.id)}>下载 {p.filename}</a>}</p>)}</section>}
        {panel && wide ? createPortal(controls,panel):controls}
        <div className="row package-links">{["00_图文对照.html","01_发布包.md","03_精剪交接.md","episode.json"].filter(file=>d.files[file]).map(file=>
          <a key={file} href={deliveryFile(did,encodeURIComponent(file))} target="_blank" rel="noreferrer">{file}</a>)}</div>
        {result && <section className="card" aria-label="出包预览">
          <h3>出包预览 · {result.segments.length} 段</h3>
          <div className="package-table"><table><thead><tr>{["段","时长","策略","分辨率","声音","候选","档位","种子","参考"].map(h=><th key={h}>{h}</th>)}</tr></thead>
            <tbody>{result.segments.map((s:any)=><tr key={s.id}><th>{s.id}</th><td>{s.params.duration} 秒</td>
              <td>{s.strategy==="grid"?"九宫格":"自由参考"}</td>
              {["resolution","audio","candidates","instanceType","seed"].map(key=><td key={key}>
                {key==="audio"?(s.params.audio?"开":"关"):s.params[key]??"随机"}<small>{s.origins[key]}</small></td>)}
              <td>{s.refs.map((r:any)=>r.label).join("、")}</td></tr>)}</tbody></table></div>
          <div className="package-files">{result.segments.map((s:any)=><details key={s.id} open><summary>{s.id} · {s.refs.length} 项参考 · {s.params.ratio}</summary>
            <ol>{s.refs.map((r:any)=><li key={r.label}><a href={deliveryFile(did,r.path.split("/").map(encodeURIComponent).join("/"))} target="_blank" rel="noreferrer">
              {r.label} · {r.name}</a> · {r.version}{r.role?" · "+r.role:""} <code>{r.sha256.slice(0,12)}</code></li>)}</ol>
          </details>)}</div>
        </section>}
        <button className="btn primary" style={{ marginTop: 12 }} disabled={!segs.length || act.busy} onClick={() => act.run(async () => {
          const args = packageArgs();
          const packed=await cmd("package",{...args,expected_preview:result?.previewHash});
          setOut({...packed,inputKey});
          const link=document.createElement("a");link.href="/api/package-download/"+encodeURIComponent(packed.packageId);link.download="";link.click();
          const updated=await q("delivery_view",{delivery_id:did});setDeliveries(ds=>ds.map(x=>x.id===did?updated:x));
        })}>下载 H3 包</button>
        {out?.inputKey===inputKey && <div className="card" style={{ marginTop: 12, background: "#13261f" }}><b className="ok">已导出</b> <span className="mono small">{out.path}</span>
          <div className="small" style={{ marginTop: 6 }}>project.json schemaVersion {out.project.schemaVersion} · {out.project.scope} · {out.project.segments.length} 段 · {out.project.assets.length} 项素材</div>
          <div className="small">角色素材：{out.project.assets.some((a: any) => a.fixedRole) ? "含固定角色 fixedRole" : "均为普通资产（未伪装成 fixedRole）"}</div>
          {out.project.segments.map((s: any) => <div key={s.id} className="muted tiny">{s.id} · {Object.entries(s.params).map(([k, v]) => `${k}=${v}`).join(" ")}</div>)}</div>}
      </>}
      <ErrorLine error={act.error} />
    </div>
  );
}

function charDiff(a0: string, b0: string): { op: string; text: string }[] {
  // long texts: diff by sentence to keep it fast; short texts: by character
  const units = a0.length * b0.length > 2_000_000;
  const split = (x: string) => units ? x.split(/(?<=[。！？\n])/) : [...x];
  const a = split(a0), b = split(b0);
  const n = a.length, m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { op: string; text: string }[] = [];
  const push = (op: string, ch: string) => { const last = out[out.length - 1]; if (last && last.op === op) last.text += ch; else out.push({ op, text: ch }); };
  let i = 0, j = 0;
  while (i < n && j < m) { if (a[i] === b[j]) { push("same", a[i]); i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) { push("del", a[i]); i++; } else { push("add", b[j]); j++; } }
  while (i < n) push("del", a[i++]);
  while (j < m) push("add", b[j++]);
  return out;
}

function ShotEditor({ ctx, art, version, shotId, onSaved }: any) {
  const bms = useQuery<any[]>("episode_bookmarks", { episode_id: ctx.eid });
  const shots = version.content.shots;
  const [confirmDelete,setConfirmDelete] = useState(false);
  const orig = shots.find((x: any) => x.id === shotId);
  const [sh, setSh] = useState<any>(orig);
  const act = useAction();
  useEffect(() => { setSh(orig); setConfirmDelete(false); }, [shotId, version.id]);
  if (!sh) return null;
  const save = (content: any, note: string) => act.run(async () => { const r = await ctx.write("save_manual", { artifact_id: art.id, base_version_id: version.id, content, note }); await ctx.reload(); onSaved(r.versionId); });
  const ref = sh.refs?.[0];
  const first = shots.filter((s: any) => s.segment === sh.segment).sort((a: any,b: any) => a.order - b.order)[0]?.id === sh.id;
  return (
    <div className="card cyan" style={{ marginTop: 14 }}>
      <div className="spread"><b>编辑镜头 {sh.id}</b><span className="muted small">保存为新草稿；采用前可看影响，取消即不采用</span></div>
      <div className="grid3" style={{ marginTop: 8 }}>
        <label className="col small">镜长（秒）<input type="number" step="0.5" value={sh.duration_ms / 1000} onChange={(e) => setSh({ ...sh, duration_ms: Math.round(parseFloat(e.target.value) * 1000) })} /></label>
        <label className="col small">机位 / 景别<input value={sh.camera ?? ""} onChange={(e) => setSh({ ...sh, camera: e.target.value })} /></label>
        <label className="col small">生成段<select value={sh.segment} onChange={(e) => setSh({ ...sh, segment: e.target.value })}>{version.content.segments.map((g: any) => <option key={g.id}>{g.id}</option>)}
          <option value="__new">＋ 新建生成段</option></select></label>
      </div>
      <label className="col small" style={{ marginTop: 8 }}>动作<textarea value={sh.action} onChange={(e) => setSh({ ...sh, action: e.target.value })} /></label>
      <div className="grid2" style={{ marginTop: 8 }}>
        {frameFields.map(([field, label]) =>
          <label key={field} className="col small">{label}
            <select aria-label={label} value={sh[field] ?? ""} onChange={(e) => setSh({ ...sh, [field]: e.target.value || null })}>
              <option value="">待绑定本镜构图</option>
              {ctx.assets.filter((a: any) => a.kind === "image").map((a: any) =>
                <option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}
            </select>
          </label>)}
      </div>
      <label className="col small">切点与接续<input value={sh.continuity ?? ""} onChange={(e) => setSh({ ...sh, continuity: e.target.value })} /></label>
      <label className="col small">动作窗接续方式<select disabled={first} value={first ? "start" : sh.continuation || "cut"}
        onChange={e => setSh({...sh, continuation:e.target.value})}>
        {first ? <option value="start">起镜</option> : <><option value="cut">切镜</option><option value="same">同镜延续</option></>}
      </select></label>
      <div className="grid2">{Object.entries({txtAnchor:"TXT 定位短句", holdReason:"长镜保留理由", attention:"注意对象", gaze:"视线", reaction:"可见反应", sound:"声音", cut:"剪点", device:"设备视点"}).map(([key, title]) =>
        <label className="col small" key={key}>{title}<input value={sh[key] ?? ""} onChange={e => setSh({...sh, [key]: e.target.value})}/></label>)}</div>
      <div className="row" style={{ marginTop: 8 }}>
        <span className="small muted">参考</span>
        <select value={ref?.bookmarkId ?? ""} onChange={(e) => setSh({ ...sh, refs: e.target.value ? [{ bookmarkId: e.target.value, mode: ref?.mode ?? "B", conditions: ref?.conditions ?? "" }] : [] })}>
          <option value="">原创（关联剧本出处）</option>{(bms.data ?? []).map((b) => <option key={b.id} value={b.id}>{b.shot_code} · v{b.pack_version} · {b.reason}</option>)}</select>
        {ref && <select value={ref.mode} onChange={(e) => setSh({ ...sh, refs: [{ ...ref, mode: e.target.value }] })}>{[["A", "A 保留"], ["B", "B 改造"], ["C", "C 仅功能"], ["D", "D 舍弃"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>}
        {!ref && <input placeholder="剧本出处场次" value={sh.origin?.scene ?? ""} onChange={(e) => setSh({ ...sh, origin: { ...(sh.origin ?? {}), scene: e.target.value } })} style={{ width: 120 }} />}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" onClick={() => {
          let segs = version.content.segments, next = sh;
          if (sh.segment === "__new") {
            const n = Math.max(...segs.map((g: any) => g.order)) + 1;
            const id = `G${String(n).padStart(2, "0")}`;
            segs = [...segs, { id, order: n, strategy: "flex" }]; next = { ...sh, segment: id };
          }
          save({ ...version.content, segments: segs, shots: shots.map((x: any) => x.id === sh.id ? next : x) }, `改 ${sh.id}`);
        }}>保存为新草稿</button>
        <button className="btn" onClick={() => { const n = Math.max(...shots.map((x: any) => x.order)) + 1; const id = `K${String(n).padStart(2, "0")}`;
          save({ ...version.content, shots: [...shots, { ...sh, id, order: n, action: "新镜头", refs: [] }] }, `新增 ${id}`); }}>在本段新增镜头</button>
        <button className="btn danger" onClick={()=>setConfirmDelete(true)}>删除镜头</button>
        {confirmDelete && <Modal onClose={()=>setConfirmDelete(false)}><h2>从新草稿删除镜头 {sh.id}？</h2>
          <button className="btn" autoFocus onClick={()=>setConfirmDelete(false)}>取消</button>
          <button className="btn danger" disabled={act.busy} onClick={()=>{setConfirmDelete(false);save({...version.content,shots:shots.filter((x:any)=>x.id!==sh.id)},`删除 ${sh.id}`);}}>确认删除镜头</button></Modal>}
      </div>
      <ErrorLine error={act.error} />
    </div>
  );
}
