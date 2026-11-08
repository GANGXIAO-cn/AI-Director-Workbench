import { budgetAmount, budgetCents } from "./budget";
import { PublishCues } from "./PublishCues";
import { MediaPreview, PromptMedia } from "./PromptMedia";
import { VersionSignals } from "./PromptSignals";
import { EpisodeSceneContact, OriginalZoom } from "./SceneContact";
import { UploadedMethods, canUseUploadedMethods } from "./UploadedMethods";
import { StoryComparison } from "./StoryComparison";
import { useDisplay } from "./DisplayPreferences";
import { ShotBoard, ReferenceReview } from "./ShotReferences";
import { OrderedImageInputs } from "./OrderedImageInputs";
import { RequiredImage } from "./RequiredImage";
import { useProjectRoute } from "./projectNavigation";
import { ProjectLibrary } from "./ProjectLibrary";
import { EpisodePaletteSettings, PaletteTrace, StepRequirements, VersionSourceText } from "./Resources";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { blob, cmd, deliveryFile, q, upload } from "./api";
import Assistant, { Selection, artifactFor } from "./Assistant";
import Canvas from "./Canvas";
import { ErrorLine, Modal, PROVIDER_LABEL, Pills, STAGES, Tag, fmtMs, rno, useAction, usePoll, useQuery } from "./ui";
import { PlanActing } from "./PlanActing";
import { ColumnDivider, savedWidths, type Widths } from "./WorkspaceColumns";
import { frameOrientation } from "./specs";

