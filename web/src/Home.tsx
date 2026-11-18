import { ArchiveControls } from "./ArchiveControls";
import {ExecutorPlanFields,useExecutorPlan} from "./ExecutorDefaults";
import {useStoryImport} from "./storyExcerpt";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { ResourceBrowser, type Excerpt, type PaletteChoice } from "./Resources";
import { ResearchReference, TopicResearch, type ReferenceDraft } from "./TopicResearch";
import { Kickoff } from "./Project";
import { addDirectorFiles, clipboardFiles, DirectorFiles, useDirectorCommand, isPicture } from "./DirectorFiles";
import { isKickoffTopic } from "./topics";
import { FileInput, PencilLine, Plus, PackageOpen, Film, Palette, NotebookPen, Ratio, ChevronDown, ArrowUp, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AttentionTag, useAttention } from "./Attention";
import { blob, q, cmd, upload, importManuscript, ApiError, commandId } from "./api";
import {OpenProjectFolder, SaveProjectFolder} from "./ProjectFolders";
import { resourceOwner, type useProjectList } from "./projectNavigation";
import { ErrorLine, Modal, PROVIDER_LABEL, go, useAction, useQuery } from "./ui";

const TABS: [string, string, LucideIcon][] = [["import", "导入故事稿 / 剧本", FileInput], ["idea", "从一个想法开始", PencilLine],
  ["episode", "给项目开一集", Plus], ["delivery", "图文交付", PackageOpen], ["reference", "从参考片写剧本", Film]];
const CHIPS = ["/ 从候选选题开一集", "/ 选题研究", "/ 承接上一集开新一集", "/ 审一份已有故事稿", "/ 修改进行中的一段"];
async function ensureProject(pid: string, name: string, id?:string) {
  if (!pid) throw new Error("请选择项目");
  if (pid !== "__new" && !pid.startsWith("__new:")) return pid;
  const config = pid.startsWith("__new:") ? pid.slice(6) : "_template";
  return (await cmd("create_project", { config_id: config, command_id:id ?? commandId(), name: config === "_template" ? name : undefined })).projectId;
}

