/* eslint-disable @typescript-eslint/no-explicit-any */
import { isKickoffTopic } from "./topics";
import { FileInput, PencilLine, Plus, PackageOpen, Film, Palette, NotebookPen, Ratio, ChevronDown, ArrowUp, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { blob, cmd, upload, importManuscript } from "./api";
import {OpenProjectFolder, SaveProjectFolder} from "./ProjectFolders";
import type { useProjectList } from "./projectNavigation";
import { ErrorLine, Modal, PROVIDER_LABEL, go, useAction, useQuery } from "./ui";

const TABS: [string, string, LucideIcon][] = [["import", "导入故事稿 / 剧本", FileInput], ["idea", "从一个想法开始", PencilLine],
  ["episode", "给项目开一集", Plus], ["delivery", "图文交付", PackageOpen], ["reference", "从参考片写剧本", Film]];
const CHIPS = ["/ 从候选选题开一集", "/ 选题研究", "/ 承接上一集开新一集", "/ 审一份已有故事稿", "/ 修改进行中的一段"];
async function ensureProject(pid: string, name: string) {
  if (pid && !pid.startsWith("__new")) return pid;
  const config = pid.startsWith("__new:") ? pid.slice(6) : "_template";
  return (await cmd("create_project", { config_id: config, name: config === "_template" ? name : undefined })).projectId;
}

export default function Home({ tab, projects }: { tab?: string; projects: ReturnType<typeof useProjectList> }) {
  const [mode, setMode] = useState(TABS.some(([k]) => k === tab) ? tab! : "idea");
  const [text, setText] = useState("");
  const [projectId, setProjectId] = useState("");
  const [pop, setPop] = useState<"" | "style" | "topic" | "settings" | "project">("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [styleNote, setStyleNote] = useState("");
  const [topic, setTopic] = useState<any>(null);
  const [previous, setPrevious] = useState("");
  const [ratio, setRatio] = useState("");
  const [creator, setCreator] = useState("claude");
  const [reviewer, setReviewer] = useState("codex");
  const [runMode, setRunMode] = useState("manual");
  const [targetEpisode, setTargetEpisode] = useState("");
  const [purpose, setPurpose] = useState("story_source");
  const [code, setCode] = useState("");
  const [recognized, setRecognized] = useState<any[]>([]);
  const creating = projectId.startsWith("__new");
  const existing = !!projectId && !projectId.startsWith("__new");
  const project = useQuery<any>(existing ? "project_overview" : null, { project_id: projectId });
  const topics = useQuery<any[]>(existing ? "topics" : null, { project_id: projectId });
  const configs=useQuery<any[]>("available_configs");
  const sourceCards = useQuery<any[]>(existing && pop === "topic" ? "source_topics" : null, {project_id:projectId});
  const exec = useQuery<any>("probe");
  const act = useAction();
  const list = projects.data ?? [];
  useEffect(() => { if (!projectId && projects.data) setProjectId(list.find((p) => p.id === localStorage.getItem("lastProject"))?.id ?? list[0]?.id ?? "__new"); }, [projectId, projects.data]);
  useEffect(() => {
    if (tab && ["library", "topics"].includes(tab) && projectId && !projectId.startsWith("__new")) go(`#/p/${projectId}/${tab}`);
  }, [tab, projectId]);
  const configName = configs.data?.find(c => "__new:" + c.configId === projectId)?.name;
  const projectLabel = list.find(p => p.id === projectId)?.name ?? (configName ? `新项目（${configName}）` : creating ? "新项目" : "选择项目");
  const projectReady = creating || (existing && project.data?.project.id === projectId);
  const listNotice = projects.error
    ? <div role="alert" className="error">项目列表读取失败 · {projects.error} <button className="btn" onClick={projects.reload}>重试项目列表</button></div>
    : !projects.data && <p role="status">正在读取项目列表…</p>;
  const nextCode = () => {
    const eps = project.data?.project.id === projectId ? project.data.episodes : [];
    const nums = eps.map((e: any) => parseInt(e.display_code.replace(/\D/g, ""), 10)).filter((n: number) => !isNaN(n));
    const prefix = eps[0]?.display_code.replace(/\d+/g, "") || "E";
    return `${prefix}${String((nums.length ? Math.max(...nums) : 0) + 1).padStart(2, "0")}`;
  };
  const episodes = project.data?.project.id === projectId ? project.data.episodes : [];
  const opened = episodes.filter((e: any) => e.kickoff_id);
  const destination = opened.find((e: any) => e.id === targetEpisode) ?? opened[opened.length - 1];
  const kickoffMode = ["idea","episode","import"].includes(mode);
  const prefill = { style: styleNote, ratio, creator, reviewer, mode: runMode, predecessorId: previous };
  const selectProject = (pid: string) => {
    localStorage.setItem("lastProject",pid); setProjectId(pid); setRecognized([]); setTopic(null); setPrevious(""); setRatio(""); setStyleNote(""); setTargetEpisode("");
  };
  const selectMode = (value: string) => { if (value !== mode) {setTopic(null); setPrevious("");} setMode(value); setPop(""); act.clear(); };
  const shortcut = (index: number) => act.run(async () => {
    if (index === 0) { selectMode("episode"); setPop("topic"); }
    if (index === 1) {
      if (!existing) {setPop("project");return;}
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
      go(`#/e/${ep.id}/${ep.adopted.plan ? "prompts" : ep.adopted.script ? "plan" : ep.adopted.story ? "script" : "story"}`);
    }
  });
  const send = () => act.run(async () => {
    if (mode === "idea" || mode === "episode") {
      if (mode === "idea" && !topic && !text.trim()) throw new Error("请输入一句话想法或选择一张选题卡");
      const pid = await ensureProject(projectId, text.slice(0, 12) || "新项目");
      const displayCode=code || (pid === projectId ? nextCode() : "E01");
      sessionStorage.setItem("kickoff-prefill:"+pid, JSON.stringify({ ...prefill, projectId:pid, displayCode, idea: topic ? undefined : text, topicId: topic?.id, goal: topic ? text : "" }));
      go(`#/p/${pid}`);
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
  if (tab==="projects" || tab==="deliveries") return <div className="app"><main className="main page"><h1>{tab==="projects"?"项目":"本地交付包"}</h1>
    {listNotice}{tab==="projects" && <OpenProjectFolder/>}
    {tab==="projects"?<div className="director-grid">{list.map(p=><ProjectCard p={p} key={p.id}/>)}</div>:list.map(p=><section key={p.id}><h2>{p.name}</h2>{p.deliveries?.length?p.deliveries.map((d:any)=><p key={d.id}><a href={`#/e/${d.episode_id}/delivery`}>{d.display_code} · {d.title || p.name} · {d.scope==="full"?"整集":"限定段落"} · {new Date(d.created_at*1000).toLocaleString()}</a></p>):<p>暂无交付记录</p>}</section>)}</main></div>;
  const recent = list.filter(p => p.recentVisible).flatMap(project => project.recent.map((episode:any) => ({episode, project})))
    .sort((a,b) => b.episode.created_at - a.episode.created_at).slice(0,5);
  const statusOf = (name: string) => exec.data?.[name]?.status === "available";
  return (
    <div className="app">
      <main className="main home">
        <div className="row" style={{ justifyContent: "flex-end", paddingTop: 16 }}>
          {["claude", "codex"].map((n) => <span key={n} className="chip"><span className="dot" style={{ background: statusOf(n) ? "var(--green)" : "var(--muted)" }} />{n === "claude" ? "Claude Code" : "Codex"}</span>)}
          <a className="chip" href="#/deliveries">本地交付包</a>

        </div>
        <div className="hero">
          <h1>和导演助手聊聊这一集</h1>
          <div className="bigbox" style={{ position: "relative" }}
            onDragOver={e=>{if(e.dataTransfer.types.includes("Files")) e.preventDefault();}}
            onDrop={e=>{
              if(!e.dataTransfer.files.length) return;
              e.preventDefault();
              const files = e.dataTransfer.files;
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
                placeholder={mode === "import" || (topic && ["idea","episode"].includes(mode)) ? "补充本集要求（选填）" : mode === "reference" ? "希望借鉴什么？例：借检票亭里认人的节奏，把故事放到末班渡轮上" :
                  `${nextCode()}：谁想做什么，遇到了什么意外？`} />}
            </div>
            {["idea","episode"].includes(mode) && (topic || previous) && <div className="row" style={{padding:"0 14px 10px"}}>
              {topic && <button className="chip" onClick={() => setTopic(null)}>选题 {topic.code} · {topic.premise} ×</button>}
              {previous && <button className="chip" onClick={() => setPrevious("")}>承接 {opened.find((e: any) => e.id === previous)?.display_code} ×</button>}</div>}
            {mode === "import" && (
              <div className="row" style={{ padding: "0 14px 10px" }}>
                <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                  <option value="story_source">故事稿 → 故事来源</option><option value="script_source">剧本 → 故事来源</option>
                </select>
                <input placeholder={`归到 ${nextCode()}`} value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 120 }} />
                <span className="muted small">导入的故事稿、剧本只作故事来源，开工后先写故事稿并过独审。</span>
              </div>
            )}
            <div className="row home-controls">
              <button className="btn ghost" onClick={() => setPop(pop === "project" ? "" : "project")}>{projectLabel}<ChevronDown size={14} aria-hidden="true"/></button>
              {kickoffMode && <button className="btn ghost" onClick={() => setPop(pop === "style" ? "" : "style")}><Palette size={14} aria-hidden="true"/>风格{styleNote && " · " + styleNote.slice(0,12)}</button>}
              {["idea","episode"].includes(mode) && <button className="btn ghost" onClick={() => setPop(pop === "topic" ? "" : "topic")}><NotebookPen size={14} aria-hidden="true"/>选题{topic && " · " + topic.code}</button>}
              {kickoffMode && <><button className="chip" onClick={() => setPop("settings")}><Ratio size={14} aria-hidden="true"/>{ratio || project.data?.project?.spec_preset?.aspectRatio || "9:16"}</button>
                <button className="btn ghost" onClick={() => setPop(pop === "settings" ? "" : "settings")}>规格预设 · {PROVIDER_LABEL[creator]} 写 / {PROVIDER_LABEL[reviewer]} 审 · {runMode === "manual" ? "手工" : "专家"}</button></>}
              <button className="btn primary" style={{ borderRadius: "50%", width: 34, height: 34, padding: 0 }} aria-label="继续" disabled={act.busy || !projectReady || (mode === "delivery" && !destination)} onClick={send}><ArrowUp size={17} aria-hidden="true"/></button>
            </div>
            {pop && <Popover kind={pop} close={() => setPop("")} projects={list} projectId={projectId} setProjectId={selectProject}
              configs={configs} listNotice={listNotice} sourceCards={sourceCards} takeCard={async (c:any) => {const r=await cmd("take_source_topic",{project_id:projectId,code:c.code,sha256:c.sha256}); setTopic({id:r.topicId,code:c.code,premise:c.premise}); await topics.reload(); setPop("");}}
              key={pop + projectId} project={project.data} topics={(topics.data ?? []).filter(t => t.project_id === projectId)} pickTopic={(t: any) => { if (t.episode_id) { go(`#/e/${t.episode_id}/story`); return; } setTopic(t); setPop(""); }}
              addTopic={async (code: string, premise: string) => {
                const pid = await ensureProject(projectId, premise.slice(0,12));
                if (pid !== projectId) setProjectId(pid);
                const r = await cmd("add_topic",{project_id:pid,code,premise});
                setTopic({id:r.topicId,code,premise}); if (pid === projectId) await topics.reload(); setPop("");
              }} styleNote={styleNote} saveStyle={(v: string) => {setStyleNote(v); setPop("");}}
              settings={{ratio,creator,reviewer,runMode,setRatio,setCreator,setReviewer,setRunMode}} />}
          </div>
          {existing && project.error && <p role="alert" className="error">当前项目读取失败 · {project.error} <button className="btn" onClick={project.reload}>重试当前项目</button></p>}
          <ErrorLine error={act.error} />
          {mode === "import" && recognized.some(r => r.projectId === projectId) && (
            <div className="card line" style={{ maxWidth: 900, margin: "14px auto", textAlign: "left" }}>
              <h3>识别与归位</h3>
              {recognized.filter(r => r.projectId === projectId).map((r) => (
                <div key={r.importId} className="spread">
                  <div>{r.name} → {r.episode} · {r.purpose === "script_source" ? "剧本（故事来源）" : "故事稿（故事来源）"} · {r.extractionMethod && r.extractionMethod+" · "}{r.parseStatus === "parsed" ? <><span className="ok">已解析</span>{r.diagnostic && <span> · {r.diagnostic}</span>}</> : <span className="error">{r.diagnostic}</span>}{r.preview && <details><summary>查看提取正文（前 800 字符）</summary><pre style={{whiteSpace:"pre-wrap"}}>{r.preview}</pre></details>}</div>
                  <button className="btn primary" disabled={r.parseStatus !== "parsed"} onClick={() => { sessionStorage.setItem("kickoff-prefill:"+r.projectId, JSON.stringify({ ...prefill, goal:text, displayCode:r.episode, projectId:r.projectId, importId: r.importId })); go(`#/p/${r.projectId}`); }}>去开工 {r.episode}</button>
                </div>))}
            </div>
          )}
          <div className="row" style={{ justifyContent: "center", marginTop: 22 }}>
            {CHIPS.map((c,i) => <button key={c} className="btn ghost" style={{ borderRadius: 20 }} onClick={() => shortcut(i)}>{c}</button>)}
          </div>
        </div>
        <div className="spread" style={{ marginTop: 30 }}><h2>最近项目</h2><a className="muted small" href="#/projects">全部 ›</a></div>
        {listNotice}
        <div className="home-project-grid">
          {list.filter(p => p.recentVisible).sort((a,b)=>b.recentAt-a.recentAt).map((p) => <ProjectCard key={p.id} p={p} />)}
        </div>
        {recent.length > 0 && <section aria-label="最近剧集"><h2>最近剧集</h2><div className="home-project-grid">{recent.map(({episode, project}) =>
          <a key={episode.id} className="epcard col" href={`#/e/${episode.id}/${episode.state === "delivered" ? "delivery" : "story"}`}>
            <strong>{episode.display_code}{episode.title && " · " + episode.title}</strong><span className="muted small">{project.name} · {episode.state === "delivered" ? "查看交付" : "打开故事稿"}</span>
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

function Popover({ configs, listNotice, kind, close, projects, projectId, setProjectId, topics, sourceCards, takeCard, pickTopic, addTopic, styleNote, saveStyle, settings }: any) {

  const style = { position: "static", width: "auto", margin: "0 14px 14px" } as const;
  const [draft, setDraft] = useState<string>(styleNote);
  const [sourceCode,setSourceCode] = useState("");
  const [topicCode, setTopicCode] = useState("");
  const [premise, setPremise] = useState("");
  const act = useAction();
  if (kind === "project") return (
    <div className="pop" style={style}>
      <h3>选择项目</h3>{listNotice}
      <div className="row">{projects.map((p: any) => <button key={p.id} className={`pill ${p.id === projectId ? "on" : ""}`} onClick={() => { setProjectId(p.id); close(); }}>{p.name}</button>)}
        <button className={`pill ${projectId === "__new" ? "on" : ""}`} onClick={() => { setProjectId("__new"); close(); }}>＋ 新项目（设定为空）</button>
        {configs.data?.filter((c: any) => c.configId !== "_template").map((c: any) => <button key={c.configId} className={`pill ${projectId === "__new:" + c.configId ? "on" : ""}`} onClick={() => { setProjectId("__new:" + c.configId); close(); }}>＋ 新项目（{c.name} 配置）</button>)}
      </div>
      {configs.error ? <p role="alert" className="error">项目配置读取失败 · {configs.error} <button className="btn" onClick={configs.reload}>重试项目配置</button></p>
        : !configs.data && <p role="status">正在读取项目配置…</p>}
    </div>);
  if (kind === "style") return (
    <div className="pop" style={style}>
      <h3>本集画面风格</h3>
      <a className="btn" href={"#/palette"+(projectId&&!projectId.startsWith("__new")?"?project="+encodeURIComponent(projectId):"")}>浏览导演色卡并设置项目风格</a>
      <div className="label">色彩灵感</div><div className="row">{[["#2b3a55","深蓝灰"],["#c9a227","金黄"],["#1f5f4a","墨绿"],["#e07a5f","珊瑚橙"]].map(([c,label]) =>
        <button key={c} className="pill" onClick={() => setDraft(`主色调：${label}（${c}）`)}><i style={{display:"inline-block",width:12,height:12,background:c}} /> {label}</button>)}</div>
      <label className="col">本集风格说明<input value={draft} onChange={e => setDraft(e.target.value)} placeholder="例如：清晨冷光，纸船保留暖红色" /></label>
      <div className="row"><button className="btn primary" onClick={() => saveStyle(draft.trim())}>应用到本集</button><button className="btn" onClick={() => saveStyle("")}>使用项目默认</button></div>
    </div>);
  if (kind === "topic") return (
    <div className="pop col" style={style}><h3>从选题池取卡</h3>
      {sourceCards.error && <span className="error">{sourceCards.error}</span>}
      {!!sourceCards.data?.length && <><label>项目原始选题池<select value={sourceCode} onChange={e=>setSourceCode(e.target.value)}><option value="">选择原卡（保留原文与来源）</option>{sourceCards.data.filter((c:any)=>!topics.some((t:any)=>t.code===c.code)).map((c:any)=><option key={c.code} value={c.code}>{c.code} · {c.status} · {c.premise}</option>)}</select></label><button className="btn" disabled={!sourceCode || act.busy} onClick={()=>act.run(()=>takeCard(sourceCards.data.find((c:any)=>c.code===sourceCode)))}>选用原卡</button></>}
      {topics.filter(isKickoffTopic).map((t: any) =>
        <button key={t.id} className="btn ghost" onClick={() => pickTopic(t)}>{t.code} · {t.premise} · 选用</button>)}
      {!topics.some(isKickoffTopic) && <span className="muted small">当前没有可选卡片，可直接新建。</span>}
      <div className="row"><input placeholder="选题编号" value={topicCode} onChange={e => setTopicCode(e.target.value)} /><input placeholder="故事前提" value={premise} onChange={e => setPremise(e.target.value)} /></div>
      <div className="row"><button className="btn primary" disabled={!projectId || !topicCode.trim() || !premise.trim() || act.busy} onClick={() => act.run(() => {
        if (topics.some((t: any) => t.code === topicCode.trim())) throw new Error("该选题编号已存在，请选择原卡或换一个编号");
        return addTopic(topicCode.trim(),premise.trim());
      })}>新建并选用</button>{projectId && !projectId.startsWith("__new") && <a className="btn" href={`#/p/${projectId}/topics`}>管理选题池</a>}</div><ErrorLine error={act.error} />
    </div>);
  return <div className="pop col" style={style}><h3>本集生成设置</h3>
    <label>画幅　<select value={settings.ratio} onChange={e => settings.setRatio(e.target.value)}><option value="">沿用项目预设</option>{["9:16","16:9","1:1","2:3","3:2","3:4","4:3","21:9"].map(v => <option key={v}>{v}</option>)}</select></label>
    {([["起草",settings.creator,settings.setCreator],["独审",settings.reviewer,settings.setReviewer]] as const).map(([label,value,set]) =>
      <label key={label}>{label}　<select aria-label={label} value={value} onChange={e => set(e.target.value)}>{Object.entries(PROVIDER_LABEL).map(([k,l]) => <option key={k} value={k}>{l}</option>)}</select></label>)}
    <label>运行模式　<select value={settings.runMode} onChange={e => settings.setRunMode(e.target.value)}><option value="manual">手工</option><option value="expert">专家</option></select></label>
    <button className="btn primary" onClick={close}>完成</button></div>;
}