type Ctx = {
  view: any; eid: string; reload: () => Promise<void>; sel: Selection; setSel: (s: Selection) => void; assets: any[];
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
  const chatOnly = focusChat && !canvasMode && stage !== "package";
  const [packagePanel,setPackagePanel]=useState<HTMLElement|null>(null);
  useEffect(() => {
    if (!active || !chatOnly) return;
    const exit = (e:KeyboardEvent) => {
      if (e.key==="Escape" && !e.defaultPrevented && !e.isComposing && !document.querySelector(".desktop-page:not([hidden]) .scrim")) {
        e.preventDefault(); setFocusChat(false);
      }
    };
    window.addEventListener("keydown",exit);
    return ()=>window.removeEventListener("keydown",exit);
  },[active,chatOnly,setFocusChat]);
  const focus = new URLSearchParams(hash.split("?")[1]).get("v");
  const pendingStoryRun = new URLSearchParams(hash.split("?")[1]).get("run");
  const view = useQuery<any>("episode_view", { episode_id: id },[stage,pendingStoryRun]);
  const assets = useQuery<any[]>("episode_assets", { episode_id: id },[stage]);
  const batches = useQuery<any[]>("episode_batches", { episode_id: id },[stage]);
  const [sel, setSel] = useState<Selection>(null);
  const [cols, setCols] = useState<Widths>(savedWidths);
  useEffect(()=>{try {localStorage.setItem("ws-cols", JSON.stringify(cols));} catch { /* storage disabled */ }}, [cols]);
  const [batch, setBatch] = useState<any>(null);
  const expertParam = new URLSearchParams(hash.split("?")[1]).get("expert");
  const [showPalettes, setShowPalettes] = useState(false);
  const [showMethod, setShowMethod] = useState(false);
  const [requirements,setRequirements]=useState(false);
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
  const versionStage = stage==="review" ? "prompts" : stage;
  const missingVersion = !!(view.data && focus && ["story","script","plan","prompts","publish"].includes(versionStage)
    && !view.data.artifacts.find((a:any)=>a.stage===versionStage)?.versions.some((version:any)=>version.id===focus));
  useEffect(()=>{ if (missingVersion) void view.reload(); },[missingVersion,focus,versionStage,id]);
  const busy = view.data?.artifacts.some((a: any) => a.tasks.some((t: any) => t.runs.some((r: any) => ["queued", "running"].includes(r.status))));
  const reload = async () => { await Promise.all([view.reload(), assets.reload(), batches.reload()]); };
  const imagesRunning=batches.data?.some(b=>["authorized","running"].includes(b.state));
  usePoll(!!busy || !!imagesRunning, reload);
  if (!view.data) return <div className="page">{view.error ?? "读取中…"}</div>;
  const v = view.data;
  if (!v.kickoff) return <div className="page">本集尚未开工。<a className="btn primary" href={`#/p/${v.episode.project_id}`}>去开工</a></div>;
  if (missingVersion) {
    return <div className="page">当前阶段的版本列表中没有此稿。
      <button className="btn" onClick={view.reload}>刷新版本列表</button><button className="btn" onClick={()=>setFocus(null)}>返回当前稿</button></div>;
  }
  const ctx: Ctx = { view: v, eid: id, reload, sel, setSel, assets: assets.data ?? [], batches: batches.data ?? [], focus, setFocus,
    openBatch: setBatch };
  const adopted = (s: string) => !!v.artifacts.find((a: any) => a.stage === s)?.adopted_version_id;
  const status = (s: string) => {
    const map: Record<string, boolean> = { story: adopted("story"), script: adopted("script"), plan: adopted("plan"),
      ...v.productionStatus, prompts: adopted("prompts"),
      publish: adopted("publish"), delivery: v.episode.state === "delivered" };
    return map[s] ? "done" : s === stage ? "now" : "";
  };
  return (
    <div className="app">
      <div className={"ws"+(chatOnly?" chat-only":"")+(canvasMode?" canvas-only":"")} style={{flex:1, gridTemplateColumns: canvasMode || chatOnly ? "minmax(0, 1fr)" : `${cols[0]}px minmax(0, 1fr) ${cols[1]}px`}}>
        {!canvasMode && <div className="catalog">
          <div className="spread" style={{ padding: "0 8px 12px" }}><b>目录</b><a className="muted" href={`#/p/${v.episode.project_id}`}>⇤</a></div>
          {STAGES.map(([k, label], i) => (
            <a key={k} className={`item ${k === stage ? "on" : ""}`} href={`#/e/${id}/${k}`}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>{label}<span className={`st ${status(k)}`} />
            </a>))}
        </div>}
        {!canvasMode && <><ColumnDivider side={0} width={cols[0]} resize={width=>setCols(old=>[width,old[1]])}/>
          <ColumnDivider side={1} width={cols[1]} resize={width=>setCols(old=>[old[0],width])}/></>}
        <div className="center">
          <div className="topbar">
            {!canvasMode && stage!=="package" && <button className="btn narrow-chat" onClick={()=>setFocusChat(true)}>专注对话</button>}
            {["assets","prompts"].includes(stage) && <button className="btn" onClick={() => setShowPalettes(true)}>本集色卡</button>}
            {stage !== "package" && <button className="btn" onClick={() => setShowMethod(true)}>制作方法</button>}
            {["story","script","plan","prompts","publish"].includes(stage) && <button className="btn" onClick={()=>setRequirements(true)}>本步需求与参考</button>}
            <span className="title">{v.episode.display_code} · {v.episode.title || "未命名"}</span>
            <span className="muted small">{frameOrientation(v.kickoff.spec.aspectRatio)}</span>
            <span style={{ marginLeft: "auto" }} className="chip">◐ {v.kickoff.relation === "continue" ? "承接前集" : "独立成集"}</span>
            <span className="chip">▯ {v.kickoff.spec.aspectRatio}</span>
            <a className="chip" href={`#/p/${v.episode.project_id}/expert${expertParam ? "/" + expertParam : ""}?episode=${id}`}>⇄ 手工 · 专家模式</a>
          </div>
          {stage==="package"?<PackageView ctx={ctx} panel={packagePanel}/>:<StageView stage={stage} ctx={ctx} />}
        </div>
        {stage==="package" && <aside className="assistant package-aside" aria-label="出包设置" ref={setPackagePanel}/>}
        {!canvasMode && stage!=="package" && <Assistant key={id + ":" + stage} composeRef={composeRef} view={v} stage={stage} sel={sel} setSel={setSel} reload={reload} focus={focus} batches={ctx.batches} onOpenBatch={setBatch} />}
      </div>
      {showPalettes && <EpisodePaletteSettings key={stage} eid={id} stage={stage} close={() => setShowPalettes(false)}/>}
      {showMethod && <StageMethods key={id+stage} pid={v.episode.project_id} eid={id} stage={stage} close={() => setShowMethod(false)} />}
      {requirements && <StepRequirements key={stage} pid={v.episode.project_id} eid={id} stage={stage} close={()=>setRequirements(false)} toComposer={text=>{composeRef.current?.(text); if (window.matchMedia("(max-width: 1120px)").matches) setFocusChat(true);}}/>}
      {batch && <BatchModal batch={batch} assets={ctx.assets} close={() => { setBatch(null); reload(); }} />}
    </div>
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
    {data.data && <><p className="muted small">开工版本 {data.data.ref}</p>
      {!data.data.sections.length && <p>此版本未收录本阶段的小节。</p>}
      {!!data.data.guides?.length && <details className="card"><summary>查阅 H3 指南原文</summary>
        {data.data.guides.map((guide:{name:string;text:string;methodSource:{sha256:string}})=>
          <details key={guide.name}><summary>{guide.name==="ref-en.txt"?"全参考指南":"文本与关键帧指南"} · {guide.name}</summary>
            <p className="muted tiny">SHA-256 {guide.methodSource.sha256}</p><pre style={{whiteSpace:"pre-wrap"}}>{guide.text}</pre></details>)}</details>}
      {data.data.sections.map((item: any) => <details className="card" key={item.name}>
        <summary>{item.name.replace(/\.md$/, "")} · {item.role === "method" ? "通用" : "项目资料"}</summary>
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
  switch (stage) {
    case "story": return <StoryView ctx={ctx} />;
    case "script": return <ScriptView ctx={ctx} />;
    case "plan": return <PlanView ctx={ctx} />;
    case "space": return <SpaceView ctx={ctx} />;
    case "cast": return <CastView ctx={ctx} />;
    case "assets": return <AssetsView ctx={ctx} />;
    case "prompts": return <PromptsView ctx={ctx} />;
    case "review": return <ReviewView ctx={ctx} />;
    case "publish": return <PublishView ctx={ctx} />;
    case "delivery": return <DeliveryView ctx={ctx} />;
    default: return <PackageView ctx={ctx} />;
  }
}

// ---------------------------------------------------------------- common pieces
const PREV: Record<string, string> = { script: "story", plan: "script", prompts: "plan", publish: "prompts" };
function useVersion(ctx: Ctx, stage: string) {
  const art = artifactFor(ctx.view, stage);
  const upstreamAdopted = !!PREV[stage] && !!ctx.view.artifacts.find((a: any) => a.stage === PREV[stage])?.adopted_version_id;
  useEffect(() => { if (!art && upstreamAdopted) cmd("open_stage", { episode_id: ctx.eid, stage }).then(ctx.reload).catch(() => null); }, [art?.id, upstreamAdopted]);
  const versions = art?.versions ?? [];
  const current = versions.find((v: any) => v.id === ctx.focus) ?? versions.find((v: any) => v.id === art?.adopted_version_id) ?? versions[versions.length - 1];
  return { art, versions, current };
}

function VersionTabs({ ctx, art, current }: { ctx: Ctx; art: any; current: any }) {
  const [show, setShow] = useState(false);
  const act=useAction();
  const refs=useQuery<any>(show && current ? "version_sources":null,{version_id:current?.id});
  return <div className="row">{art.versions.map((v: any) => (
    <button key={v.id} className={`pill ${v.id === current?.id ? "on" : ""}`} onClick={() => ctx.setFocus(v.id)}>
      {rno(v.display_no)}{v.origin === "manual" ? " 手稿" : v.origin === "combined" ? " 组合" : ""}{art.adopted_version_id === v.id ? " ✓" : ""}</button>))}
    <button className="btn" onClick={()=>setShow(true)}>版本来源</button>
    {current && current.id!==art.versions[art.versions.length-1]?.id && <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
      const r=await cmd("restore_version",{version_id:current.id,expected_adopted_version_id:art.adopted_version_id});
      await ctx.reload(); ctx.setFocus(r.versionId);})}>恢复此完整稿为新版本</button>}<ErrorLine error={act.error}/>
    {show && <Modal width={760} onClose={()=>setShow(false)}><h2>{rno(current.display_no)} 的来源</h2>
      {refs.error && <p className="error">{refs.error}</p>}
      {refs.data && <><p>来源 {refs.data.origin} {refs.data.base ? " · 基版 "+refs.data.base : ""} {refs.data.run ? " · 运行 "+refs.data.run : ""}</p>
        {refs.data.inputs.length===0 && <p>这个版本没有直接读取外部参考的运行记录。</p>}
        {refs.data.inputs.map((item:any)=><details key={item.sha256+item.name}><summary>{item.name} · {({citation:"引用资料",visual_reference:"导演色卡",method:"制作方法",project_method:"项目方法",step_requirements:"本步需求",reference_pack:"参考包",adaptation_source:"改编来源"} as Record<string,string>)[item.role] || item.role}</summary><small>输入文件 SHA-256 {item.sha256}</small>{!refs.data.run && <p className="muted small">来源稿的运行 · {item.sourceRuns?.join("、")}</p>}<VersionSourceText role={item.role} text={item.text}/></details>)}</>}
      <button className="btn" onClick={()=>setShow(false)}>关闭</button></Modal>}</div>;
}

function AdoptBar({ ctx, art, version, hint, next }: { ctx: Ctx; art: any; version: any; hint: string; next?: string }) {
  const { go } = useProjectRoute();
  const act = useAction();
  if (!version) return null;
  const adoptedHere = art.adopted_version_id === version.id;
  const adopt = (cont: boolean) => act.run(async () => {
    const res = await cmd("adopt", { artifact_id: art.id, version_id: version.id, expected_adopted_version_id: art.adopted_version_id, and_continue: cont });
    ctx.reload();
    if (cont && next) go(`#/e/${ctx.eid}/${next}`);
    return res;
  });
  return (
    <div className="actionbar">
      <span className="muted small">· {hint}</span>
      {!adoptedHere && art.adopted_version_id && <button className="btn" onClick={() => ctx.setFocus(art.adopted_version_id)}>对比 {rno(art.versions.find((v: any) => v.id === art.adopted_version_id)?.display_no)}</button>}
      {art.stage === "story" && !version.gate.review && <button className="btn primary" onClick={() => act.run(async () => { await cmd("submit_review", { version_id: version.id }); ctx.reload(); })}>发起独立审查</button>}
      {!adoptedHere && (art.stage !== "story" || version.gate.review) && <>
        <button className="btn" disabled={!version.gate.adoptable} onClick={() => adopt(false)}>只采用</button>
        {next && <button className="btn primary" disabled={!version.gate.adoptable} onClick={() => adopt(true)}>采用并继续</button>}
      </>}
      {adoptedHere && <Tag tone="green">{rno(version.display_no)} 已采用</Tag>}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 01 故事稿 (J06, J36, J54, J59, J60)
function storyText(value:unknown):string {
  if (typeof value==="string") return value;
  return value==null ? "" : JSON.stringify(value,null,2);
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
  const [tab, setTab] = useState(requestedPair ? "compare" : "synopsis");
  const [pairSource, setPairSource] = useState(requestedPair);
  useEffect(()=>{if (requestedPair) {setPairSource(requestedPair);setTab("compare");}},[hash,requestedPair]);
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState<any>({});
  const act = useAction();
  useEffect(() => { setDraft(current?.content ?? {}); setEdit(false); }, [current?.id]);
  const editId=params.get("edit")==="1" ? params.get("v") : null;
  useEffect(()=>{if (editId && current?.id===editId) {setEdit(true);setTab("synopsis");}},[hash,editId,current?.id]);
  const k = ctx.view.kickoff;
  if (requestedRun) return <div><h1>改编故事稿</h1><p role="status">{result ? "正在打开新稿…" :
    run && ["queued","running"].includes(run.status) ? "改编稿生成中…" :
    run?.reason || (run ? "本次运行尚无改编稿。" : "本集未找到这次改编任务。")}</p>
    <a href={`#/e/${ctx.eid}/story`}>查看本集故事稿</a></div>;
  if (!current) return (
    <div><div className="stagehead"><div><h1>故事稿</h1><div className="muted small">尚无稿件 · 来源：{k.source_type === "import" ? ctx.view.sourceImport?.original_name : k.idea_text ?? "选题卡"}</div></div></div>
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
        <VersionTabs ctx={ctx} art={art} current={current} />
      </div>
      <div className="spread"><div className="tabs">{[["synopsis", "故事梗概"], ["cast", "角色表"], ["handoff", "交接说明"], ["notes", "开发辅助"], ["review", "独立审查"], ["compare","并排出稿"], ...(ctx.view.sourceImport ? [["source", "来源原文"]] : [])].map(([t, l]) => (
        <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => {if(t==="compare") setPairSource(art.comparisons?.find((c:any)=>c.version_id===current.id)?.source_version_id || current.id);setTab(t);}}>{l}</button>))}</div>
        {tab!=="compare" && <div className="row">{edit ? <><button className="btn" onClick={() => setEdit(false)}>取消</button>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current.id, content: draft }); await ctx.reload(); ctx.setFocus(r.versionId); })}>保存为手稿</button></>
          : <button className="btn" onClick={() => {setDraft(current.content);setEdit(true);}}>✎ 编辑</button>}</div>}</div>
      {tab === "synopsis" && (edit ? <textarea style={{ minHeight: 260, marginTop: 12 }} value={storyText(draft.synopsis)} onChange={(e) => setDraft({ ...draft, synopsis: e.target.value })} /> : (
        <div className="field" style={{ marginTop: 12, border: "1px solid #2b5fb8" }}>{paras.map((p: string, i: number) => (
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
      {tab==="compare" && <StoryComparison key={pairSource || current.id} sourceId={pairSource || current.id} select={setPairSource}
        plan={k.executor_plan} art={art} reload={ctx.reload} open={(vid:string)=>{setTab("synopsis");go("#/e/"+ctx.eid+"/story?v="+vid);}}/>}
      {current.gate.formatErrors.length > 0 && <div className="card red" style={{ marginTop: 12 }}><b className="error">结构错误（不能放行）</b><ul className="small">{current.gate.formatErrors.map((e: string) => <li key={e}>{e}</li>)}</ul></div>}
      {tab!=="compare" && (tab === "review" || review) && <StoryReview ctx={ctx} version={current} review={review} />}
      <ErrorLine error={act.error} />
      {tab!=="compare" && <AdoptBar ctx={ctx} art={art} version={current} next="script"
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
      <div className="spread"><h2>故事独立审查 · 第 {review.pass_no} 遍</h2>
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
          {i.setting_content && <div className="grid2" style={{ margin: "8px 0" }}><div className="field small">设定原文：{i.setting_content}</div><div className="field small" style={{ background: "var(--red-bg)" }}>冲突句：{i.loc.evidence}</div></div>}
          <div className="small" style={{ margin: "6px 0" }}>{i.reason}</div>
          {i.blocking ? <div className="row"><button className="btn" onClick={() => ctx.setSel({ label: `${i.loc.anchor}（审查意见）`, data: { issue: i.id, anchor: i.loc.anchor } })}>改故事</button>
            <a className="btn" href={`#/p/${ctx.view.episode.project_id}/settings`}>去项目设定</a><span className="muted tiny">没有放行入口</span></div>
            : i.waiver_reason ? <div className="ok small">已放行（仅本版本）：{i.waiver_reason}</div>
              : <div className="row"><input style={{ flex: 1 }} placeholder="写理由放行（绑定本版本，改动后失效）" value={reasons[i.id] ?? ""} onChange={(e) => setReasons({ ...reasons, [i.id]: e.target.value })} />
                <button className="btn" onClick={() => act.run(async () => { await cmd("waive", { issue_id: i.id, version_id: version.id, reason: reasons[i.id] ?? "" }); ctx.reload(); })}>写理由放行</button>
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

function ScriptView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "script");
  const [tab, setTab] = useState("full");
  const [cmp, setCmp] = useState<any>(null);
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [editScenes, setEditScenes] = useState<any[] | null>(null);
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
  if (!art) return <div className="empty">故事未通过独审并采用前，不能写剧本。</div>;
  const story = artifactFor(ctx.view, "story");
  if (!current) return <div><div className="stagehead"><h1>剧本</h1></div>
    <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("create_task",{episode_id:ctx.eid,stage:"script",goal:"按采用的故事写分场剧本"});ctx.reload();})}>按采用的故事起草剧本</button><ErrorLine error={act.error}/></div>;
  const scenes = current.content.scenes ?? [];
  const baseV = current.id !== adoptedId ? art.versions.find((v: any) => v.id === (current.base_id ?? adoptedId)) : null;
  const baseScenes = baseV ? Object.fromEntries((baseV.content.scenes ?? []).map((x: any) => [x.id, x.action])) : null;
  const changedCount = baseScenes ? scenes.filter((x: any) => baseScenes[x.id] !== x.action).length : 0;
  const others = art.versions.filter((v: any) => v.id !== current.id && v.id !== adoptedId);
  return (
    <div>
      <div className="stagehead">
        <div><h1>剧本</h1><div className="muted small">{rno(current.display_no)} {current.id === adoptedId ? "已采用" : "草稿"}{current.base_id ? `（基于 ${rno(art.versions.find((v: any) => v.id === current.base_id)?.display_no)}）` : ""} · {baseScenes ? `改动 ${changedCount} 处 · 绿色新增 / 红色删除` : `${scenes.length} 场`}</div></div>
        <VersionTabs ctx={ctx} art={art} current={current} />
      </div>
      <div className="spread"><div className="tabs">{[["full", "全文"], ["outline", "阶段大纲"], ["budget", "分段与时长预算"], ["diff", "差异"], ["side", "并排比较"]].map(([t, l]) => (
        <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => t === "side" ? openSide() : setTab(t)}>{l}</button>))}</div>
        <div className="row">{editScenes ? <><button className="btn" onClick={() => setEditScenes(null)}>取消</button>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current.id, content: { ...current.content, scenes: editScenes } }); setEditScenes(null); await ctx.reload(); ctx.setFocus(r.versionId); })}>保存为手稿</button></>
          : <button className="btn" onClick={() => setEditScenes(JSON.parse(JSON.stringify(scenes)))}>✎ 编辑</button>}</div></div>
      {editScenes && editScenes.map((sc: any, i: number) => (
        <div key={i} style={{ marginTop: 12 }}><div className="row"><input value={sc.id} onChange={(e) => { const x = [...editScenes]; x[i] = { ...sc, id: e.target.value }; setEditScenes(x); }} style={{ width: 90 }} />
          <input value={sc.heading ?? ""} onChange={(e) => { const x = [...editScenes]; x[i] = { ...sc, heading: e.target.value }; setEditScenes(x); }} placeholder="内外景 · 时段" /></div>
          <textarea value={sc.action} onChange={(e) => { const x = [...editScenes]; x[i] = { ...sc, action: e.target.value }; setEditScenes(x); }} />
          <div className="grid2">{Object.entries(sceneDetails).map(([key, label]) => <label className="col small" key={key}>{label}
            <input value={sc[key] ?? ""} onChange={e => setEditScenes(editScenes.map((row: any, j: number) => j === i ? {...row, [key]: e.target.value} : row))}/></label>)}</div></div>))}
      {!editScenes && tab === "full" && scenes.map((s: any, i: number) => { const old = baseScenes?.[s.id]; return (
        <div key={s.id} style={{ marginTop: 18 }}>
          <h2>{i + 1}. {s.id} <span className="muted small">{s.heading}</span></h2>
          {old !== undefined && old !== s.action ? <div className={`para ${ctx.sel?.data.scene === s.id ? "sel" : ""}`} style={{ lineHeight: 1.8 }} onClick={() => ctx.setSel({ label: `${s.id}（差异处）`, data: { scene: s.id } })}>{charDiff(old, s.action).map((d, k) => d.op === "same" ? <span key={k}>{d.text}</span> : d.op === "del" ? <del key={k} className="d">{d.text}</del> : <ins key={k} className="d">{d.text}</ins>)}</div> :
          s.action.split(/(?<=。)/).filter(Boolean).map((p: string, j: number) => (
            <div key={j} className={`para ${ctx.sel?.data.scene === s.id && ctx.sel?.data.sentence === j + 1 ? "sel" : ""}`} onClick={() => ctx.setSel({ label: `${s.id} 第${j + 1}句`, data: { scene: s.id, sentence: j + 1 } })}>{p}</div>))}
          {Object.entries(sceneDetails).map(([key, title]) => s[key] && <div key={key} className="small" onClick={() => ctx.setSel({label: s.id + " " + title, data: {scene: s.id, field: key}})}><b>{title}</b> {s[key]}</div>)}
          {s.source && <div className="muted tiny">出处：故事 {rno(story?.versions.find((v: any) => v.id === s.source.storyVersion)?.display_no)} · 节拍 {s.source.beat} · 原镜 {s.source.refShot}{s.source.pack ? ` · 参考包 ${s.source.pack}` : ""}</div>}
        </div>); })}
      {tab === "budget" && !editScenes && <div>
        {scenes.filter((s: any) => s.proposedSegments || s.watchPurpose || s.omit || s.budgetNote).map((s: any) => <div className="card" key={s.id}><b>{s.id} · {s.heading}</b>
          {["proposedSegments", "watchPurpose", "omit", "budgetNote"].map(key => s[key] && <p key={key}>{sceneDetails[key]} · {s[key]}</p>)}</div>)}
        {current.id === adoptedId ? <ScriptBudget ctx={ctx} script={current} /> : <p className="muted">切换到已采用剧本可查看与制作方案的对应关系。</p>}
      </div>}
      {tab === "outline" && <table className="table" style={{ marginTop: 12 }}><tbody>{scenes.map((s: any) => <tr key={s.id}><td style={{ width: 80 }}>{s.id}</td><td className="muted">{s.heading}</td><td>{s.action.slice(0, 60)}…</td></tr>)}</tbody></table>}
      {tab === "diff" && (cmp ? <div className="field" style={{ marginTop: 12 }}>{(cmp.fields.scenes ?? []).map((d: any, i: number) => (
        d.op === "same" ? <span key={i}>{d.text}</span> : d.op === "del" ? <del key={i} className="d">{d.text}</del> : <ins key={i} className="d">{d.text}</ins>))}</div>
        : <div className="muted" style={{ marginTop: 12 }}>当前版本即采用版，没有差异。</div>)}
      {tab === "side" && (
        <div style={{ marginTop: 12 }}>
          <div className="muted small">并排显示两份草稿及共同基版；可保留任一稿，或逐项组合成新的待审稿。关闭比较保留草稿。</div>
          <div className="grid2" style={{ marginTop: 10 }}>{[current, ...others.slice(-1)].map((v: any) => (
            <div key={v.id} className="card line"><div className="spread"><b>{rno(v.display_no)} · {v.origin === "manual" ? "手工稿" : v.origin === "combined" ? "组合稿" : "AI 稿"}</b>
              <div className="row">{["scenes", "notes"].map((f) => <button key={f} className={`pill ${picks[f] === v.id ? "on" : ""}`} onClick={() => setPicks({ ...picks, [f]: v.id })}>取{f === "scenes" ? "场次" : "说明"}</button>)}</div></div>
              {(v.content.scenes ?? []).map((s: any) => <div key={s.id} className="small" style={{ margin: "6px 0" }}><b>{s.id}</b> {s.action}</div>)}
              {v.content.notes && <div className="muted small">说明：{v.content.notes}</div>}</div>))}</div>
          <div className="row" style={{ marginTop: 10 }}><button className="btn primary" disabled={Object.keys(picks).length === 0} onClick={() => act.run(async () => {
            const r = await cmd("combine", { artifact_id: art.id, picks }); await ctx.reload(); ctx.setFocus(r.versionId); setTab("full"); })}>组合成新的待审稿</button>
            <button className="btn" onClick={closeSide}>关闭比较</button></div>
        </div>)}
      <ErrorLine error={act.error} />
      <AdoptBar ctx={ctx} art={art} version={current} next="plan" hint="采用前先看影响 · 重复点击不会生成重复版本" />
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
  const [trace, setTrace] = useState<any>(null);
  const act = useAction();
  const lay = useMemo(() => layout(current?.content), [current?.id]);
  const still = (id?: string) => blob(ctx.assets.find((a) => a.id === id)?.sha256);
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
  if (!art) return <div className="empty">剧本采用后才能设计镜头。</div>;
  if (!current) return <div><div className="stagehead"><h1>制作方案</h1></div>
    <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("create_task",{episode_id:ctx.eid,stage:"plan",goal:"按采用剧本设计镜头、接点和生成段"});ctx.reload();})}>按剧本设计镜头与生成段</button><ErrorLine error={act.error}/></div>;
  const shots = current.content.shots ?? [];
  const scenes: string[] = [...new Set<string>(shots.map((s: any) => s.origin?.scene ?? "参考镜头"))];
  const over = lay.segments.filter((s: any) => s.duration > 15000);
  return (
    <div>
      <div className="stagehead">
        <div><h1>制作方案</h1><div className="muted small">{rno(current.display_no)} {current.id === art.adopted_version_id ? "已采用" : "草稿"} · {shots.length} 镜 · {lay.segments.length} 段 · {Math.round(lay.total / 1000)} 秒 · 镜头时长加总等于段长</div></div>
        <div className="tabs">{[["shots", "镜头"], ["segments", "生成段"], ["acting", "表演与台词读秒"], ["table", "表格"]].map(([t, l]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div>
      </div>
      <VersionTabs ctx={ctx} art={art} current={current} />
      {over.map((s: any) => (
        <div key={s.id} className="card amber" style={{ marginTop: 10 }}>
          <b>{s.id} 共 {s.duration / 1000} 秒，超过 15 秒</b><span className="muted small"> · 须在动作完成处拆段，修复前阻断交付</span>
          <div className="row" style={{ marginTop: 8 }}>{s.shots.slice(1).map((sid: string) => (
            <button key={sid} className="btn" onClick={() => act.run(async () => { const r = await cmd("split_segment", { version_id: current.id, segment_id: s.id, first_shot_of_second: sid }); await ctx.reload(); ctx.setFocus(r.versionId); })}>在 {sid} 前拆开</button>))}</div>
        </div>))}
      <RhythmReview version={current.id} save={async rhythmTable=>{
        const r=await cmd("save_manual",{artifact_id:art.id,base_version_id:current.id,content:{rhythmTable},note:"编辑镜头充分性联合表"});
        await ctx.reload(); ctx.setFocus(r.versionId);
      }}/>
      {tab === "acting" && <PlanActing key={current.id} version={current} episodeId={ctx.eid} assets={ctx.assets} onSave={async shots=>{
        const r = await cmd("save_manual", {artifact_id:art.id, base_version_id:current.id, content:{shots}, note:"更新表演与台词时长"});
        await ctx.reload(); ctx.setFocus(r.versionId);
      }}/>}
      {tab === "shots" && scenes.map((sc) => (
        <div key={sc}>
          <h2 style={{ marginTop: 18 }}>{sc}</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(180px,1fr))", gap: 10 }}>
            {shots.filter((s: any) => (s.origin?.scene ?? "参考镜头") === sc).map((s: any) => (
              <div key={s.id} className={`shotcard ${shot === s.id ? "sel" : ""}`} onClick={() => { setShot(s.id); ctx.setSel({ label: `镜头 ${s.id}（${s.segment}）`, data: { shot: s.id, segment: s.segment, version: current.id } }); }}>
                <div className="pic" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 4 }}>
                  {[s.startAssetId, s.endAssetId].some(id => still(id))
                    ? [s.startAssetId, s.endAssetId].map((id, i) => still(id) && <img key={i} src={still(id)} alt={i ? "落幅" : "起幅"} style={{ minWidth: 0, maxWidth: "50%", height: "100%", objectFit: "contain" }} />)
                    : <span className="muted tiny">待绑定本镜构图</span>}
                </div>
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
        <tbody>{lay.segments.map((s: any) => <tr key={s.id}><td>{s.id}</td><td><select value={s.strategy} onChange={(e) => act.run(async () => {
          const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current.id, note: `${s.id} 改为 ${e.target.value}`,
            content: { ...current.content, segments: current.content.segments.map((g: any) => g.id === s.id ? { ...g, strategy: e.target.value } : g) } });
          await ctx.reload(); ctx.setFocus(r.versionId); })}><option value="flex">flex</option><option value="grid">grid</option></select></td><td>{fmtMs(s.start)}–{fmtMs(s.end)}</td><td>{s.duration / 1000}s</td><td>{s.shots.join("、")}</td></tr>)}</tbody></table>}
      {tab === "table" && <table className="table" style={{ marginTop: 12 }}><thead><tr><th>镜</th><th>段</th><th>段内</th><th>机位</th><th>动作</th><th>出处</th></tr></thead>
        <tbody>{shots.map((s: any) => { const t = lay.shots[s.id]; const seg = lay.segments.find((x: any) => x.id === s.segment);
          return <tr key={s.id}><td>{s.id}</td><td>{s.segment}</td><td>{((t.start - seg.start) / 1000).toFixed(1)}–{((t.end - seg.start) / 1000).toFixed(1)}s</td><td>{s.camera}</td><td>{s.action}</td><td className="muted">{s.refs?.length ? "参考镜头" : s.origin?.scene}</td></tr>; })}</tbody></table>}
      <div className="segstrip">{lay.segments.map((s: any) => (
        <div key={s.id} className={s.shots.includes(shot) ? "on" : ""} style={{ flex: s.duration }}>{s.id} · {Math.round(s.duration / 100) / 10}s</div>))}</div>
      {shot && <ShotEditor ctx={ctx} art={art} version={current} shotId={shot} onSaved={(vid: string) => ctx.setFocus(vid)} />}
      {trace && (
        <div className="card line" style={{ marginTop: 14 }}>
          <b>{trace.shot.id} 出处</b>
          {trace.refs.map((r: any, i: number) => <div key={i} className="small">原片 {r.source.name} · 参考包 v{r.packVersion} · {r.refShot} {fmtMs(r.start_ms)}–{fmtMs(r.end_ms)} · 用法 {r.mode} · {r.reason}</div>)}
          {trace.origin && <div className="small">原创镜：剧本 {trace.origin.scene}「{trace.origin.line ?? ""}」</div>}
        </div>)}
      <ErrorLine error={act.error} />
      <AdoptBar ctx={ctx} art={art} version={current} next="prompts" hint="空间锁定通过后才能出图" />
    </div>
  );
}

// ---------------------------------------------------------------- 04 空间锁定 (J39)
type SpaceLock = {id:string; label:string; relation:string; segments:string[]; status:string; versionId:string|null; name:string|null;
  version:number|null; sha256:string|null; positions?:number[]};

function SpaceView({ ctx }: { ctx: Ctx }) {
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
      <div className="stagehead"><div><h1>空间锁定</h1><div className="muted small">逐条出处；未确认的用验证图，只阻塞依赖项</div></div></div>
      {items.data && !locks.some(l => l.status === "pending") && <div className="card"><span className="ok">没有待确认的空间关系</span>。</div>}
      {items.error && <p className="error">{items.error}</p>}
      {locks.map((t) => (
        <div key={t.id} className="card" style={{ marginBottom: 10 }}>
          <div className="row"><Tag tone={t.status==="pending"?"amber":"green"}>{t.status==="pending"?"待确认":"已确认"}</Tag><b>{t.label}</b> {t.segments.join("、")}</div>
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
        <input placeholder="依赖段，逗号分隔，例：G04" value={form.depends} onChange={(e) => setForm({ ...form, depends: e.target.value })} />
      </div>
      <button className="btn" style={{ marginTop: 10 }} onClick={() => act.run(async () => { await cmd("add_space_item", { episode_id: ctx.eid, label: form.label, relation: form.relation, source: form.source, depends: form.depends.split(/[,，、\s]+/).filter(Boolean) }); items.reload(); })}>登记（待确认）</button>
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
  const appearance = draft.appearance ?? baseline.appearance ?? "";
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
      <div className="stagehead"><div><h1>角色出镜与身份覆盖</h1><div className="muted small">逐段绑定；grid 段的交付输入只取本段一张九宫格，缺口可修格或改 flex</div></div>
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
        <label>外观短描述 <input value={appearance} onChange={e=>editBaseline({appearance:e.target.value})} /></label>
        <div><button className="btn" disabled={act.busy || !identityId || !appearance.trim()} onClick={()=>act.run(async()=>{
          await cmd("set_role_baseline",{episode_id:ctx.eid,role:focused,asset_version_id:identityId,appearance});
          m.reload();ctx.reload();
        })}>保存本集身份基准</button></div></div>}
      <table className="table"><thead><tr><th>段</th><th>策略</th>{roles.map((r) => <th key={r}>{r}</th>)}</tr></thead>
        <tbody>{m.data.rows.map((row: any) => (
          <tr key={row.segment}><td><b>{row.segment}</b></td><td>{row.strategy}</td>
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
                {c?.options?.map((o:string)=><a key={o} href={o.includes("flex") ? `#/e/${ctx.eid}/plan` : `#/e/${ctx.eid}/assets`}>{o}</a>)}</div></td>); })}
          </tr>))}</tbody></table>
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 06 素材与选图 (J10, J14, J15, J18, J27, J41)
function adoptedReferences(ctx:Ctx): Record<string,{refs:{assetVersionId:string;label:string;kind:string}[]}> {
  const prompts=artifactFor(ctx.view,"prompts");
  return prompts?.versions.find((v:any)=>v.id===prompts.adopted_version_id)?.content?.segments ?? {};
}

function AssetsView({ ctx }: { ctx: Ctx }) {
  const { hash, go } = useProjectRoute();
  const tab = new URLSearchParams(hash.split("?")[1]).get("tab") ?? "round";
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
  const act = useAction();
  const images = ctx.assets.filter((a) => a.kind === "image").sort((a, b) => (b.grid ? 1 : 0) - (a.grid ? 1 : 0));
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
  const imgStyle = mode === "灰度" ? { filter: "grayscale(1)" } : mode === "手机尺寸" ? {width:180,aspectRatio:"9/16",objectFit:"contain" as const,background:"#08090b",margin:"auto",border:"3px solid #494b54",borderRadius:16} : {};
  return (
    <div className="asset-stage">
      <div className="stagehead">
        <div><h1>素材与选图</h1><div className="muted small">{images.length} 张 · 提示词已引用 {cited.size} 张 · 手动已采用 {images.filter((a) => marked(a, "adopted")).length} · 本集图像批次 {ctx.batches.length}</div></div>
        <div className="row"><div className="tabs">{[["needs", "需求"], ["round", "本轮出图"], ["backup", "补选区"], ["canvas", "画布"]].map(([t, l]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div>
          <button className="btn primary" onClick={() => setNewBatch({ operation: "generate" })}>出图</button>
          <select value={upKind} onChange={(e) => setUpKind(e.target.value)} style={{ padding: "4px 6px" }}><option value="state">场面 / 状态图</option><option value="grid">九宫格</option><option value="identity">身份图</option><option value="costume">服装参考</option><option value="scene">空景</option></select>
          <label className="btn">上传<input type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && act.run(async () => { await upload("asset", e.target.files![0], { projectId: ctx.view.episode.project_id, episodeId: ctx.eid, category: upKind, grid: upKind === "grid" }); ctx.reload(); })} /></label></div>
      </div>
      <ProjectLibrary pid={ctx.view.episode.project_id} eid={ctx.eid} productsOnly refresh={ctx.reload} images={images} shots={artifactFor(ctx.view,"plan")?.versions.find((v:any)=>v.id===artifactFor(ctx.view,"plan")?.adopted_version_id)?.content.shots ?? []}/>
      {tab === "canvas" ? <Canvas ctx={ctx} providers={providers.data ?? {}} openBatch={ctx.openBatch} /> : tab === "needs" ? <NeedsTable ctx={ctx} /> : (
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
                {!!a.grid && <button className="btn" onClick={() => setNewBatch({ operation: "edit", base: a })}>修一格</button>}
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
        </div><OriginalZoom name={detail.name} sha={detail.sha256}/>{!!detail.grid && <GridDetail a={detail} eid={ctx.eid}/>}</Modal>}
      {ctx.batches.length > 0 && <h2 style={{ marginTop: 24 }}>图像批次</h2>}
      {ctx.batches.map((b) => (
        <div key={b.id} className="card line" style={{ marginBottom: 8 }}>
          <div className="spread"><span>{b.provider} · {b.operation === "edit" ? "局部重画" : "生成"} · {b.count} 张 · {b.size} · {BATCH_LABEL[b.state] ?? b.state}</span><button className="btn" onClick={() => ctx.openBatch(b)}>打开</button></div>
          <div className="row small" style={{ marginTop: 6 }}>{b.requests.map((r: any) => <Tag key={r.id} tone={r.state === "succeeded" ? "green" : r.state === "unknown" ? "amber" : r.state === "failed" ? "red" : ""}>#{r.ordinal} {REQ[r.state] ?? r.state}</Tag>)}</div>
        </div>))}
      {newBatch && <NewBatch ctx={ctx} init={newBatch} providers={providers.data ?? {}} close={() => { setNewBatch(null); ctx.reload(); }} />}
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
  return <><table className="table"><thead><tr><th>段</th><th>策略</th><th>镜号</th><th>素材用途</th><th>缺口</th></tr></thead>
    <tbody>{lay.segments.map((s: any) => { const used=refs[s.id]?.refs ?? [];const has=used.length>0 && used.every(r=>ctx.assets.some(a=>a.id===r.assetVersionId));
      return <tr key={s.id}><td>{s.id}</td><td>{s.strategy}</td><td>{s.shots.join("、")}</td><td>{s.strategy === "grid" ? "本段九宫格" : "角色 / 场景 / 道具参考"}</td><td>{has ? <Tag tone="green">已引用 {used.length} 份</Tag> : <Tag tone="amber">待绑定参考</Tag>}</td></tr>; })}</tbody></table>
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

function GridDetail({ a, eid }: { a: any; eid:string }) {
  return (
    <div className="card line" style={{ marginTop: 14 }}>
      <b>九宫格详情 · {a.name} v{a.version_no}</b>{a.changed_cells && <span className="muted small"> · 请求修改第 {a.changed_cells.join("、")} 格</span>}
      <GridMethods eid={eid}/>
      {a.source_version_id && <GridComparison key={a.id} id={a.id} />}
      <div style={{ display: "grid", gridTemplateColumns:"repeat(3,1fr)", gap: 6, marginTop: 10 }}>
        {(a.cells ?? Array.from({ length: 9 }, (_, i) => ({ cell: i + 1 }))).map((c: any) => (
          <div key={c.cell} className={`card ${a.changed_cells?.includes(c.cell) ? "amber" : "line"}`} style={{ padding: 8 }}>
            <div role="img" aria-label={`第 ${c.cell} 格`} style={{aspectRatio:`${a.width||1}/${a.height||1}`,backgroundImage:`url(${blob(a.sha256)})`,backgroundSize:"300% 300%",backgroundPosition:`${(c.cell-1)%3*50}% ${Math.floor((c.cell-1)/3)*50}%`}}/><b>第 {c.cell} 格</b><div className="muted tiny">{c.moment || "未登记时刻"}</div></div>))}
      </div>
    </div>
  );
}

function GridComparison({ id }: { id: string }) {
  const cmp = useQuery<any>("compare_cells", { new_version_id: id });
  if (!cmp.data) return <ErrorLine error={cmp.error ? { message: cmp.error } : null} />;
  if (!cmp.data.comparable) return <p className="small">底图与返回图无法按相同尺寸逐格比较。</p>;
  return <p className="small">像素有变化的格：{cmp.data.changed.join("、") || "无"} · 像素完全一致的格：{cmp.data.unchanged.join("、") || "无"}</p>;
}

function NewBatch({ ctx, init, providers, close }: { ctx: Ctx; init: any; providers: any; close: () => void }) {
  const names = Object.keys(providers);
  const [provider, setProvider] = useState(names[0] ?? "");
  const [prompt, setPrompt] = useState(init.base ? `局部重画 ${init.base.name}` : "");
  const [count, setCount] = useState(1);
  const [size, setSize] = useState("");
  const [quality, setQuality] = useState("low");
  const [cells, setCells] = useState<number[]>(init.base ? [5] : []);
  const [ref, setRef] = useState("");
  const [budget, setBudget] = useState("2.00");
  const [segs, setSegs] = useState("");
  const locks = useQuery<SpaceLock[]>("space_locks", {episode_id:ctx.eid});
  const scoped = segs.split(/[,，、\s]+/).filter(Boolean);
  const relevant = (locks.data || []).filter(l => l.status==="confirmed" && l.versionId && l.segments.some(s=>scoped.includes(s)));
  const [chosen,setChosen] = useState<string[]>([]);
  const ordered = chosen.filter(id=>relevant.some(l=>l.versionId===id));
  const act = useAction();
  const caps = providers[provider]?.capabilities;
  useEffect(() => { if (caps && !caps.sizes.includes(size)) setSize(caps.sizes[0]); }, [provider]);
  const create = () => act.run(async () => {
    const budget_cents = budgetCents(budget);
    const r = init.base ? await cmd("redraw_cells", { episode_id: ctx.eid, base_version_id: init.base.id, cells, reference_version_ids: Array.from(new Set([ref,...ordered].filter(id=>id && id!==init.base.id))), segments:scoped, prompt, provider, size, quality, budget_cents })
      : await cmd("create_batch", { episode_id: ctx.eid, provider, operation: "generate", size, quality, budget_cents,
        items: Array.from({ length: count }, (_, i) => ({ prompt, segments: scoped, inputs: ordered, target: { name: `${prompt.slice(0, 10)} ${i + 1}`, category: "state" } })) });
    ctx.openBatch(await q("batch_view", { batch_id: r.batchId }));
    close();
  });
  return (
    <Modal onClose={close} width={620}>
      <div className="row"><Tag tone="amber">会消耗额度</Tag><h2 style={{ margin: 0 }}>{init.base ? `局部重画 ${init.base.name}` : "发起图像批次"}</h2></div>
      <div className="formrow"><span className="muted">通道</span><select value={provider} onChange={(e) => setProvider(e.target.value)}>{names.map((n) => <option key={n} value={n}>{n}（{providers[n].verified === "simulated" ? "模拟" : providers[n].verified === "pending_verification" ? "待验证" : "已验证"}）</option>)}</select></div>
      <div className="formrow"><span className="muted">要求</span><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} /></div>
      {init.base ? <>
        <GridMethods eid={ctx.eid}/>
        <div className="formrow"><span className="muted">只改格</span><div className="row">{Array.from({ length: 9 }, (_, i) => i + 1).map((c) => <button key={c} className={`pill ${cells.includes(c) ? "on" : ""}`} onClick={() => setCells(cells.includes(c) ? cells.filter((x) => x !== c) : [...cells, c])}>{c}</button>)}</div></div>
        <div className="formrow"><span className="muted">参考（仅绘图）</span><select value={ref} onChange={(e) => setRef(e.target.value)}><option value="">无</option>{ctx.assets.filter((a) => a.kind === "image" && a.id !== init.base.id).map((a) => <option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select></div>
      </> : <>
        <div className="formrow"><span className="muted">张数</span><input type="number" min={1} max={9} value={count} onChange={(e) => setCount(+e.target.value)} /></div>
      </>}
        <div className="formrow"><span className="muted">用于段</span><input placeholder="例：G04（依赖未确认空间锁定的段会被拦下）" value={segs} onChange={(e) => setSegs(e.target.value)} /></div>
        {relevant.map(l=><label className="formrow" key={l.id}><span className="muted">空间参考</span><span>
          <input type="checkbox" checked={chosen.includes(l.versionId!)} onChange={e=>setChosen(e.target.checked?[...chosen,l.versionId!]:chosen.filter(id=>id!==l.versionId))}/>
          加入{l.label}锁定图 · {l.name} v{l.version}</span></label>)}
        {locks.error && <p className="error">{locks.error}</p>}
      <div className="formrow"><span className="muted">尺寸</span><select value={size} onChange={(e) => setSize(e.target.value)}>{(caps?.sizes ?? []).map((s: string) => <option key={s}>{s}</option>)}</select></div>
      <div className="formrow"><span className="muted">质量</span><select value={quality} onChange={(e) => setQuality(e.target.value)}>{(caps?.quality ?? []).map((v: string) => <option key={v}>{v}</option>)}</select></div>
      <div className="formrow"><span className="muted">本批授权预算（{providers[provider]?.currency}）</span><input value={budget} onChange={(e) => setBudget(e.target.value)} /></div>
      <p className="muted small">只执行这一批；结果不明会标为“待核对”，不会自动重发。</p>
      <ErrorLine error={act.error} />
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={close}>取消</button><button className="btn primary" disabled={act.busy} onClick={create}>生成摘要，去确认</button></div>
    </Modal>
  );
}

const BATCH_LABEL: Record<string, string> = { awaiting_authorization: "待确认", authorized: "已授权", completed: "完成", partial: "部分待发",
  pending_reconcile: "待核对", partially_succeeded: "部分成功", failed: "失败", cancelled: "已取消" };

function BatchModal({ batch, assets, close }: { batch: any; assets: any[]; close: () => void }) {
  const b = useQuery<any>("batch_view", { batch_id: batch.id });
  const [accept, setAccept] = useState(false);
  const [editedBudget, setEditedBudget] = useState<string | null>(null);
  const [dispatched, setDispatched] = useState(false);
  const act = useAction();
  usePoll(!!b.data && !["awaiting_authorization", "cancelled"].includes(b.data.state)
    && b.data.requests.some((r: any) => r.state === "submitted" || (dispatched && r.state === "pending")), b.reload, 1000);
  if (!b.data) return null;
  const d = b.data;
  const pending = d.requests.filter((r: any) => r.state === "pending").length;
  const sending = d.requests.some((r: any) => r.state === "submitted");
  const budget = editedBudget ?? (d.budget_cents == null ? "" : (d.budget_cents / 100).toFixed(2));
  return (
    <Modal onClose={close} width={640}>
      <div className="row"><Tag tone="amber">会消耗额度</Tag><h2 style={{ margin: 0 }}>{d.operation === "edit" ? "局部重画" : "图像批次"} · {d.count} 张</h2><Tag>{BATCH_LABEL[d.state] ?? d.state}</Tag></div>
      {d.operation === "edit" && !!d.spec.items[0]?.cells?.length && (() => { const it = d.spec.items[0]; const base = assets.find((a) => a.id === it.inputs?.[0]); return base ? (
        <div className="row" style={{ marginTop: 12, flexWrap: "nowrap" }}>
          <div style={{ position: "relative", width: 96 }}><img src={blob(base.sha256)} style={{ width: 96, borderRadius: 6, display: "block" }} />
            <div style={{ position: "absolute", inset: 0, display: "grid", gridTemplateColumns: "repeat(3,1fr)", gridTemplateRows: "repeat(3,1fr)" }}>
              {Array.from({ length: 9 }, (_, i) => <div key={i} style={{ border: it.cells?.includes(i + 1) ? "2px solid #f5b13d" : "none" }} />)}</div></div>
          <span className="muted" style={{ fontSize: "1.54rem" }}>→</span>
          <div className="small">基础图 {base.name}（{base.marks.includes("required") ? "必用" : "候选"}）<br />只改：第 {it.cells?.join("、")} 格；其余 {9 - (it.cells?.length ?? 0)} 格保持不变<br />参考：{it.inputs.slice(1).map((x: string) => assets.find((a) => a.id === x)?.name).join("、") || "无"}</div>
        </div>) : null; })()}
      <table className="table" style={{ margin: "12px 0" }}><tbody>
        {d.spec.palettes?.length>0&&<tr><td>风格来源</td><td><PaletteTrace cards={d.spec.palettes} label="本批次冻结色卡"/></td></tr>}
        <tr><td className="muted">服务</td><td>{d.provider}{d.model ? ` · ${d.model}` : ""} · {d.operation === "edit" ? "编辑（带基础图）" : d.spec.items.some((it: any) => it.inputs?.length) ? "带参考图生成（编辑接口）" : "文生图"}</td></tr>
        <tr><td className="muted">实际输入</td><td>{d.spec.items.map((it: any, i: number) => <div key={i} className="small">#{i + 1} {it.prompt}{it.cells ? ` · 目标格 ${it.cells.join("、")}` : ""}<OrderedImageInputs inputs={d.inputGroups?.[i]}/></div>)}</td></tr>
        {d.spec.sceneLocks && <tr><td>空间锁定核对</td><td>{d.spec.sceneLocks.map((locks: SpaceLock[], i:number)=>locks.map(l=>
          <div key={i+l.id}>第 {i+1} 张 · {l.label} · {l.name ? l.name+" v"+l.version : "原锁定图不可用"} · {l.positions?.length ? "已列入输入 "+l.positions.join("、") : "未列入本张输入"}</div>))}</td></tr>}
        <tr><td className="muted">尺寸 / 质量</td><td>{d.size} · {d.spec.quality ?? "通道默认（历史批次）"}</td></tr>
        <tr><td className="muted">预算 / 费用</td><td>{budgetAmount(d.budget_cents, d.currency)} · {d.cost_state === "unknown" ? "费用未知" : `估算 ${budgetAmount(d.estimate_cents, d.currency)}`}</td></tr>
      </tbody></table>
      {d.authorization && <details open><summary>授权回执</summary>
        <div className="small">授权时间 {new Date(d.authorization.at * 1000).toLocaleString()} · {budgetAmount(d.authorization.budget_cents, d.authorization.currency)}</div>
        <div className="small" style={{overflowWrap:"anywhere"}}>确认摘要 {d.authorization.summaryHash}</div>
      </details>}
      {d.state === "awaiting_authorization" && d.billingCurrent && <label className="formrow"><span>授权预算（{d.currency}）</span><input value={budget} onChange={e=>setEditedBudget(e.target.value)}/></label>}
      {!d.billingCurrent && ["awaiting_authorization","authorized","partial"].includes(d.state) && <p>请核对历史回执。全批待发时可取消后重建；已有发送记录时，仅为仍需生成的项目新建批次并核对币种。</p>}
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
      {d.state === "awaiting_authorization" && d.cost_state === "unknown" && <label className="small"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} /> 我接受实际费用未知，通道结算金额可能超过授权预算</label>}
      <ErrorLine error={act.error} />
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        {["awaiting_authorization", "authorized"].includes(d.state) && !sending && !dispatched && <button className="btn" disabled={act.busy} onClick={() => act.run(async () => { await cmd("cancel_batch", { batch_id: d.id }); close(); })}>取消批次</button>}
        {d.state === "awaiting_authorization" && d.billingCurrent && <button className="btn primary" disabled={act.busy || (d.cost_state === "unknown" && !accept)} onClick={() => act.run(async () => {
          await cmd("authorize_batch", { batch_id: d.id, summary_hash: d.summary_hash, budget_cents: budgetCents(budget), accept_unknown_cost: accept });
          b.reload();
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
  const lay = layout(plan?.versions.find((v: any) => v.id === plan.adopted_version_id)?.content);
  const {hash}=useProjectRoute();
  const requestedSegment=new URLSearchParams(hash.split("?")[1]).get("segment");
  const [seg, setSeg] = useState<string>("");
  const [txt, setTxt] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [sound, setSound] = useState<any>(null);
  const act = useAction();
  const [includeImages, setIncludeImages] = useState(false);
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
    if (requestedSegment && current?.content.segments?.[requestedSegment]) chooseSegment(requestedSegment);
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
  if (!art) return <div className="empty">制作方案采用后才能写分段提示词。</div>;
  const create = ctx.view.kickoff.executor_plan.create;
  const modelDraft = () => act.run(async () => {
    await cmd("create_task", {episode_id:ctx.eid, stage:"prompts", prompt_images:includeImages,
      goal:"从采用的方案起草所有生成段的完整提示词。沿用所给逐段输入编号、固定块与时间窗口，写清起幅、表演、机位、反应、交接和声音。"});
    ctx.setFocus(null); ctx.reload();
  });
  const programDraft = () => act.run(async () => {
    const content = await q("draft_prompts", {episode_id:ctx.eid});
    const r = await cmd("save_manual", {artifact_id:art.id, base_version_id:current?.id ?? null, content});
    await ctx.reload(); ctx.setFocus(r.versionId);
  });
  const segInfo = lay.segments.find((s: any) => s.id === seg);
  const errors = (current?.gate.formatErrors ?? []).filter((e: string) => e.startsWith(seg + "："));
  const save = (content: any) => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current?.id ?? null, content }); await ctx.reload(); ctx.setFocus(r.versionId); });
  const draft = {txt, ambience:amb.split(/[,，、]/).map(x=>x.trim()).filter(Boolean),
    dialogue:dia.split(/[；;\n]/).map(x=>x.trim()).filter(Boolean)};
  const editReference = (action:string, asset_id?:string, previous_id?:string, purpose?:string) => act.run(async()=>{
    const r = await cmd("edit_prompt_reference",{version_id:current.id, segment_id:seg, action, asset_id, previous_id, purpose, draft});
    await ctx.reload(); ctx.setFocus(r.versionId); return r;
  });
  const applySound = (change:{silent:boolean;retained_notes?:string}|{bgm:boolean;music_description:string}, confirm=false) => act.run(async () => {
    const r = await cmd("set_sound", {version_id:current.id, segment_id:seg, change, confirm, draft});
    if (r.needsConfirm) setSound(r);
    else {setSound(null); await ctx.reload(); ctx.setFocus(r.versionId);}
  });
  const move = (i: number, d: number) => act.run(async () => {
    const order = entry.refs.map((r: any) => r.assetVersionId);
    [order[i], order[i + d]] = [order[i + d], order[i]];
    setPreview({ order, ...(await cmd("reorder_refs", { version_id: current.id, segment_id: seg, new_order: order, draft })) });
  });
  return (
    <div>
      <div className="stagehead"><div><h1>提示词</h1><div className="muted small">{current ? `${rno(current.display_no)} ${current.id === art.adopted_version_id ? "已采用" : "草稿"}` : "尚无"} · 分段参考与提示词</div></div>
        {current && <VersionTabs ctx={ctx} art={art} current={current} />}</div>
      <div className="row">{lay.segments.map((s: any) => <button key={s.id} className={`pill ${seg === s.id ? "on" : ""}`} onClick={() => chooseSegment(s.id)}>{s.id} · {s.duration / 1000}s</button>)}</div>
      {current && <VersionSignals key={current.id} version={current.id} number={current.display_no} locate={(sid,phrase)=>{chooseSegment(sid);setJump({version:current.id,sid,phrase});}}/>}
      {entry ? <>
        <RhythmReview key={current.id} version={current.id}/>
        {palette.error&&<p className="error">{palette.error}</p>}
        <ReferenceReview ctx={ctx} version={current.id} sid={seg} entry={entry} draft={draft} save={ctx.setFocus}/>
        {palette.data&&<PaletteTrace cards={palette.data.cards} label={palette.data.direct?`${seg} 所在稿起草时参考`:`${seg} 上游稿 R${palette.data.displayNo} 起草时参考`}/>}
        <div className="row" style={{ margin: "14px 0", alignItems: "stretch" }}>
          {entry.refs.map((r: any, i: number) => { const a = ctx.assets.find((x) => x.id === r.assetVersionId); return (
            <div key={r.assetVersionId} className="card line" style={{ width: 150, padding: 8 }}>
              {a && (a.kind === "image" ? <img src={blob(a.sha256)} alt={a.name} style={{width:"100%",borderRadius:6}}/>
                : <MediaPreview episode={ctx.eid} asset={a} label={r.label}/>)}
              <div className="spread"><b>{r.label}</b><span className="row">{i > 0 && <button className="btn ghost" aria-label={r.label+"前移"} disabled={act.busy} onClick={() => move(i, -1)}>←</button>}{i < entry.refs.length - 1 && <button className="btn ghost" aria-label={r.label+"后移"} disabled={act.busy} onClick={() => move(i, 1)}>→</button>}</span></div>
              <div className="muted tiny">{r.role || "用途未写"}{r.grid ? " · 九宫格" : ""}</div>
              <select aria-label={"替换"+r.label} disabled={act.busy} style={{width:"100%",marginTop:4}} value="" onChange={e=>editReference("replace",e.target.value,r.assetVersionId)}>
                <option value="">{segInfo?.strategy === "grid" ? "替换本段九宫格…" : "替换此参考…"}</option>
                {ctx.assets.filter(x=>x.kind===r.kind && (segInfo?.strategy!=="grid" || x.grid) && !entry.refs.some((ref:any)=>ref.assetVersionId===x.id)).map(x=><option key={x.id} value={x.id}>{x.name} v{x.version_no}{x.marks.includes("required") ? "（必用）" : ""}</option>)}
              </select>
              {segInfo?.strategy!=="grid" && (r.kind!=="image" || entry.refs.filter((ref:any)=>ref.kind==="image").length>1) &&
                <button className="btn ghost" aria-label={"移出"+r.label} disabled={act.busy} onClick={()=>editReference("remove",undefined,r.assetVersionId)}>移出本段</button>}
            </div>); })}
          {segInfo?.strategy !== "grid" && <select aria-label="添加图片参考" disabled={act.busy} style={{alignSelf:"center",width:130}} value="" onChange={e=>{
            const v=ctx.assets.find(x=>x.id===e.target.value);
            if (v) void editReference("add",v.id,undefined,v.name.replace(/\.png$/i,""));
          }}><option value="">＋ 添加图片…</option>{ctx.assets.filter(x=>x.kind==="image" && !entry.refs.some((r:any)=>r.assetVersionId===x.id)).map(x=><option key={x.id} value={x.id}>{x.name} v{x.version_no}</option>)}</select>}
        </div>
        {segInfo?.strategy!=="grid" && <PromptMedia key={ctx.eid+"/"+seg+"/"+current.id} episode={ctx.eid} reload={ctx.reload} busy={act.busy}
          assets={ctx.assets.filter(x=>(x.kind==="audio" || x.kind==="video") && !entry.refs.some((r:any)=>r.assetVersionId===x.id))}
          attach={(id,purpose)=>editReference("add",id,undefined,purpose)}/>}
        {preview && <div className="card amber" style={{ marginBottom: 10 }}><b>重排预览</b> <span className="small">{Object.entries(preview.preview.mapping).map(([a, b]) => `${a}→${b}`).join("，")}</span>
          {preview.preview.unclearRoles.length > 0 && <div className="small">用途不明确，请核对：{preview.preview.unclearRoles.join("、")}</div>}
          <div className="field small" style={{ marginTop: 6 }}>{preview.preview.txtDiff.map((d: any, i: number) => d.op === "same" ? <span key={i}>{d.text}</span> : d.op === "del" ? <del key={i} className="d">{d.text}</del> : <ins key={i} className="d">{d.text}</ins>)}</div>
          <div className="row" style={{ marginTop: 6 }}><button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("reorder_refs", { version_id: current.id, segment_id: seg, new_order: preview.order, draft, confirm: true }); await ctx.reload(); ctx.setFocus(r.versionId); setPreview(null); })}>确认重排</button><button className="btn" onClick={() => setPreview(null)}>取消</button></div></div>}
        <textarea ref={editor} data-version={current.id} aria-label={seg+" 提示词正文"} style={{ minHeight: 260, fontSize: "1rem" }} value={txt} onChange={(e) => {setTxt(e.target.value);setSound(null);setPreview(null);}} />
        {errors.length > 0 && <div className="error">{errors.join("；")}</div>}
        <div className="grid2" style={{ marginTop: 8 }}>
          <input placeholder="环境声（逗号分隔）" value={amb} onChange={(e) => {setAmb(e.target.value);setSound(null);setPreview(null);}} />
          <input placeholder="对白（多条用；分隔，英语）" value={dia} onChange={(e) => {setDia(e.target.value);setSound(null);setPreview(null);}} /></div>
        <div className="spread" style={{ marginTop: 10 }}>
          <div className="row small">声音：<Tag tone={entry.sound?.bgm ? "green" : ""}>{entry.sound?.bgm ? "有背景音乐" : "无背景音乐"}</Tag>
            {(entry.sound?.ambience ?? []).map((x: string) => <Tag key={x}>{x}</Tag>)}{(entry.sound?.dialogue ?? []).map((x: string) => <Tag key={x} tone="blue">{x}</Tag>)}
            {entry.sound?.silent && <Tag tone="amber">全静音</Tag>}</div>
          <div className="row">
            {entry.prompt_format === "readable_v3" && !entry.sound?.bgm && <input aria-label="本段配乐描述" placeholder="具体配乐描述" value={music} onChange={e=>setMusic(e.target.value)}/>}
            <button className="btn" disabled={act.busy} onClick={() => applySound({bgm:!entry.sound?.bgm,music_description:music})}>{entry.sound?.bgm ? "去掉背景音乐" : "加背景音乐"}</button>
            <button className="btn" disabled={act.busy} onClick={() => applySound({silent:!entry.sound?.silent})}>{entry.sound?.silent ? "解除全静音" : "全静音"}</button>
            <button className="btn primary" onClick={() => save({ ...current.content, segments: { ...current.content.segments, [seg]: { ...entry, txt,
              sound: { ...(entry.sound ?? {}), ambience: amb.split(/[,，、]/).map((x) => x.trim()).filter(Boolean), dialogue: dia.split(/[；;\n]/).map((x) => x.trim()).filter(Boolean) } } } })}>保存本段</button>
          </div>
        </div>
        {sound && <div className="card amber" style={{ marginTop: 10 }}><b>全静音会与以下内容冲突</b>{sound.conflicts.map((c: any) => <div key={c.item} className="small">{c.item}：{Array.isArray(c.detail) ? c.detail.join("、") : c.detail}</div>)}
          {typeof sound.retainedNotes==="string" && <label>静音后保留的画面文字及固定说明<textarea value={sound.retainedNotes} onChange={e=>setSound({...sound,retainedNotes:e.target.value})}/></label>}
          <div className="row" style={{ marginTop: 6 }}><button className="btn danger" disabled={act.busy} onClick={() => applySound({silent:true,retained_notes:sound.retainedNotes??undefined},true)}>仍然全静音</button><button className="btn" onClick={() => setSound(null)}>保留</button></div></div>}
      </> : <div style={{ marginTop: 14 }}><p className="muted">该段还没有提示词。创作执行器为 {PROVIDER_LABEL[create.provider] ?? create.provider} · {create.model ?? "默认型号"}。</p>
        <div className="row"><button className="btn primary" disabled={act.busy} onClick={modelDraft}>从方案起草提示词</button>
          <label><input type="checkbox" checked={includeImages} onChange={e => setIncludeImages(e.target.checked)}/>同时发送本集已绑定的参考原图</label>
          <button className="btn" disabled={act.busy} onClick={programDraft}>生成本地时间与参考骨架</button></div></div>}
      <ErrorLine error={act.error} />
      {current && <AdoptBar ctx={ctx} art={art} version={current} next="publish" hint="冻结实际送审 TXT 与引用文件后进入两遍独审" />}
    </div>
  );
}

// ---------------------------------------------------------------- 08 两遍独审 (J17, J21)
function ReviewInputs({runId,label}:{runId:string;label:string}) {
  const [open,setOpen] = useState(false);
  type FrozenInput = {name:string;role:string;sha256:string;bytes:number};
  const packet = useQuery<{matches:boolean;fingerprint:string;manifest:{inputs:FrozenInput[]}}>(open?"run_manifest":null,{run_id:runId});
  return <details onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>{label}送审文件</summary>
    {packet.error && <p className="error">{packet.error}</p>}
    {packet.data && (packet.data.matches ? <ul>{packet.data.manifest.inputs.map((file,index)=><li key={index}>
      <a href={blob(file.sha256)} download={file.name}>{file.name}</a> · {file.bytes} 字节 · <code title={file.sha256}>{file.sha256.slice(0,12)}</code>
    </li>)}</ul> : <p className="error">送审清单指纹与运行记录不符</p>)}
  </details>;
}

function ReviewView({ ctx }: { ctx: Ctx }) {
  const { go } = useProjectRoute();
  const { art, current } = useVersion(ctx, "prompts");
  const st = useQuery<any>(current ? "prompt_review_status" : null, { version_id: current?.id }, [ctx.view]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const act = useAction();
  if (!art || !current) return <div className="empty">提示词定稿后才能两遍独审。</div>;
  const rows = Object.entries(st.data ?? {});
  const cnt = (f: (x: any) => boolean) => rows.filter(([, x]) => f(x)).length;
  const label = (x: any) => {
    if (x.ok) return x.waived ? "用户放行" : "通过";
    const states = [x.coldRead.state,x.comparison.state];
    if (states.includes("blocked")) return "退回";
    if (states.includes("pending_decision")) return "待判断";
    return "待审";
  };
  const waiting = (pass:any) => pass.state === "needed" && !pass.activeRun;
  const progress = (pass:any) => pass.activeRun?.state === "queued" ? "已排队" : pass.activeRun ? "正在审读" : "";
  const submit = (pass_name:"cold_read"|"comparison") => act.run(async () => {
    await cmd("start_prompt_review_batch",{version_id:current.id,pass_name});
    st.reload(); ctx.reload();
  });
  return (
    <div>
      <div className="stagehead"><div><h1>两遍独审</h1><div className="muted small">{PROVIDER_LABEL[ctx.view.kickoff.executor_plan.review.provider]} · 独立会话 · 第一遍读取本段 TXT 和原图 · 第二遍加入故事、方案和本步选用的参考资料</div></div>
        <div className="row"><button className="btn" disabled={act.busy || !cnt(x=>waiting(x.coldRead))} onClick={()=>submit("cold_read")}>送审材料（第一遍）</button><button className="btn" disabled={act.busy || !rows.length || cnt(x=>x.coldRead.state==="needed" || x.coldRead.activeRun)>0 || !cnt(x=>waiting(x.comparison))} onClick={()=>submit("comparison")}>开始第二遍</button>
          <button className="btn primary" onClick={() => go(`#/e/${ctx.eid}/delivery`)}>去交付核验</button></div></div>
      <div className="grid3">
        <div className="card"><div className="muted small">通过</div><div style={{ fontSize: "1.69rem", color: "var(--green)", fontWeight: 700 }}>{cnt((x) => x.ok && !x.waived)} 段</div>{cnt(x=>x.ok && x.waived)>0&&<div className="small">另有 {cnt(x=>x.ok && x.waived)} 段由用户放行</div>}</div>
        <div className="card"><div className="muted small">退回</div><div style={{ fontSize: "1.69rem", color: "var(--red)", fontWeight: 700 }}>{cnt((x) => label(x) === "退回")} 段</div></div>
        <div className="card"><div className="muted small">待判断 / 待审</div><div style={{ fontSize: "1.69rem", color: "var(--amber)", fontWeight: 700 }}>{cnt((x) => !x.ok && label(x) !== "退回")} 段</div></div>
      </div>
      <VersionSignals key={current.id} version={current.id} number={current.display_no}/>
      <div className="muted small" style={{ margin: "10px 0" }}>通过：两遍都没有问题 · 退回：必须修改 · 待判断：改，或由你写理由放行（记为“用户放行”，不记为通过）</div>
      {rows.map(([seg, x]: any) => { const ref = current.content.segments[seg]?.refs?.find((r:any)=>r.kind==="image"); const a = ctx.assets.find((y) => y.id === ref?.assetVersionId);
        const issues = [...(x.coldRead.issues ?? []), ...(x.comparison.issues ?? [])]; const l = label(x);
        return (
          <div key={seg} className={`card ${l === "退回" ? "red" : ""}`} style={{ marginBottom: 10 }}>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <img src={blob(a?.sha256)} style={{ width: 70, height: 40, objectFit: "cover", borderRadius: 6 }} />
              <div style={{ flex: 1 }}><b>{seg}</b><div className="muted tiny">第一遍 · 冷读</div><div className="small">{progress(x.coldRead) || (x.coldRead.state === "needed" ? "待审" : x.coldRead.observation ?? DEC[x.coldRead.decision])}</div></div>
              <div style={{ flex: 1 }}><div className="muted tiny">第二遍 · 对照</div><div className="small">{progress(x.comparison) || (x.comparison.state === "needed" ? "待审（含相邻接点）" : x.comparison.observation ?? DEC[x.comparison.decision])}</div></div>
              <Tag tone={l === "通过" || l === "用户放行" ? "green" : l === "退回" ? "red" : "amber"}>{l}</Tag>
            </div>
            {x.coldRead.runId&&<ReviewInputs key={x.coldRead.runId} runId={x.coldRead.runId} label="第一遍"/>}
            {x.comparison.runId&&<ReviewInputs key={x.comparison.runId} runId={x.comparison.runId} label="第二遍"/>}
            {issues.map((i: any) => (
              <div key={i.id} className="small" style={{ marginTop: 8 }}><Tag tone={i.blocking ? "red" : "amber"}>{i.blocking ? "必须修复" : "语义意见"}</Tag> {i.reason}
                {i.waiver_reason ? <span className="ok"> · 已放行：{i.waiver_reason}</span> : !i.blocking && <span className="row" style={{ marginTop: 4 }}>
                  <input placeholder="写理由放行（绑定本段输入，改动后失效）" value={reasons[i.id] ?? ""} onChange={(e) => setReasons({ ...reasons, [i.id]: e.target.value })} style={{ flex: 1 }} />
                  <button className="btn" onClick={() => act.run(async () => { await cmd("waive_segment_issue", { issue_id: i.id, reason: reasons[i.id] ?? "" }); st.reload(); })}>放行</button></span>}
              </div>))}
          </div>); })}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 09 发布包 (J42)
function PublishView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "publish");
  const [form, setForm] = useState<any>(null);
  const settings = useQuery<any>("publish_settings", {project_id: ctx.view.episode.project_id});
  const composed = useQuery<any>(current ? "compose_publish" : null, { episode_id: ctx.eid, version_id: current?.id }, [current?.id]);
  const act = useAction();
  useEffect(() => { setForm(current?.content ?? { title: "", body: "", tags: [], extraFields: {}, cues: [] }); }, [current?.id]);
  if (!art) return <div className="empty">提示词采用后编写发布包。</div>;
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
      <div className="stagehead"><div><h1>发布包</h1><div className="muted small">发布文案按项目字段分别复制；保存版本时记录当时的发布设置</div></div>
        {current && <VersionTabs ctx={ctx} art={art} current={current} />}</div>
      <div className="grid2">
        <div className="col">
          {[["title", "发布标题"], ["body", settings.data?.body_fixed_lines?.length ? "正文第一行" : "正文"],
            ...(settings.data?.additional_fields ?? []).map((v: any) => [v.key, v.label])].map(([k, l]) => {
              const nested = !["title","body","suno_title","suno_style","suno_exclude"].includes(k);
              return <label key={k} className="col" style={{gap:4}}><span className="muted small">{l}</span>
                <textarea rows={k==="body"?3:1} value={(nested ? form.extraFields?.[k] : form[k]) ?? ""}
                  onChange={e => setForm(nested ? {...form, extraFields:{...form.extraFields,[k]:e.target.value}} : {...form,[k]:e.target.value})}/></label>;
            })}
          <label className="col" style={{ gap: 4 }}><span className="muted small">本集话题标签（逗号分隔）</span><input value={(form.tags ?? []).join(",")} onChange={(e) => setForm({ ...form, tags: e.target.value.split(/[,，]/).map((x) => x.trim()).filter(Boolean) })} /></label>
          <PublishCues form={form} segments={segments} bgm={bgm} change={patch=>setForm({...form,...patch})}/>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current?.id ?? null, content: form }); await ctx.reload(); ctx.setFocus(r.versionId); })}>保存为新版本</button>
        </div>
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
function DeliveryView({ ctx }: { ctx: Ctx }) {
  const [scope, setScope] = useState<"full" | "trial">(ctx.view.kickoff.spec.packaging==="trial"?"trial":"full");
  const [segs, setSegs] = useState<string[]>([]);
  const problems = useQuery<any>("delivery_problems", { episode_id: ctx.eid, scope, segments: scope === "trial" ? segs : null }, [scope, segs.join()]);
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const [verify, setVerify] = useState<Record<string, any>>({});
  const act = useAction();
  const plan = artifactFor(ctx.view, "plan");
  const lay = layout(plan?.versions.find((v: any) => v.id === plan.adopted_version_id)?.content);
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
      {scope === "trial" && <div className="row">{lay.segments.map((s: any) => <button key={s.id} className={`pill ${segs.includes(s.id) ? "on" : ""}`} onClick={() => setSegs(segs.includes(s.id) ? segs.filter((x) => x !== s.id) : [...segs, s.id])}>{s.id}</button>)}<span className="muted small">试验范围从零计时，不更新整集完成状态</span></div>}
      {list.length > 0 ? (
        <div className="card red" style={{ marginTop: 14 }}>
          <b className="error">结构阻断 · {list.length} 项</b><span className="muted small"> · 不能放行，逐条定位到出错的阶段</span>
          {Object.entries(byStage).map(([stg, ps]) => (
            <div key={stg} style={{ marginTop: 10 }}><div className="row"><Tag tone="red">{stg}</Tag><a className="btn ghost" href={`#/e/${ctx.eid}/${stg === "recheck" || stg === "review" ? "review" : stg === "assets" ? "assets" : stg === "space" ? "space" : stg}`}>去修复</a></div>
              {ps.slice(0, 8).map((p: any, i: number) => <div key={i} className="small" style={{ margin: "3px 0" }}>{p.segment ?? ""} {p.file ? `· ${p.file}` : ""} · {p.problem}</div>)}</div>))}
        </div>) : problems.data && <div className="card" style={{ marginTop: 14, background: "#13261f" }}><Tag tone="green">核验通过</Tag> <span className="small">可以写入交付目录（先写临时目录，校验后改名）。</span></div>}
      <button className="btn primary" style={{ marginTop: 12 }} disabled={!problems.data || !!problems.error || list.length > 0 || act.busy} onClick={() => act.run(async () => { await cmd("deliver", { episode_id: ctx.eid, scope, segments: scope === "trial" ? segs : null }); await load(); ctx.reload(); problems.reload(); })}>生成图文交付</button>
      <h2 style={{ marginTop: 24 }}>已登记的交付</h2>
      {deliveries.length === 0 && <div className="muted">尚无。</div>}
      {deliveries.map((d) => (
        <div key={d.id} className="card line" style={{ marginBottom: 10 }}>
          <div className="spread"><span><Tag tone={d.scope === "full" ? "green" : "blue"}>{d.scope === "full" ? "整集" : "试验"}</Tag> 交付号 <span className="mono">{d.id.slice(0, 8)}</span> · {d.segments.join("、")} · {Object.keys(d.files).length} 个文件</span>
            <div className="row"><a className="btn" target="_blank" href={deliveryFile(d.id, "00_图文对照.html")}>打开图文页</a>
              <button className="btn" onClick={() => navigator.clipboard.writeText(d.html)}>复制 HTML 绝对路径</button>
              <button className="btn" onClick={() => act.run(async () => setVerify({ ...verify, [d.id]: await q("verify_delivery", { delivery_id: d.id }) }))}>离线验包</button>
              {d.scope === "full" && <a className="btn primary" href={`#/p/${ctx.view.episode.project_id}/closure-start/${d.id}`}>整理本集设定</a>}</div></div>
          <div className="muted tiny mono" style={{ marginTop: 6 }}>{d.html}</div>
          <p className="small">图文包记录计划时长。实际生成视频后，在制作和剪辑工具中核对片长、画幅、动作与声音。</p>
          <a className="btn" href={`#/e/${ctx.eid}/package?delivery=${encodeURIComponent(d.id)}`}>将这次交付打包给 H3</a>
          {verify[d.id] && <div className={verify[d.id].ok ? "ok small" : "error small"}>{verify[d.id].ok ? "验包通过：文件与指纹一致" : verify[d.id].errors.map((e: any) => `${e.file} ${e.problem}`).join("；")}</div>}
        </div>))}
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
  const { hash } = useProjectRoute();
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
  const controls=d && <div className="package-controls">
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
      {!d ? <div className="muted">{requestedDelivery ? "未找到指定交付，请从本集交付记录重新选择。" : "先完成图文交付。"}</div> : <>
        <div className="row"><select value={did} onChange={(e) => { const next = deliveries.find(x => x.id === e.target.value); setDid(e.target.value);setBatch(emptyPackageParams);setIndividual({});setTarget(""); setSegs(next?.segments ?? []); setOut(null); }}>{deliveries.map((x) => <option key={x.id} value={x.id}>{x.scope === "full" ? "整集" : "试验"} {x.id.slice(0, 8)}</option>)}</select>
          <button className="btn" onClick={()=>act.run(()=>navigator.clipboard.writeText(d.html))}>复制图文页路径</button>
          <details className="small"><summary>图文页位置</summary><code>{d.html}</code></details></div>
        <p>{d.scope==="full"?"整集图文已交付":"试验图文已交付"} · {d.segments.length} 段</p>
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
          setOut({...await cmd("package",{...args,expected_preview:result?.previewHash}),inputKey});
        })}>验包并导出 ZIP</button>
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
  const orig = shots.find((x: any) => x.id === shotId);
  const [sh, setSh] = useState<any>(orig);
  const act = useAction();
  useEffect(() => setSh(orig), [shotId, version.id]);
  if (!sh) return null;
  const save = (content: any, note: string) => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: version.id, content, note }); await ctx.reload(); onSaved(r.versionId); });
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
        {([["startAssetId", "起幅"], ["endAssetId", "落幅"]] as const).map(([field, label]) =>
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
        <button className="btn danger" onClick={() => save({ ...version.content, shots: shots.filter((x: any) => x.id !== sh.id) }, `删除 ${sh.id}`)}>删除镜头</button>
      </div>
      <ErrorLine error={act.error} />
    </div>
  );
}