function projectName(p:any, list:any[]) {
  return list.some(other=>other.id!==p.id && other.name===p.name) ? p.name+" · "+p.episodes+" 集 · "+p.id.slice(0,4) : p.name;
}
export default function Home({ tab, projects, rememberProject, referenceDraft, initialTopic, consumeTopic }: { initialTopic:any; consumeTopic:()=>void; referenceDraft:ReferenceDraft; tab?: string; projects: ReturnType<typeof useProjectList>; rememberProject:(id:string)=>void }) {
  const [mode, setMode] = useState(TABS.some(([k]) => k === tab) ? tab! : "idea");
  const [text, setText] = useState("");
  const [projectId, setProjectId] = useState<string>(initialTopic?.project_id ?? "");
  const [pop, setPop] = useState<"" | "style" | "topic" | "settings" | "project">(referenceDraft.value?"topic":"");
  const [pictures,setPictures] = useState<File[]>([]);
  const startEpisode=useDirectorCommand();
  const newProjectRequest=useRef<{key:string;id:string}|null>(null);
  const [imageKickoff,setImageKickoff] = useState<any>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [paletteCards,setPaletteCards]=useState<PaletteChoice[]>([]);
  const [choosingResearchOwner,setChoosingResearchOwner]=useState(false);
  const [original,setOriginal]=useState<Excerpt|null>(null);
  const storyImport=useStoryImport();
  const [styleNote, setStyleNote] = useState("");
  const [topic, setTopic] = useState<any>(initialTopic);
  useEffect(() => { if (initialTopic) consumeTopic(); }, [initialTopic, consumeTopic]);
  const [previous, setPrevious] = useState("");
  const [ratio, setRatio] = useState("");
  const {plan:executorPlan,setPlan:setExecutorPlan}=useExecutorPlan();
  const creator=executorPlan.create.provider, reviewer=executorPlan.review.provider;
  const [targetEpisode, setTargetEpisode] = useState("");
  const [purpose, setPurpose] = useState("story_source");
  const [code, setCode] = useState("");
  const [recognized, setRecognized] = useState<any[]>([]);
  const creating = projectId.startsWith("__new");
  const existing = !!projectId && !projectId.startsWith("__new");
  const project = useQuery<any>(existing ? "project_overview" : null, { project_id: projectId });
  const topics = useQuery<any[]>(existing ? "topics" : null, { project_id: projectId });
  const configs=useQuery<any[]>("available_configs");
  const exec = useQuery<any>("probe");
  const act = useAction();
  const list = projects.data ?? [];
  useEffect(() => { if (!projectId && projects.data) setProjectId(list.find((p) => p.id === localStorage.getItem("lastProject"))?.id ?? (list.length===1 ? list[0].id : list.length ? "" : "__new")); }, [projectId, projects.data]);
  const resourceTab = tab==="library" || tab==="topics";
  const owner = resourceOwner(list);
  useEffect(()=>{
    if (resourceTab && owner) go(`#/p/${owner}/${tab}`);
  },[resourceTab,owner,tab]);
  const configName = configs.data?.find(c => "__new:" + c.configId === projectId)?.name;
  const projectLabel = (list.some(p=>p.id===projectId) ? projectName(list.find(p=>p.id===projectId),list) : null) ?? (configName ? `新项目（${configName}）` : creating ? "新项目" : "选择项目");
  const projectReady = creating || (existing && project.data?.project.id === projectId);
  const listNotice = projects.error
    ? <div role="alert" className="error">项目列表读取失败 · {projects.error} <button className="btn" onClick={projects.reload}>重试项目列表</button></div>
    : !projects.data && <p role="status">正在读取项目列表…</p>;
  const nextCode = () => {
    const eps = project.data?.project.id === projectId ? project.data.project.reservedEpisodeCodes : [];
    const nums = eps.map((code: string) => parseInt(code.replace(/\D/g, ""), 10)).filter((n: number) => !isNaN(n));
    const prefix = eps[0]?.replace(/\d+/g, "") || "E";
    return `${prefix}${String((nums.length ? Math.max(...nums) : 0) + 1).padStart(2, "0")}`;
  };
  const episodes = project.data?.project.id === projectId ? project.data.episodes : [];
  const opened = episodes.filter((e: any) => e.kickoff_id);
  const destination = opened.find((e: any) => e.id === targetEpisode) ?? opened[opened.length - 1];
  const kickoffMode = ["idea","episode","import"].includes(mode);
  const topicFor = async(pid:string) => topic && !topic.id ? (await cmd("add_topic",{project_id:pid,code:topic.code,premise:topic.premise,command_id:topic.pending})).topicId : topic?.id;
  const prefill = { style: styleNote, paletteCards, ratio, creator, reviewer, executorPlan, mode: "manual", predecessorId: previous };
  const selectProject = (pid: string) => {
    if(pid===projectId)return;
    setPictures([]); if(!pid.startsWith("__new"))rememberProject(pid); setProjectId(pid); setRecognized([]); setTopic(null); setPrevious(""); setTargetEpisode("");
  };
  const selectMode = (value: string) => { if (value !== mode) {setTopic(null); setPrevious("");} setMode(value); setPop(""); act.clear(); };
  const openKickoff = (pid:string, values:Record<string,unknown>) => {
    if (pictures.length) {
      setProjectId(pid);
      setImageKickoff({...values,projectId:pid});
    } else {
      sessionStorage.setItem("kickoff-prefill:"+pid,JSON.stringify({...values,projectId:pid}));
      go(`#/p/${pid}`);
    }
  };
  const attachPictures = (incoming:File[]) => act.run(async()=>{
    const files = addDirectorFiles(pictures,incoming,true);
    if (!kickoffMode) selectMode("idea");
    setPictures(files);
  });
  const shortcut = (index: number) => act.run(async () => {
    if (index === 0) { selectMode("episode"); setPop("topic"); }
    if (index === 1) {
      if (!existing) {setPop("topic");return;}
      go(`#/p/${projectId}/topics`);
    }
    if (index === 2) {
      if (!opened.length) throw new Error("当前项目还没有可承接的已开工集");
      selectMode("episode"); setPrevious(opened[opened.length - 1].id);
    }
    if (index === 3) selectMode("import");
    if (index === 4) {
      const ep = [...opened].reverse().find(e => e.state !== "delivered");
      if (!ep) throw new Error("当前项目还没有进行中的剧集");
      const view = await q("episode_view", {episode_id:ep.id});
      const stages = view.workflow.stages.map(([id]:[string,string])=>id);
      const start = view.kickoff?.sourceIntent === "locked_screenplay" ? stages.indexOf("script") : 0;
      const next = stages.slice(start).find((stage:string)=>!view.skippedStages[stage] &&
        !view.artifacts.some((a:any)=>a.stage === stage && a.adopted_version_id));
      go(`#/e/${ep.id}/${next || "delivery"}`);
    }
  });
  const [quickStarting,setQuickStarting]=useState(false);
  // 实测 114：运行模式“专家”即 AI 连续制作（默认）；“手工”为开工后逐阶段手动推进。
  const [runMode,setRunMode]=useState<"expert"|"manual">("expert");
  const send = () => act.run(async () => {
    if (mode === "idea" || mode === "episode") {
      if(referenceDraft.value){setPop("topic");throw new Error("已选素材，请先选择按原文拍、研究备选题或取消引用。");}
      if (mode === "idea" && !topic && !original && !text.trim()) throw new Error("请输入一句话想法或选择一张选题卡");
      const key=JSON.stringify([projectId,text,mode,topic?.id,original?.path,original?.offset]);
      if(newProjectRequest.current?.key!==key)newProjectRequest.current={key,id:commandId()};
      const pid = await ensureProject(projectId, text.slice(0, 12) || original?.title || "新项目",newProjectRequest.current.id);
      const importId=await storyImport(pid,original);
      const topicId=await topicFor(pid);
      const goal=importId ? ["按所选原文剧情改编本集故事稿。",text.trim()].filter(Boolean).join("\n") : topic ? text : "";
      if(runMode==="manual"){
        const displayCode=code || (pid === projectId ? nextCode() : "E01");
        const setup={...prefill,displayCode,importId,idea:topic||importId ? undefined : text,topicId,goal};
        if(!text.trim()&&!topic&&!importId){openKickoff(pid,setup);return;}
        try {
          const r=await startEpisode("start_episode",{episode_id:null,project_id:pid,display_code:displayCode,
            source_type:importId?"import":topic?"topic":"idea",source_import_id:importId,idea_text:topic||importId?undefined:text,topic_id:topicId,goal,
            relation:previous?"continue":"independent",predecessor_ids:previous?[previous]:[],
            executor_plan:executorPlan,cooperation_mode:"advanced",
            palette_ids:paletteCards.map(c=>c.id),spec_overrides:{...(ratio?{aspectRatio:ratio}:{}),...(styleNote?{style:styleNote}:{})},start_story:true},pictures);
          rememberProject(pid);referenceDraft.change(null);setText("");setPictures([]);newProjectRequest.current=null;go("#/e/"+r.episodeId+"/story");
        } catch(error) {
          if(error instanceof ApiError && error.status===409){setImageKickoff({...setup,projectId:pid});}
          throw error;
        }
        return;
      }
      setQuickStarting(true);
      try {
        const result=await startEpisode("quick_start",{project_id:pid,idea:text,selections:{
          topicId,source_import_id:importId,source_intent:"adapt",goal,
          executor_plan:executorPlan,cooperation_mode:"advanced",
          palette_ids:paletteCards.map(c=>c.id),spec_overrides:{...(ratio?{aspectRatio:ratio}:{}),...(styleNote?{style:styleNote}:{})},
          relation:previous?"continue":"independent",predecessor_ids:previous?[previous]:[]
        }},pictures);
        rememberProject(pid);referenceDraft.change(null);setText("");setPictures([]);newProjectRequest.current=null;
        go("#/e/"+result.episodeId+"/story");
      } finally {setQuickStarting(false);}
    } else if (mode === "import") {
      const file = importFile;
      if (!file) throw new Error("请先选择故事稿或剧本文件");
      const pid = await ensureProject(projectId, file.name.replace(/\.[^.]+$/, "").slice(0, 12));
      const fresh = pid !== projectId;
      setProjectId(pid);
      const displayCode=code || (fresh ? "E01" : nextCode());
      const r = await importManuscript(pid,file,purpose);
      setRecognized([{ name: file.name, purpose, episode:displayCode, projectId: pid, ...r }]);
    } else if (mode === "reference") {
      const file = referenceFile;
      if (!file) throw new Error("请选择本地参考片");
      const pid = await ensureProject(projectId, text.slice(0, 12) || "参考片项目");
      setProjectId(pid);
      const src = await upload("reference", file, { projectId: pid });
      if (src.state !== "readable") throw new Error("原片不可读：" + JSON.stringify(src.probe));
      const pack = await cmd("create_pack", { source_id: src.sourceId, request: text });
      go(`#/p/${pid}/ref/${pack.packId}`);
    } else if (mode === "delivery") {
      if (!destination) throw new Error("请先选择有已开工剧集的项目");
      go(`#/e/${destination.id}/delivery`);
    }
  });
  const attention = useAttention(); // 实测 121：须在所有提前 return 之前调用，否则切换页面时 hooks 数量不一致导致白屏
  if (resourceTab) return <main className="main page"><h1>{tab==="library" ? "选择要查看资产库的项目" : "选择要查看选题池的项目"}</h1>
    {listNotice}{list.map(p=><a className="setting-link" key={p.id} href={`#/p/${p.id}/${tab}`}>{p.name}</a>)}
    {projects.data && !list.length && <a href="#/">创建项目</a>}</main>;
  if (tab==="projects" || tab==="deliveries") return <div className="app"><main className="main page"><h1>{tab==="projects"?"项目":"本地交付包"}</h1>
    {listNotice}{tab==="projects" && <><OpenProjectFolder/><ArchiveControls reload={projects.reload}/></>}
    {tab==="projects"?<div className="director-grid">{list.map(p=><ProjectCard p={p} key={p.id}/>)}</div>:list.map(p=><section key={p.id}><h2>{p.name}</h2>{p.deliveries?.length?p.deliveries.map((d:any)=><p key={d.id}><a href={`#/e/${d.episode_id}/delivery?delivery=${encodeURIComponent(d.id)}`}>{d.display_code} · {d.title || p.name} · {d.scope==="full"?"整集":"限定段落"} · {new Date(d.created_at*1000).toLocaleString()}</a></p>):<p>暂无交付记录</p>}</section>)}</main></div>;
  const recent = list.filter(p => p.recentVisible).flatMap(project => project.recent.map((episode:any) => ({episode, project})))
    .sort((a,b) => b.episode.created_at - a.episode.created_at).slice(0,5);
  const statusOf = (name: string) => exec.data?.[name]?.status === "available";
  return (
    <div className="app">
      <main className="main home">
        <div className="row" style={{ justifyContent: "flex-end", paddingTop: 16 }}>
          {["codex", "antigravity"].map((n) => <span key={n} className="chip"><span className="dot" style={{ background: statusOf(n) ? "var(--green)" : "var(--muted)" }} />{PROVIDER_LABEL[n]}</span>)}
          <a className="chip" href="#/deliveries">本地交付包</a>

        </div>
        <div className="hero">
          <h1>和导演助手聊聊这一集</h1>
          <div className="bigbox" style={{ position: "relative" }}
            onPaste={e=>{
              const files = clipboardFiles(e.clipboardData);
              if (files.length) {e.preventDefault();attachPictures(files);}
            }}
            onDragOver={e=>{if(e.dataTransfer.types.includes("Files")) e.preventDefault();}}
            onDrop={e=>{
              if(!e.dataTransfer.files.length) return;
              e.preventDefault();
              const files = Array.from(e.dataTransfer.files);
              if (files.every(isPicture)) {attachPictures(files);return;}
              if(files.length !== 1) { act.run(async()=>{throw new Error("每次请选择一份稿件或参考片");}); return; }
              const file = files[0];
              if(mode==="reference" && file.type.startsWith("video/")) { setReferenceFile(file); return; }
              if(!/\.(md|txt|docx|pdf)$/i.test(file.name)) { act.run(async()=>{throw new Error("稿件支持 MD、TXT、DOCX 或 PDF");}); return; }
              selectMode("import"); setImportFile(file);
            }}>
            <div className="tabs5">{TABS.map(([k, label, Icon]) => (
              <button key={k} className={mode === k ? "on" : ""} onClick={() => selectMode(k)}><Icon size={14} aria-hidden="true"/>{label}</button>))}</div>
            <div className="row" style={{ padding: 14, alignItems: "flex-start", flexWrap: "nowrap" }}>
              {(mode === "import" || mode === "reference") && (
                <label key={mode} className="drop" style={{ cursor: "pointer" }}>
                  <input type="file" style={{ display: "none" }} accept={mode === "reference" ? "video/*" : ".md,.txt,.docx,.pdf"} onChange={(e) => (mode === "reference" ? setReferenceFile : setImportFile)(e.target.files?.[0] ?? null)} />
                  <div>＋<br />{mode === "reference" ? referenceFile?.name ?? "选择本地参考片" : importFile?.name ?? "选择故事稿 / 剧本"}<br /><span className="tiny">{mode === "reference" ? "默认研究全片" : "可拖入 MD、TXT、DOCX 或 PDF"}</span></div>
                </label>
              )}
              {mode === "delivery" ? <label>选择交付的集　<select value={destination?.id ?? ""} onChange={e => setTargetEpisode(e.target.value)}>
                {!opened.length && <option value="">本项目尚无已开工集</option>}{opened.map((e: any) => <option key={e.id} value={e.id}>{e.display_code} · {e.title || e.label}</option>)}</select></label> :
              <textarea style={{ border: 0, background: "transparent", minHeight: 120 }} value={text} onChange={(e) => setText(e.target.value)}
                placeholder={mode === "import" || ((original || topic) && ["idea","episode"].includes(mode)) ? "补充本集要求（选填）" : mode === "reference" ? "希望借鉴什么？例：借检票亭里认人的节奏，把故事放到末班渡轮上" :
                  `${nextCode()}：谁想做什么，遇到了什么意外？`} />}
            </div>
            {kickoffMode && <span className="muted tiny" style={{marginLeft:14}}>可粘贴或拖入故事参考图（PNG、JPEG）</span>}
            {kickoffMode && <DirectorFiles files={pictures} remove={i=>setPictures(pictures.filter((_,n)=>n!==i))}/>}
            {["idea","episode"].includes(mode) && (original || topic || previous) && <div className="row" style={{padding:"0 14px 10px"}}>
              {original && <button className="chip" onClick={()=>setOriginal(null)}>按原文拍 · 《{original.title}》 · 第 {original.offset+1}–{original.offset+Array.from(original.text).length} 字 ×</button>}
              {topic && <button className="chip picked-topic" title={topic.premise} onClick={() => setTopic(null)}>选题 {topic.code}<span>{topic.title || topic.premise}</span> ×</button>}
              {previous && <button className="chip" onClick={() => setPrevious("")}>承接 {opened.find((e: any) => e.id === previous)?.display_code} ×</button>}</div>}
            {mode === "import" && (
              <div className="row" style={{ padding: "0 14px 10px" }}>
                <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                  <option value="story_source">故事稿 → 故事来源</option><option value="script_source">剧本 → 故事来源</option>
                </select>
                <input placeholder={`归到 ${nextCode()}`} value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 120 }} />
                <span className="muted small">开工时选择作为改编来源，或将完整定稿直接整理为05剧本。</span>
              </div>
            )}
            <div className="row home-controls">
              <button className="btn ghost" onClick={() => setPop(pop === "project" ? "" : "project")}>{projectLabel}<ChevronDown size={14} aria-hidden="true"/></button>
              {kickoffMode && <button className="btn ghost" onClick={() => setPop(pop === "style" ? "" : "style")}><Palette size={14} aria-hidden="true"/>风格{paletteCards.length ? " · "+paletteCards.map(c=>c.name).join("、") : styleNote && " · " + styleNote.slice(0,12)}</button>}
              {["idea","episode"].includes(mode) && <button className="btn ghost" onClick={() => setPop(pop === "topic" ? "" : "topic")}><NotebookPen size={14} aria-hidden="true"/>选题{topic && " · " + topic.code}</button>}
              {kickoffMode && <><button className="chip" onClick={() => setPop("settings")}><Ratio size={14} aria-hidden="true"/>{ratio || project.data?.project?.spec_preset?.aspectRatio || "9:16"}</button>
                <button className="btn ghost home-settings" title={`规格预设 · ${PROVIDER_LABEL[creator]} 写 / ${PROVIDER_LABEL[reviewer]} 审 · ${runMode === "manual" ? "手工" : "专家"}`} onClick={() => setPop(pop === "settings" ? "" : "settings")}><span>规格预设 · {PROVIDER_LABEL[creator]} 写 / {PROVIDER_LABEL[reviewer]} 审 · {runMode === "manual" ? "手工" : "专家"}</span></button></>}
              {["idea","episode"].includes(mode)?<div className="quick-start-actions">
                <button data-home-submit className="btn primary" disabled={act.busy || !projectReady} onClick={()=>send()}>{quickStarting?"正在开拍…":"一句话开拍"}</button>
              </div>:<button className="btn primary" style={{ borderRadius: "50%", width: 34, height: 34, padding: 0 }} data-home-submit aria-label="继续" disabled={act.busy || !projectReady || (mode === "delivery" && !destination)} onClick={()=>send()}><ArrowUp size={17} aria-hidden="true"/></button>}
            </div>
            {pop && <Popover kind={pop} close={() => {setPop(choosingResearchOwner&&pop==="project"?"topic":"");if(pop!=="project")setChoosingResearchOwner(false);}} projects={list} projectId={projectId} setProjectId={selectProject}
              topicQuery={topics} projectLabel={projectLabel} configs={configs} listNotice={listNotice}
              key={pop + projectId} project={project.data} topics={(topics.data ?? []).filter(t => t.project_id === projectId)} pickTopic={(t: any) => { if (t.episode_id) { go(`#/e/${t.episode_id}/story`); return; } referenceDraft.change(null);setOriginal(null);setTopic(t); setPop(""); }}
              referenceDraft={referenceDraft} onOriginal={(part:Excerpt)=>{setOriginal(part);setTopic(null);referenceDraft.change(null);setPop("");}} researchOnOpen={choosingResearchOwner} chooseOwner={()=>{setChoosingResearchOwner(true);setPop("project");}}
              addTopic={async (code: string, premise: string) => {
                if(creating){referenceDraft.change(null);setOriginal(null);setTopic({code,premise,pending:commandId()});setPop("");return;}
                const pid = projectId;
                if (pid !== projectId) setProjectId(pid);
                const r = await cmd("add_topic",{project_id:pid,code,premise});
                referenceDraft.change(null);setOriginal(null);setTopic({id:r.topicId,code:r.code || code,premise}); if (pid === projectId) await topics.reload(); setPop("");
              }} styleNote={styleNote} paletteCards={paletteCards} saveStyle={(v: string,cards:PaletteChoice[]) => {setStyleNote(v); setPaletteCards(cards); setPop("");}}
              settings={{ratio,executorPlan,setExecutorPlan,runMode,setRatio,setRunMode,quick:["idea","episode"].includes(mode),
                advanced:["idea","episode"].includes(mode)?()=>act.run(async()=>{
                  if(referenceDraft.value){setPop("topic");throw new Error("请先确定所选原文的用途");}
                  const pid=await ensureProject(projectId,text.trim().slice(0,12)||"新项目");
                  if(pid!==projectId){setProjectId(pid);rememberProject(pid);}
                  setPop("");setImageKickoff({...prefill,projectId:pid,displayCode:code||(pid===projectId?nextCode():"E01"),
                    importId:await storyImport(pid,original),idea:topic||original?undefined:text,topicId:await topicFor(pid),goal:original?"按所选原文剧情改编。"+text:topic?text:""});
                }):undefined}} />}
          </div>
          {existing && project.error && <p role="alert" className="error">当前项目读取失败 · {project.error} <button className="btn" onClick={project.reload}>重试当前项目</button></p>}
          {imageKickoff && <Kickoff projectId={imageKickoff.projectId} prefill={imageKickoff} pictures={pictures} onClose={()=>setImageKickoff(null)}/>}
          <ErrorLine error={act.error} />
          {mode === "import" && recognized.some(r => r.projectId === projectId) && (
            <div className="card line" style={{ maxWidth: 900, margin: "14px auto", textAlign: "left" }}>
              <h3>识别与归位</h3>
              {recognized.filter(r => r.projectId === projectId).map((r) => (
                <div key={r.importId} className="spread">
                  <div>{r.name} → {r.episode} · {r.purpose === "script_source" ? "剧本（故事来源）" : "故事稿（故事来源）"} · {r.extractionMethod && r.extractionMethod+" · "}{r.parseStatus === "parsed" ? <><span className="ok">已解析</span>{r.diagnostic && <span> · {r.diagnostic}</span>}</> : <span className="error">{r.diagnostic}</span>}{r.preview && <details><summary>查看提取正文（前 800 字符）</summary><pre style={{whiteSpace:"pre-wrap"}}>{r.preview}</pre></details>}</div>
                  <button className="btn primary" disabled={r.parseStatus !== "parsed"} onClick={() => openKickoff(r.projectId,{ ...prefill,goal:text,displayCode:r.episode,importId:r.importId })}>去开工 {r.episode}</button>
                </div>))}
            </div>
          )}
          <div className="row" style={{ justifyContent: "center", marginTop: 22 }}>
            {CHIPS.map((c,i) => <button key={c} className="btn ghost" style={{ borderRadius: 20 }} onClick={() => shortcut(i)}>{c}</button>)}
          </div>
        </div>
        {attention.length > 0 && <section aria-label="正在等候外部服务" className="attention-list"><h2>正在等候外部服务</h2><div className="home-project-grid">{attention.map(a =>
          <a key={a.episodeId} className="epcard col attention-card" href={`#/e/${a.episodeId}/story`}>
            <strong>{a.displayCode}{a.title && " · " + a.title} <AttentionTag item={a}/></strong>
            <span className="muted small">{a.projectName} · {a.reason}</span>
          </a>)}</div></section>}
        <div className="spread" style={{ marginTop: 30 }}><h2>最近项目</h2><a className="muted small" href="#/projects">全部 ›</a></div>
        {listNotice}
        <div className="home-project-grid">
          {list.filter(p => p.recentVisible).sort((a,b)=>b.recentAt-a.recentAt).map((p) => <ProjectCard key={p.id} p={p} />)}
        </div>
        {recent.length > 0 && <section aria-label="最近剧集"><h2>最近剧集</h2><div className="home-project-grid">{recent.map(({episode, project}) =>
          <a key={episode.id} className="epcard col" href={`#/e/${episode.id}/${episode.state === "delivered" ? "delivery" : "story"}`}>
            <strong>{episode.display_code}{episode.title && " · " + episode.title} <AttentionTag item={attention.find(a=>a.episodeId===episode.id)}/></strong><span className="muted small">{project.name} · {episode.state === "delivered" ? "查看交付" : "打开故事稿"}</span>
          </a>)}</div></section>}
      </main>
    </div>
  );
}

function ProjectCard({ p }: { p: any }) {
  const [menu, setMenu] = useState(false);
  const [savingFolder,setSavingFolder] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(p.name);
  const [original, setOriginal] = useState(p.name);
  const wrapper = useRef<HTMLDivElement>(null);
  const act = useAction();
  useEffect(() => {
    if (!menu) return;
    const outside = (e: PointerEvent) => { if (!wrapper.current?.contains(e.target as Node)) setMenu(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [menu]);
  const changed = () => dispatchEvent(new Event("projects-changed"));
  const ep=p.recent?.[0];
  const cover=blob(p.cover);
  return (
    <div className="project-card-wrap" ref={wrapper} onContextMenu={e=>{if (!editing) { e.preventDefault(); setMenu(true); }}}>
    <a className="projcard" href={`#/p/${p.id}`}>
      <div className="cover" style={{ backgroundImage: cover ? `linear-gradient(transparent 50%, rgba(0,0,0,.7)), url(${cover})` : undefined }}>
        {ep && <span className="tag" style={{ position: "absolute", left: 10, top: 10 }}>{ep.state==="delivered"?"已交付":"制作中"}</span>}
        <div className="t">{p.name}</div>
      </div>
      <div className="muted small">{p.episodes} 集 · 设定 {p.settingsCount} 条 · {ep ? `${ep.display_code} ${ep.state==="delivered"?"已交付":"制作中"}` : "尚未开工"}</div>
    </a>
    <button className="btn card-actions" aria-label={`管理项目 ${p.name}`} aria-expanded={menu}
      onClick={()=>setMenu(!menu)}>⋯</button>
    {menu && <div className="card-menu">
      <button className="btn" onClick={()=>{setOriginal(p.name);setName(p.name);setMenu(false);setEditing(true);act.clear();}}>重命名</button>
      <button className="btn" onClick={()=>{setMenu(false);setSavingFolder(true);}}>另存独立项目</button>
      <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
        await cmd("set_recent_visibility", {project_id:p.id, visible:!p.recentVisible}); setMenu(false); changed();
      })}>{p.recentVisible ? "从最近项目移除" : "恢复到最近项目"}</button>
    </div>}
    {savingFolder && <SaveProjectFolder pid={p.id} name={p.name} close={()=>setSavingFolder(false)}/>}
    {editing && <Modal width={380} onClose={()=>setEditing(false)}><form onSubmit={e=>{e.preventDefault();act.run(async()=>{
      await cmd("rename_project",{project_id:p.id, name, expected_name:original});setEditing(false);changed();
    });}}>
      <label className="col">项目名称<input autoFocus value={name} onChange={e=>setName(e.target.value)}/></label>
      <div className="row"><button type="submit" className="btn primary" disabled={act.busy || !name.trim()}>保存名称</button>
      <button type="button" className="btn" onClick={()=>setEditing(false)}>取消</button></div>
      <ErrorLine error={act.error}/>
    </form></Modal>}
    {!editing && <ErrorLine error={act.error}/>}
    </div>
  );
}

function Popover({ configs, listNotice, topicQuery, projectLabel, kind, close, projects, projectId, setProjectId, topics, pickTopic, addTopic, styleNote, paletteCards, saveStyle, onOriginal, chooseOwner, researchOnOpen, referenceDraft, settings }: any) {

  const style = { position: "static", width: "auto", margin: "0 14px 14px" } as const;
  const [draft, setDraft] = useState<string>(styleNote);
  const [picked,setPicked]=useState<PaletteChoice[]>(paletteCards);
  const [research,setResearch]=useState(!!researchOnOpen);

  const [browseDraft,setBrowseDraft]=useState(false);
  const existing=!!projectId&&!projectId.startsWith("__new");
  const [topicCode, setTopicCode] = useState("");
  const [premise, setPremise] = useState("");
  const act = useAction();
  if (kind === "project") return (
    <div className="pop" style={style}>
      <h3>选择项目</h3>{listNotice}
      <div className="row">{projects.map((p: any) => <button key={p.id} className={`pill ${p.id === projectId ? "on" : ""}`} onClick={() => { setProjectId(p.id); close(); }}>{projectName(p,projects)}</button>)}
        <button className={`pill ${projectId === "__new" ? "on" : ""}`} onClick={() => { setProjectId("__new"); close(); }}>＋ 新项目（设定为空）</button>
        {!!configs.data?.some((c:any)=>c.configId!=="_template") && <details><summary>用模板新建项目</summary>{configs.data?.filter((c: any) => c.configId !== "_template").map((c: any) => <button key={c.configId} className={`pill ${projectId === "__new:" + c.configId ? "on" : ""}`} onClick={() => { setProjectId("__new:" + c.configId); close(); }}>＋ 新项目（{c.name} 配置）</button>)}</details>}
      </div>
      {configs.error ? <p role="alert" className="error">项目配置读取失败 · {configs.error} <button className="btn" onClick={configs.reload}>重试项目配置</button></p>
        : !configs.data && <p role="status">正在读取项目配置…</p>}
    </div>);
  if (kind === "style") return (
    <div className="pop" style={style}>
      <h3>本集画面风格</h3>
      <ResourceBrowser kind="palette" episodePicker={{selected:picked,change:setPicked}} homePicker/>
      <label className="col">本集风格说明<input value={draft} onChange={e => setDraft(e.target.value)} placeholder="例如：清晨冷光，纸船保留暖红色" /></label>
      <div className="row"><button className="btn primary" onClick={() => saveStyle(draft.trim(),picked)}>应用到本集</button><button className="btn" onClick={() => saveStyle("",[])}>使用项目默认</button></div>
    </div>);
  if (kind === "topic") return (
    <div className="pop col" style={style}><h3>{projectLabel} · 选题池</h3>
      {existing&&!topicQuery.data&&!topicQuery.error&&<p role="status">正在读取项目选题…</p>}
      {topicQuery.error&&<span className="error">{topicQuery.error} <button className="btn" onClick={topicQuery.reload}>重新读取</button></span>}
      <div className="home-topic-list">{topics.filter(isKickoffTopic).map((t: any) =>
        <button key={t.id} className="btn ghost home-topic-option" onClick={() => pickTopic(t)}>
          <b>{t.code}</b><span>{t.title && <strong className="topic-option-title">{t.title}</strong>}<span className="topic-option-summary">{t.premise}</span></span><span>选用</span>
        </button>)}</div>
      {existing&&topicQuery.data&&!topics.some(isKickoffTopic)&&
        <span className="muted small">{topics.length?"本项目目前没有可开工的候选卡。":"本项目还没有选题卡。"}可以填写故事前提新建一张，也可以展开选题研究。</span>}
      {referenceDraft.value&&<ResearchReference reference={referenceDraft.value} replace={()=>setBrowseDraft(true)} clear={()=>referenceDraft.change(null)}/>}
      {referenceDraft.value&&<div className="row"><button className="btn primary" onClick={()=>onOriginal(referenceDraft.value)}>按原文改编</button>
        <button className="btn" onClick={()=>existing?setResearch(!research):chooseOwner()}>{existing?"参考这段内容想新题":"选择已有项目研究备选题"}</button></div>}
      <div className="row"><input placeholder="编号（自动生成）" value={topicCode} onChange={e => setTopicCode(e.target.value)} /><input placeholder="故事想法" value={premise} onChange={e => setPremise(e.target.value)} /></div>
      <div className="row"><button className="btn primary" disabled={!projectId || !premise.trim() || act.busy} onClick={() => act.run(() => {
        if (topics.some((t: any) => t.code === topicCode.trim())) throw new Error("该选题编号已存在，请选择原卡或换一个编号");
        return addTopic(topicCode.trim(),premise.trim());
      })}>保存并选用</button>{projectId && !projectId.startsWith("__new") && <a className="btn" href={`#/p/${projectId}/topics`}>查看全部选题</a>}{!referenceDraft.value&&<button className="btn" onClick={()=>{setBrowseDraft(true);}}>从知识库选参考</button>}
        {existing&&!referenceDraft.value&&<button className="btn" aria-expanded={research} onClick={()=>setResearch(!research)}>{research?"收起选题研究":"让 AI 想选题"}</button>}</div>
      {existing&&research&&<TopicResearch projectId={projectId} poolChanged={topicQuery.reload} hidePicker referenceDraft={referenceDraft} onPick={pickTopic}/>}
      {browseDraft&&<Modal width={960} onClose={()=>setBrowseDraft(false)}><button className="btn" onClick={()=>setBrowseDraft(false)}>返回选题</button>
        <ResourceBrowser kind="knowledge" storyPicker onChoose={part=>{referenceDraft.change(part);setBrowseDraft(false);}}/></Modal>}<ErrorLine error={act.error} />
    </div>);
  return <div className="pop col" style={style}><h3>本集生成设置</h3>
    <label>画幅　<select value={settings.ratio} onChange={e => settings.setRatio(e.target.value)}><option value="">沿用项目预设</option>{["9:16","16:9","1:1","2:3","3:2","3:4","4:3","21:9"].map(v => <option key={v}>{v}</option>)}</select></label>
    <ExecutorPlanFields plan={settings.executorPlan} onChange={settings.setExecutorPlan}/>
    <label>运行模式　<select aria-label="运行模式" value={settings.runMode} onChange={e => settings.setRunMode(e.target.value)}><option value="expert">专家</option><option value="manual">手工</option></select></label>
    {settings.quick && <p className="muted small">{settings.runMode==="manual"?"开工后进入本集，由你逐阶段推进。":"专家：开拍后由 AI 连续往下做，自己处理审查与返修，只在需要你取舍时给你选择。"}</p>}
    {settings.advanced && <button className="btn" onClick={settings.advanced}>调整完整开工设置…</button>}
    <button className="btn primary" onClick={close}>完成</button></div>;
}
