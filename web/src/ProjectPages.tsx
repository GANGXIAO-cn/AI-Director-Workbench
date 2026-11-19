import { ImageAPISettings } from "./ImageAPISettings";
import {ExecutorDefaults} from "./ExecutorDefaults";
import { SeriesArchive } from "./SeriesArchive";
import {useStoryImport} from "./storyExcerpt";
import { FILM_STAGES } from "./workflow";
import { ResourceBrowser } from "./Resources";
import {ExecutorConnection} from "./ExecutorConnection";
import { ProjectBaselines } from "./ProjectBaselines";
import { TopicRuntimeTag, runtimeLabel, useManualRuntime } from "./TopicRuntime";
import { isKickoffTopic } from "./topics";
import { TopicResearch } from "./TopicResearch";
import { FreeChat } from "./FreeChat";
import { UploadedMethods, ProjectMethodSync } from "./UploadedMethods";
import { MethodChanges, FrozenMethods } from "./MethodChanges";
import { Providers } from "./Providers";
import { DisplaySettings } from "./DisplayPreferences";
import { useProjectRoute } from "./projectNavigation";
import { ProjectLibrary, HistoricalChecks } from "./ProjectLibrary";
import { ProjectPresets } from "./ProjectPresets";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { Fragment, useEffect, useState } from "react";
import { blob, cmd, commandId, q, setCliPath, upload } from "./api";
import { ErrorLine, Modal, PROVIDER_LABEL, pickable, Pills, Tag, fmtMs, useAction, usePoll, useQuery } from "./ui";

const settingTitles:Record<string,string>={"cat-no-dialogue":"两只猫没有对白","biped-paws":"两只猫怎样走路和拿东西","milo-active":"MILO 主动推动故事","leo-own-goal":"LEO 和主人的行动","owner-relationship":"主人和猫的关系","tone-not-cruel":"喜剧的语气","complete-light-continuity":"每集怎样联系前集","recent-difference":"与近期已拍作品的差异"};
const CATS: Record<string, string> = { character: "角色", relationship: "关系", space: "常用场景", rule: "世界规则", signature: "角色的习惯动作", prop: "道具" };

export function ProjectPage({ id, sub, arg, pickTopic }: { pickTopic:(topic:any)=>void; id: string; sub: string; arg?: string }) {
  const route=useProjectRoute();
  const p = useQuery<any>("project_view", { project_id: id }, [sub, arg]);   // re-read on every page switch
  useEffect(() => { if (sub === "expert") { const episode=new URLSearchParams(route.hash.split("?")[1]).get("episode"); route.go(episode ? `#/e/${encodeURIComponent(episode)}/story` : `#/p/${id}`); } }, [sub, id, route.hash]);
  const head = <div className="spread" style={{ marginBottom: 18 }}><div className="row"><a className="muted" href={`#/p/${id}`}>← {p.data?.name ?? "项目"}</a></div></div>;
  const body = (() => {
    switch (sub) {
      case "free": return <FreeChat key={route.hash} pid={id} thread={arg}/>;
      case "settings": return <Settings id={id} p={p.data} reload={p.reload} />;
      case "preset": return <><ProjectPresets key={id} pid={id} showProjectLink/><PublishSettings id={id}/></>;
      case "baseline": return <ProjectBaselines pid={id} />;
      case "library": return <Library key={id} id={id} p={p.data} />;
      case "knowledge": return <Knowledge key={id} id={id} />;
      case "topics": return <Topics key={id} id={id} pickTopic={pickTopic} />;
      case "groups": return <Groups id={id} p={p.data} />;
      case "closure": return <Closure id={id} cid={arg!} />;
      case "closure-start": return <ClosureStart id={id} did={arg!} />;
      case "ref": return <RefFilm id={id} packId={arg!} p={p.data} />;
      case "expert": return null;
      default: return <div className="empty">未知页面</div>;
    }
  })();
  return <div className="page">{head}{body}</div>;
}

// ---------------------------------------------------------------- J31 设定
function Settings({ id, p, reload }: any) {
  const reasonDefault=new URLSearchParams(location.hash.split("?")[1] || "").get("reason_default") || undefined;
  const [cat, setCat] = useState("all");
  const [edit, setEdit] = useState<any>(null);
  const act = useAction();
  if (!p) return null;
  const list = p.settings.filter((s: any) => cat === "all" || s.category === cat);
  return (
    <div>
      <div className="spread"><div><h1>项目设定</h1><div className="muted small">{p.settings.length} 条 · 修订 {p.settings_revision} · 新分集开工时使用最新设定。</div></div><button className="btn primary" onClick={()=>setEdit({isNew:true,key:commandId(),category:cat==="all"?"rule":cat,content:"",strength:"required",reason:""})}>新增设定</button></div>
      <div className="row" style={{ margin: "14px 0" }}><button className={`pill ${cat === "all" ? "on" : ""}`} onClick={() => setCat("all")}>全部</button>
        {Object.entries(CATS).map(([k, l]) => <button key={k} className={`pill ${cat === k ? "on" : ""}`} onClick={() => setCat(k)}>{l}</button>)}</div>
      <ProjectMethodSync key={id} pid={id}/>
      {!list.length && <p>{p.settings.length?"这个分类还没有设定。":"这个项目还没有设定。"}</p>}
      {list.map((s: any) => (
        <div key={s.key} className="card line" style={{ marginBottom: 8 }}>
          <div className="spread"><div className="row"><Tag tone={s.strength === "required" ? "red" : "blue"}>{s.strength === "required" ? "每集必须遵守" : "默认遵守，可按剧情调整"}</Tag><Tag>{CATS[s.category] ?? s.category}</Tag><code className="mono">{settingTitles[s.key]??s.content.slice(0,16)}</code></div>
            <button className="btn" onClick={() => setEdit({ ...s, reason: "" })}>编辑设定</button></div>
          <div style={{ margin: "8px 0" }}>{s.content}</div>
          <div className="muted tiny">出处：{s.source_ref}{s.user_reason ? ` · 补充说明：${s.user_reason}` : ""}</div>
        </div>))}
      {edit && (
        <Modal onClose={()=>setEdit(null)}>
          <h2>{edit.isNew?"新增项目设定":"编辑 "+(settingTitles[edit.key]??"项目设定")}</h2>
          {edit.isNew && <label>分类 <select aria-label="分类" value={edit.category} onChange={e=>setEdit({...edit,category:e.target.value})}>{Object.entries(CATS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>}
          <label>设定内容
          <textarea value={edit.content} onChange={(e) => setEdit({ ...edit, content: e.target.value })} /></label>
          <label>这条设定每集都要遵守吗？</label><div className="row"><Pills value={edit.strength} onChange={(v: string) => setEdit({ ...edit, strength: v })} options={[["required", "每集必须遵守"], ["variable", "默认遵守，可按剧情调整"]]} />
            <input style={{ flex: 1 }} placeholder="补充说明（选填）" value={edit.reason} onChange={(e) => setEdit({ ...edit, reason: e.target.value })} /></div>
          <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => setEdit(null)}>取消</button>
            <button className="btn primary" disabled={act.busy || !edit.content.trim()} onClick={() => act.run(async () => {
              await cmd("propose_setting_change", {project_id:id,key:edit.key,category:edit.category,content:edit.content.trim(),strength:edit.strength,reason:edit.reason,reason_default:reasonDefault});
              if(edit.isNew && cat!=="all") setCat(edit.category);
              setEdit(null); await reload();
            })}>{edit.isNew?"保存设定":"保存修改"}</button></div>
          <ErrorLine error={act.error}/>
        </Modal>)}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- J48 / J32 复用资产库
const assetCategories:Record<string,string> = {identity:"身份图",costume:"服装参考",scene:"空景",state:"场面 / 状态图",prop:"道具",product:"商品",reference:"参考图"};

function LibraryUpload({projectId,previous,done,close}:{projectId:string;previous:any;done:(id:string)=>void;close:()=>void}) {
  const [file,setFile]=useState<File|null>(null);
  const [category,setCategory]=useState(previous?.category || "state");
  const [usage,setUsage]=useState(previous?.usage || "");
  const [request,setRequest]=useState(commandId);
  const act=useAction();
  async function save() {
    if (!file) return;
    const r=await upload("library-image",file,{projectId,category,usage,previousEntryId:previous?.id,commandId:request});
    done(r.entryId);
  }
  return <Modal onClose={close}><h2>{previous ? "为 "+previous.name+" 添加版本" : "添加资产图片"}</h2>
    <label className="field">图片文件 <input type="file" accept="image/png,image/jpeg" onChange={e=>{setFile(e.target.files?.[0]??null);setRequest(commandId());}}/></label>
    <label className="field">图片分类 <select value={category} disabled={!!previous} onChange={e=>{setCategory(e.target.value);setRequest(commandId());}}>
      {Object.entries(assetCategories).map(([key,label])=><option key={key} value={key}>{label}</option>)}
      {previous && !assetCategories[category] && <option value={category}>{category}</option>}</select></label>
    <label className="field">用途 <input placeholder="可不填" value={usage} onChange={e=>{setUsage(e.target.value);setRequest(commandId());}}/></label>
    <ErrorLine error={act.error}/><div className="row"><button className="btn primary" disabled={act.busy||!file} onClick={()=>act.run(save)}>保存到资产库</button>
      <button className="btn" disabled={act.busy} onClick={close}>取消</button></div></Modal>;
}

function Library({ id, p }: any) {
  const [qText, setQ] = useState("");
  const [status, setStatus] = useState("");
  const route=useProjectRoute();
  const [category,setCategory]=useState("");
  const [uploading,setUploading]=useState<{previous:any}|null>(null);
  const [attached,setAttached]=useState<string[]>([]);
  const lib = useQuery<any[]>("library", {project_id:id});
  const entries=(lib.data ?? []).filter(e=>(!qText || e.name.includes(qText) || e.usage.includes(qText)) && (!status || status===e.status) && (!category || e.category===category));
  const availableCategories=[...new Set((lib.data??[]).map(e=>e.category as string))];
  const selectedChat=new URLSearchParams(attached.map(id=>["image",id]));

  const [detailId,setDetailId] = useState("");
  const detail = entries.find(item=>item.id===detailId);
  const [limits,setLimits] = useState<string|null>(null);
  const labels: Record<string,string> = {pending:"待审核",approved:"可复用",limited:"限条件",retired:"停用",rejected:"未通过"};
  const [target, setTarget] = useState("");
  const act = useAction();
  const existingBase=detail?.source_episode_id===target ? detail?.asset_version_id :
    detail?.usedBy.find((u:any)=>u.episode_id===target)?.copy_version;
  return (
    <div>
      <div className="spread"><div><h1>复用资产库</h1><div className="muted small">每个条目对应一版原图。核准后可复制到集内或用作改图底图。</div></div>
        <div className="row"><button className="btn primary" onClick={()=>setUploading({previous:null})}>添加图片</button>
          <select aria-label="按资产分类筛选" value={category} onChange={e=>setCategory(e.target.value)}><option value="">全部分类</option>
            {availableCategories.map(key=><option key={key} value={key}>{assetCategories[key]||key}</option>)}</select>
          <input placeholder="名称 / 用途" value={qText} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="资产审核状态" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option>{["pending", "approved", "limited", "retired","rejected"].map((s) => <option key={s} value={s}>{labels[s]}</option>)}</select></div></div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 12, marginTop: 16 }}>
        {entries.map((e) => (
          <div key={e.id} className={`card ${detail?.id === e.id ? "sel" : "line"}`} style={{ cursor: "pointer" }} onClick={() => {setDetailId(e.id);setLimits(null);}}>
            <img src={blob(e.sha256)} alt={e.name+" v"+e.version_no} style={{ width: "100%", borderRadius: 8 }} />
            <div className="spread" style={{ marginTop: 6 }}><b>{e.name}</b><Tag tone={e.status === "approved" ? "green" : e.status === "pending" ? "amber" : e.status === "retired" ? "" : "blue"}>{labels[e.status]}</Tag></div>
            <div className="muted tiny">v{e.version_no} · {e.width}×{e.height} · {assetCategories[e.category]||e.category} · {e.usage}</div>
            {e.kind==="image" && <label onClick={event=>event.stopPropagation()}><input type="checkbox" aria-label={`附上 ${e.name} v${e.version_no}`}
              checked={attached.includes(e.asset_version_id)} disabled={!attached.includes(e.asset_version_id) && attached.length>=6}
              onChange={ev=>setAttached(prev=>ev.target.checked ? [...prev,e.asset_version_id] : prev.filter(v=>v!==e.asset_version_id))}/> 对话附图</label>}
          </div>))}
        {lib.data && entries.length === 0 && <div className="muted">库中还没有条目。</div>}
      </div>
      {attached.length>0 && <div className="row"><a className="btn primary" href={`#/p/${id}/free?${selectedChat}`}>带 {attached.length} 张图去对话</a>
        <button className="btn" onClick={()=>setAttached([])}>清空选择</button></div>}
      {uploading && <LibraryUpload projectId={id} previous={uploading.previous} close={()=>setUploading(null)} done={entry=>{
        setUploading(null);setDetailId(entry);setCategory("");setStatus("");setQ("");void lib.reload();
      }}/>}
      {lib.error && <p className="error">{lib.error}</p>}
      {detail && (
        <div className="card cyan" style={{ marginTop: 16 }}>
          <b>{detail.name} · ID {detail.asset_version_id.slice(0, 8)}@v{detail.version_no}</b>
          <div className="small" style={{ margin: "6px 0" }}>指纹 <span className="mono">{detail.sha256.slice(0, 16)}</span> · 来源集 {detail.source_episode_id?.slice(0, 6) ?? "—"} · 限制：{detail.limits || "无"} · 实际观察：{detail.observation || "—"}</div>
          <div className="small">被使用：{detail.usedBy.length ? detail.usedBy.map((u: any) => u.episode_id.slice(0, 6)).join("、") : "暂无"}</div>
          <div className="row" style={{ marginTop: 8 }}>
            {detail.kind==="image" && <button className="btn" onClick={()=>setUploading({previous:detail})}>添加新版本</button>}
            {detail.status === "pending" && <><button className="btn" onClick={() => act.run(async () => { await cmd("review_library", { entry_id: detail.id, status: "approved" }); lib.reload(); })}>核准可复用</button>
              <button className="btn" onClick={()=>setLimits(detail.limits || "")}>限条件</button>
              {limits!==null && <Modal onClose={()=>setLimits(null)}><h2>限定“{detail.name}”的使用条件</h2>
                <label>限制条件<textarea autoFocus value={limits} onChange={e=>setLimits(e.target.value)}/></label>
                <button className="btn" onClick={()=>setLimits(null)}>取消</button>
                <button className="btn primary" disabled={!limits.trim() || act.busy} onClick={()=>act.run(async()=>{
                  await cmd("review_library",{entry_id:detail.id,status:"limited",limits:limits.trim()});
                  setLimits(null);await lib.reload();
                })}>保存限制条件</button><ErrorLine error={act.error}/></Modal>}</>}
            {["approved", "limited"].includes(detail.status) && <>
              <select aria-label="资产目标集" value={target} onChange={(e) => setTarget(e.target.value)}><option value="">选择目标集</option>{(p?.episodes ?? []).map((e: any) => <option key={e.id} value={e.id}>{e.display_code}</option>)}</select>
              <button className="btn primary" disabled={!target} onClick={() => act.run(async () => { await cmd("copy_to_episode", { entry_id: detail.id, episode_id: target }); lib.reload(); })}>复制到本集</button>
              {detail.kind==="image" && <button className="btn" disabled={act.busy || !p?.episodes.some((ep:any)=>ep.id===target && ep.kickoff_id)} onClick={()=>act.run(async()=>{
                const local=existingBase ||
                  (await cmd("copy_to_episode",{entry_id:detail.id,episode_id:target})).versionId;
                route.go(`#/e/${target}/assets?${new URLSearchParams({editImage:local,libraryEntry:detail.id})}`);
              })}>{existingBase ? "用这版图改图" : "复制到目标集并改图"}</button>}
              <button className="btn" onClick={() => act.run(async () => { await cmd("review_library", { entry_id: detail.id, status: "retired" }); lib.reload(); })}>停用</button></>}
          </div>
        </div>)}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- J34 知识库
function Knowledge({ id }: { id: string }) {
  const [text, setText] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  const records=useQuery<any[]>("project_knowledge",{project_id:id});
  const [imported, setImported] = useState<any[]>([]);
  const [kind, setKind] = useState("mechanism");
  const [task, setTask] = useState("");
  const [cited, setCited] = useState<string[]>([]);
  const tasks = useQuery<any[]>("task_records", { project_id: id });
  const act = useAction();
  return (
    <div>
      <h1>知识库</h1><div className="row"><a className="btn" href="#/knowledge">浏览全局剧本知识库</a><a className="btn" href={`#/palette?project=${id}`}>选择导演色卡</a></div><div className="muted small" style={{ margin: "6px 0 16px" }}>自有资料为主（台账、机制、本机案例、导入的灵感材料）；推荐有依据，引用留记录。无平台数据也可开工。</div>
      <SeriesArchive digest={records.data?.find(r=>r.kind==="series_catalog")?.sha256}/>
      <section className={records.data?.length===0 ? "card first-use" : ""}>
        <h2>项目已导入资料</h2>
        {records.data?.length===0 && <p>这个项目还没有资料。可先导入一份 UTF-8 文本，供本项目检索和引用。</p>}
        <div className="row"><select aria-label="导入资料分类" value={kind} onChange={e=>setKind(e.target.value)}><option value="mechanism">机制</option><option value="case">本机案例</option><option value="inspiration">外部灵感</option><option value="">不指定分类</option></select>
          <label className="btn file-picker">{records.data?.length===0 ? "导入第一份资料" : "导入文件"}<input type="file" onChange={e=>{
            const file=e.currentTarget.files?.[0];e.currentTarget.value="";
            if(file) act.run(async()=>{setImported(await upload("knowledge",file,{projectId:id,kind:kind||undefined}));await records.reload();});
          }}/></label></div>
        {records.error && <p className="error">{records.error}</p>}
        {records.data?.filter(r=>r.kind!=="series_catalog").map(r=><p key={r.id}>{r.title} · {r.source} · {r.sha256.slice(0,12)}</p>)}
      </section>
      {imported.map((r, i) => <div key={i} className="small" style={{ margin: "4px 0" }}>{r.name} · <Tag tone={r.status === "readable" ? "green" : r.status === "duplicate" ? "" : r.status === "corrupt" ? "red" : "amber"}>{({ readable: "可读", duplicate: "重复", corrupt: "损坏", needs_category: "需指定分类" } as any)[r.status]}</Tag></div>)}
      <ProjectLibrary pid={id}/><HistoricalChecks pid={id}/>
      <h2 style={{ marginTop: 20 }}>查相似（台账与知识库）</h2>
      <div className="row"><input style={{ flex: 1 }} placeholder="输入前提或机制，例：三次失败后警报赶跑对手" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn primary" onClick={() => act.run(async () => setHits(await q("similar", { project_id: id, text })))}>检索</button></div>
      {hits.map((h) => <div key={h.id} className="card line" style={{ marginTop: 8 }}><div className="spread"><b>{h.episode ? `${h.episode} · ` : ""}{h.title}</b><Tag>{h.kind === "ledger" ? "台账" : "知识库"} · 相似 {h.score}</Tag></div>
        <div className="small" style={{ margin: "6px 0" }}>{h.body.slice(0, 120)}</div><div className="muted tiny">出处：{h.source} · 差异词：{h.difference.join(" ")}</div>
        {h.kind === "knowledge" && <div className="row" style={{ marginTop: 6 }}><select value={task} onChange={(e) => setTask(e.target.value)}><option value="">加入哪个任务的引用</option>
          {(tasks.data ?? []).filter((t) => !["closed", "abandoned"].includes(t.state)).map((t) => <option key={t.id} value={t.id}>{t.display_code} · {t.goal.slice(0, 16)}</option>)}</select>
          <button className="btn" disabled={!task} onClick={() => act.run(async () => { await cmd("cite", { task_id: task, knowledge_id: h.id, reason: `引用原例：${h.title}` }); setCited([...cited, h.id]); })}>加入引用</button>
          {cited.includes(h.id) && <span className="ok small">已加入，下一次运行的输入清单会带上</span>}</div>}</div>)}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- J35 选题池
function ProjectStageMethods({ id, stage }: { id: string; stage: "topic" | "group" }) {
  const [open, setOpen] = useState(false);
  const methods = useQuery<any>(open ? "project_stage_methods" : null, {project_id: id, stage});
  return <details className="card" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>{stage === "topic" ? "AI 怎样挑选题" : "连续故事制作方法与模板"}</summary>
    {methods.error && <p className="error">{methods.error}</p>}
    {methods.data && <p className="muted small">项目采用 {methods.data.ref} · {methods.data.sections.length} 个小节</p>}
    {methods.data?.sections.map((s: any) => <details key={s.name}>
      <summary>{s.name.replace(/\.md$/, "")}{s.role === "project_method" && "（本项目）"}</summary>
      <p className="muted tiny">{s.methodSource.source.path} · 第 {s.methodSource.source.lines} 行</p>
      <pre style={{whiteSpace: "pre-wrap", overflowWrap: "anywhere"}}>{s.text}</pre>
    </details>)}
  </details>;
}

function Topics({ id, pickTopic }: { id: string; pickTopic:(topic:any)=>void }) {
  const storyImport=useStoryImport();
  const { go } = useProjectRoute();
  const t=useQuery<any[]>("topics",{project_id:id});
  const manualRuntime=useManualRuntime();
  const [form,setForm]=useState({code:"",premise:"",mechanism:"",motivation:""});
  const [status,setStatus]=useState("想拍");
  const [progress,setProgress]=useState("全部");
  const [search,setSearch]=useState("");
  const [pair,setPair]=useState(["",""]);
  const act=useAction();
  const cards=(t.data??[]).map(x=>({...x,key:x.id,group:x.intent,difference:x.similar.map((h:any)=>h.title).join("、")}));
  const stage=(x:any)=>x.publication?"已登记发布":({候选:"尚未开集",开发中:"已开集",待审稿:"故事待审核",已成稿:"故事稿已采用",已制作:"图文已交付"} as Record<string,string>)[x.status]??"尚未开集";
  const list=cards.filter(x=>(status==="全部"||x.group===status)&&(progress==="全部"||stage(x)===progress)&&[x.code,x.title,x.premise,x.mechanism,x.publication].join(" ").toLowerCase().includes(search.trim().toLowerCase()));
  const change=(topic_id:string,intent:string)=>act.run(async()=>{await cmd("set_topic_intent",{topic_id,intent});await t.reload();});
  return (
    <div>
      <h1>选题池</h1><div className="muted small" style={{ margin: "6px 0 16px" }}>待整理的旧题不会进入 AI 研究。你可以将其中想继续做的题列为「想拍」。</div>
      <TopicResearch key={id} projectId={id} poolChanged={t.reload} onOriginal={async part=>{
        const overview=await q<any>("project_overview",{project_id:id});
        const codes:string[]=overview.reservedEpisodeCodes;
        const prefix=codes[0]?.replace(/\d+/g,"")||"E";
        const displayCode=prefix+String(1+Math.max(0,...codes.map((c:string)=>Number(c.replace(/\D/g,""))||0))).padStart(2,"0");
        const importId=await storyImport(id,part);
        sessionStorage.setItem("kickoff-prefill:"+id,JSON.stringify({projectId:id,displayCode,importId,goal:"按所选原文剧情改编本集故事稿。"}));
        go("#/p/"+id);
      }}/>
      <ProjectStageMethods id={id} stage="topic" />
      <label>搜索选题 <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索编号、标题或故事内容"/></label>
      {cards.length>1 && <details><summary>对比两个选题</summary><div className="grid2">
        {pair.map((key,i)=>{
          const card=cards.find(c=>c.key===key);
          return <div key={i}><select aria-label={i===0 ? "第一张选题卡" : "第二张选题卡"} value={key}
            onChange={e=>setPair(pair.map((old,j)=>j===i?e.target.value:old))}>
            <option value="">选择一张选题卡</option>{cards.filter(c=>c.key!==pair[1-i]).map(c=>
              <option key={c.key} value={c.key}>{c.code} · {c.runtime && runtimeLabel(c.runtime)+" · "}{c.group} · {c.title || c.premise}</option>)}</select>
            {card && <><TopicRuntimeTag target={card.runtime}/><p style={{whiteSpace:"pre-line"}}>{card.premise}</p><p>事情怎样一步步变化 · {card.mechanism || "尚未记录"}</p>
              <p>相似选题 · {card.difference || "尚未记录"}</p></>}</div>;
        })}</div></details>}
      <div className="row"><label>选题意向 <select value={status} onChange={e=>setStatus(e.target.value)}>{["想拍","搁置","弃用","待整理","已交付","已拍","全部"].map(v=><option key={v}>{v}</option>)}</select></label>
        <label>制作进度 <select value={progress} onChange={e=>setProgress(e.target.value)}>{["全部","尚未开集","已开集","故事待审核","故事稿已采用","图文已交付","已登记发布"].map(v=><option key={v}>{v}</option>)}</select></label></div>
      <div className="grid2">{list.map(x=><div className="card" key={x.id}><div className="spread"><b>{x.code} {x.title}</b><span>{x.group==="已交付"?stage(x):x.group+" · "+stage(x)}</span></div><TopicRuntimeTag target={x.runtime}/><p style={{whiteSpace:"pre-line"}}>{x.premise}</p>
        {x.publication&&<details><summary>查看发布记录</summary>{x.publications.map((r:any)=><p key={r.platform+r.platform_id}>{r.platform} · {r.published_at} · {r.title} {r.evidence.startsWith("https://")&&<a href={r.evidence} target="_blank" rel="noreferrer">查看发布作品</a>}</p>)}</details>}
        {x.mechanism&&<p>事情怎样一步步变化 · {x.mechanism}</p>}{x.motivation&&x.motivation!=="MILO"&&<p>主角为什么这样做 · {x.motivation}</p>}
        {x.episode_id&&<a href={"#/e/"+x.episode_id+"/story"}>打开 {x.display_code}</a>}
        <div className="row">{isKickoffTopic(x)&&<button className="btn" onClick={()=>pickTopic(x)}>用这个题开一集</button>}
        {(x.group==="已交付"||x.group==="已拍") ? <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
          if(!cards.some(c=>c.origin==="restore:"+x.id&&isKickoffTopic(c)))
            await cmd("add_topic",{project_id:id,code:"",premise:x.premise,mechanism:x.mechanism,motivation:x.motivation,origin:"restore:"+x.id});
          await t.reload();setSearch("");setProgress("全部");setStatus("想拍");
        })}>还原为可选题</button> : ["想拍","搁置","弃用"].filter(v=>v!==x.intent).map(v=><button className="btn" disabled={act.busy} key={v} onClick={()=>change(x.id,v)}>{v==="想拍"?"列为想拍":v==="搁置"?"暂时搁置":"弃用选题"}</button>)}</div></div>)}</div>
      {t.error&&<p>{t.error}</p>}{t.data&&!list.length&&<p>没有符合条件的选题。 <button className="btn" onClick={()=>{setStatus("全部");setProgress("全部");setSearch("");}}>查看全部选题</button></p>}
      <h2 style={{ marginTop: 20 }}>添加选题</h2>
      {manualRuntime.fields}
      <div className="grid2">{(["code", "premise", "mechanism", "motivation"] as const).map((k) => <input key={k} placeholder={{ code: "编号（自动生成）", premise: "故事想法", mechanism: "事情怎样一步步变化（选填）", motivation: "主角为什么这样做（选填）" }[k]} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />)}</div>
      <button className="btn primary" style={{ marginTop: 10 }} onClick={() => act.run(async () => { await cmd("add_topic", { project_id: id, ...form, runtime:manualRuntime.read() }); t.reload(); })}>保存到选题池</button>
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- J37 连续故事
function Groups({ id, p }: any) {
  const { go } = useProjectRoute();
  const [name, setName] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const groups=useQuery<any[]>("groups",{project_id:id});
  const [group, setGroup] = useState<any>(null);
  const [code, setCode] = useState("");
  const act = useAction();
  if (!p) return null;
  return (
    <div>
      <h1>连续故事（几集组成）</h1><div className="muted small" style={{ margin: "6px 0 16px" }}>按集/章浏览结尾遗留、下一集接法和共享资产限制；承接开新一集只带入指定前集事实。</div>
      <ProjectStageMethods id={id} stage="group" />
      <div className="row">{groups.data?.map(g=><button className="btn" key={g.id} onClick={()=>act.run(async()=>setGroup(await q("group_view",{group_id:g.id})))}>{g.name}</button>)}</div>
      <div className="row"><input placeholder="故事组名称，例：西部篇" value={name} onChange={(e) => setName(e.target.value)} />
        {p.episodes.map((e: any) => <button key={e.id} className={`pill ${members.includes(e.id) ? "on" : ""}`} onClick={() => setMembers(members.includes(e.id) ? members.filter((x) => x !== e.id) : [...members, e.id])}>{e.display_code}</button>)}
        <button className="btn primary" disabled={!name || !members.length} onClick={() => act.run(async () => { const r = await cmd("create_group", { project_id: id, name, members: members.map((m, i) => ({ episodeId: m, chapter: `第 ${i + 1} 章` })) }); setGroup(await q("group_view", { group_id: r.groupId }));groups.reload(); })}>建立故事组</button></div>
      {group && (
        <div className="card line" style={{ marginTop: 16 }}>
          <b>{group.name}</b>
          {group.members.map((m: any) => <div key={m.episode_id} className="spread small" style={{ margin: "6px 0" }}><span>{m.chapter} · {m.display_code} · {m.state}</span><span className="muted">遗留：{JSON.parse(m.leftover_facts).join("；") || "—"}</span></div>)}
          <div className="row" style={{ marginTop: 8 }}><input placeholder="新集号" value={code} onChange={(e) => setCode(e.target.value)} />
            <button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("continue_in_group", { group_id: group.group_id ?? group.id, display_code: code }); sessionStorage.setItem("kickoff-prefill:"+id, JSON.stringify({ projectId:id, episodeId: r.episodeId, predecessorId: r.predecessor })); go(`#/p/${id}`); })}>承接开新一集</button></div>
        </div>)}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- J51 / J56 收尾
function ClosureStart({ id, did }: { id: string; did: string }) {
  const { go } = useProjectRoute();
  const act = useAction();
  useEffect(() => { act.run(async () => { const r = await cmd("start_closure", { delivery_id: did }); go(`#/p/${id}/closure/${r.closureId}`); }); }, [did]);
  return <div className="muted">正在根据该交付版本整理提议…<ErrorLine error={act.error} /></div>;
}

function Closure({ id, cid }: { id: string; cid: string }) {
  const { go } = useProjectRoute();
  const c = useQuery<any>("closure_view", { closure_id: cid });
  const [dec, setDec] = useState<Record<string, any>>({});
  const [result, setResult] = useState<any>(null);
  const act = useAction();
  useEffect(() => { if (c.data) setDec(Object.fromEntries(c.data.items.map((i: any) => [i.id, { decision: i.decision, strength: i.strength ?? i.suggested_strength, reason: i.reason ?? "", content: i.content, assetVersionId: i.asset_version_id }]))); }, [c.data?.id,c.data?.state]);
  usePoll(c.data?.state === "proposing", c.reload);
  const assets = useQuery<any[]>(c.data ? "episode_assets" : null, { episode_id: c.data?.episode_id });
  if (!c.data) return null;
  const decisions = () => Object.entries(dec).map(([itemId, d]: any) => ({ itemId, ...d }));
  const counts = { write: decisions().filter((d) => d.decision === "write_setting").length, ledger: decisions().filter((d) => d.decision === "ledger_only").length, drop: decisions().filter((d) => d.decision === "drop").length };
  if (result || c.data.state === "confirmed") {
    const raw = result ?? c.data.result ?? {};
    const r = typeof raw === "string" ? JSON.parse(raw) : raw;
    return <div><h1>本集收尾已确认</h1><div className="card" style={{ marginTop: 14 }}>写进设定 {r.settings?.length ?? 0} 条 · 只留台账 {r.ledger?.length ?? 0} 条 · 复用申请 {r.reuse?.length ?? 0} 项（待审核）</div>
      <div className="row" style={{ marginTop: 12 }}><a className="btn primary" href={`#/p/${id}/settings`}>查看设定（刚写入）</a><a className="btn" href={`#/p/${id}`}>返回项目</a></div></div>;
  }
  return (
    <div>
      <h1>收尾：设定提议</h1><div className="muted small" style={{ margin: "6px 0 16px" }}>写进设定或只留台账；保存为项目设定时选择是否每集都要遵守。稍后处理会保留现有选择与待办。</div>
      {c.data.state === "proposing" && <div className="muted">助手正在根据交付版本提出条目…</div>}
      {c.data.items.map((it: any) => { const d = dec[it.id] ?? {};  return (
        <div key={it.id} className="card line" style={{ marginBottom: 10 }}>
          <div className="spread"><div className="row"><span className="muted">{it.ordinal}</span><Tag>{CATS[it.category] ?? it.category}</Tag><input style={{ width: 420 }} value={d.content ?? ""} onChange={(e) => setDec({ ...dec, [it.id]: { ...d, content: e.target.value } })} /></div>
            <Pills value={d.decision ?? ""} onChange={(v: string) => setDec({ ...dec, [it.id]: { ...d, decision: v } })} options={[["write_setting", "写进设定"], ["ledger_only", "只留台账"], ["drop", "不处理"]]} /></div>
          <div className="muted tiny" style={{ margin: "6px 0" }}>出处：{it.source_ref.anchor} · {it.source_ref.evidence} · 提议依据：{it.rationale}</div>
          <div className="row small" style={{ marginBottom: 6 }}><span className="muted">同时申请复用资产</span>
            <select value={d.assetVersionId ?? ""} onChange={(e) => setDec({ ...dec, [it.id]: { ...d, assetVersionId: e.target.value || null } })}>
              <option value="">不申请</option>{(assets.data ?? []).filter((a: any) => !a.grid).map((a: any) => <option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select></div>
          {d.decision === "write_setting" && <div className="row"><Pills value={d.strength} onChange={(v: string) => setDec({ ...dec, [it.id]: { ...d, strength: v } })} options={[["required", "每集必须遵守"], ["variable", "默认遵守，可按剧情调整"]]} />
            <input style={{ flex: 1 }} placeholder="补充说明（选填）" value={d.reason} onChange={(e) => setDec({ ...dec, [it.id]: { ...d, reason: e.target.value } })} /></div>}
        </div>); })}
      <div className="card" style={{ marginTop: 10 }}>确认前：写进设定 {counts.write} · 只留台账 {counts.ledger} · 不处理 {counts.drop}</div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn" onClick={() => act.run(async () => { await cmd("decide_items", { closure_id: cid, decisions: decisions() }); go(`#/p/${id}`); })}>稍后处理</button>
        <button className="btn primary" onClick={() => act.run(async () => { await cmd("decide_items", { closure_id: cid, decisions: decisions() }); setResult(await cmd("confirm_closure", { closure_id: cid })); })}>确认</button>
      </div>
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- J62–J65, J63a, J70–J71 拉片
function RefFilm({ id, packId, p }: any) {
  const { go } = useProjectRoute();
  const pack = useQuery<any>("pack_view", { pack_id: packId });
  const [tab, setTab] = useState("shots");
  const [cur, setCur] = useState<any>(null);
  const [cutAt,setCutAt] = useState("");
  const [bookmarkReason,setBookmarkReason] = useState("");
  const [conditions,setConditions] = useState("");
  const selectShot = (shot:any) => {
    setCur({...shot}); setCutAt(String(Math.round((shot.start_ms+shot.end_ms)/2)));
  };
  const [beat, setBeat] = useState({ code: "B01", event: "", info: "" });
  const [ep, setEp] = useState("");
  const [req, setReq] = useState("");
  const [notice, setNotice] = useState("");
  const act = useAction();
  const frames = useQuery<any>("ref_frames", { pack_id: packId }, [pack.data?.shots?.length]);
  const [cutFrames, setCutFrames] = useState<any>(null);
  useEffect(() => { if (cur) q("ref_frames", { pack_id: packId, around_ms: cur.start_ms }).then(setCutFrames); }, [cur?.id]);
  const asks = useQuery<any[]>("ref_questions", { pack_id: packId });
  const [askText, setAskText] = useState("");
  const [askBy, setAskBy] = useState("codex");
  usePoll(!!asks.data?.some((a: any) => a.state === "running"), asks.reload);
  if (!pack.data) return null;
  const pk = pack.data;
  const unconfirmed = pk.shots.filter((s: any) => !s.confirmed).length;
  const epFor = pk.episode_id ?? p?.episodes?.[0]?.id;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "170px 1fr", gap: 24 }}>
      <div className="col" style={{ gap: 4 }}>
        <div className="muted small" style={{ marginBottom: 8 }}>本集创作</div>
        {[["参考片与开工", null], ["逐镜与节拍", "shots"], ["改编故事", "adapt"], ["故事独审", "story"], ["分场剧本", "script"]].map(([l, k]) => (
          k === "story" || k === "script" ? <a key={l} className="nav" style={{ padding: "8px 10px", borderRadius: 8, color: "var(--ink2)" }} href={epFor ? `#/e/${epFor}/${k}` : "#"}>{l}</a>
            : <button key={l} className={`pill ${tab === k || (k === "shots" && tab === "beats") ? "on" : ""}`} style={{ textAlign: "left" }} onClick={() => k && setTab(k)}>{l}</button>))}
      </div>
    <div>
      <div className="spread"><div><h1>{tab === "shots" ? "逐镜拉片" : tab === "beats" ? "节拍与可借鉴的设计" : "改编故事"}</h1>
        <div className="muted small">{pk.source.name} · {fmtMs(pk.source.duration_ms ?? 0)} · {pk.source.has_audio ? "有音轨" : "无音轨"} · 参考包 v{pk.version} · 区间 {fmtMs(pk.range_start_ms)}–{fmtMs(pk.range_end_ms)} · 指纹 {pk.source.sha256.slice(0, 10)}</div></div>
        <Pills value={tab} onChange={setTab} options={[["shots", "逐镜证据"], ["beats", "节拍"], ["adapt", "改编"]]} /></div>
      {tab === "shots" && <>
        <div style={{ display: "flex", height: 34, gap: 2, margin: "16px 0" }}>{pk.shots.map((s: any) => (
          <div key={s.id} onClick={() => selectShot(s)} style={{ flex: s.end_ms - s.start_ms, background: cur?.id === s.id ? "#3b82f6" : s.confirmed ? "#1f5f4a" : "#3a3b42", borderRadius: 4, display: "grid", placeItems: "center", cursor: "pointer", fontSize: "0.85rem" }}>{s.code}</div>))}</div>
        <div className="col">{pk.shots.map((s: any) => (
          <div key={s.id} className={`card ${cur?.id === s.id ? "sel" : ""}`} style={{ display: "flex", gap: 14, cursor: "pointer" }} onClick={() => selectShot(s)}>
            <img src={blob(frames.data?.[s.code]?.sha256)} style={{ width: 160, borderRadius: 6, background: "#222" }} />
            <div><b>{s.code}　{fmtMs(s.start_ms)}–{fmtMs(s.end_ms)}</b>
              <div className="small" style={{ marginTop: 6 }}>{s.narrative ?? "叙事作用待确认"} · {s.camera ?? "机位待确认"} / {s.motion ?? "运动待确认"}</div>
              <div className="row" style={{ marginTop: 6 }}>{s.unknown_fields.includes("sound") && <Tag tone="amber">无音轨 · 声音未知</Tag>}{s.confirmed ? <Tag tone="green">已确认</Tag> : <Tag>候选切点</Tag>}</div></div>
          </div>))}</div>
        <table className="table" style={{ display: "none" }}><thead><tr><th>镜号</th><th>起止</th><th>机位 / 运动</th><th>声音</th><th>叙事作用</th><th>状态</th></tr></thead>
          <tbody>{pk.shots.map((s: any) => <tr key={s.id} onClick={() => selectShot(s)} style={{ cursor: "pointer" }}><td>{s.code}</td><td>{fmtMs(s.start_ms)}–{fmtMs(s.end_ms)}</td><td>{s.camera ?? "—"} / {s.motion ?? "—"}</td><td>{s.unknown_fields.includes("sound") ? <Tag tone="amber">无音轨 · 未知</Tag> : s.sound}</td><td>{s.narrative ?? "—"}</td><td>{s.confirmed ? <Tag tone="green">已确认</Tag> : <Tag>候选切点</Tag>}</td></tr>)}</tbody></table>
        {cur && pk.state !== "saved" && (
          <div className="card cyan" style={{ marginTop: 12 }}><b>{cur.code} 候选切点复核</b><span className="muted small"> · {fmtMs(cur.start_ms)}–{fmtMs(cur.end_ms)}；改切点先列受影响的节拍</span>
            {cutFrames && cur.start_ms > 0 && <div className="row" style={{ marginTop: 8 }}>{["before", "after"].map((k) => (
              <figure key={k} style={{ margin: 0 }}><img src={blob(cutFrames[k]?.sha256)} style={{ width: 200, borderRadius: 6 }} /><figcaption className="muted tiny">{k === "before" ? "切点前" : "切点后"} {fmtMs(cutFrames[k]?.ms ?? 0)}.{String((cutFrames[k]?.ms ?? 0) % 1000).padStart(3, "0")}</figcaption></figure>))}
              <span className="muted small">相邻时点画面，用来判断是切镜还是镜内动作</span></div>}
            {cur.start_ms > 0 && <div style={{ marginTop: 10 }}>
              <div className="row">
                <input className="input" style={{ flex: 1 }} placeholder={`例如：${fmtMs(cur.start_ms)} 是切镜吗？`} value={askText} onChange={(e) => setAskText(e.target.value)} />
                <select className="input" value={askBy} onChange={(e) => setAskBy(e.target.value)}>{pickable(Object.keys(PROVIDER_LABEL),askBy).map((k) => <option key={k} value={k}>{PROVIDER_LABEL[k]}</option>)}</select>
                <button className="btn" disabled={!askText.trim()} onClick={() => act.run(async () => { await cmd("ask_cut", { pack_id: packId, at_ms: cur.start_ms, question: askText, provider: askBy }); setAskText(""); asks.reload(); })}>问模型</button></div>
              {(asks.data ?? []).filter((a: any) => a.at_ms === cur.start_ms).map((a: any) => (
                <div key={a.id} className="card" style={{ marginTop: 8 }}>
                  <div className="small"><b>问：</b>{a.question} <span className="muted tiny">· {PROVIDER_LABEL[a.provider] ?? a.provider}</span></div>
                  {a.state === "running" && <div className="muted small">模型在看切点前后两张图…</div>}
                  {a.state === "failed" && <div className="small" style={{ color: "var(--red)" }}>没有得到有效回答：{a.diagnostic}</div>}
                  {a.state === "waiting_quota" && <div className="small">额度用尽，稍后再问：{a.diagnostic}</div>}
                  {a.state === "answered" && <>
                    <div className="row" style={{ marginTop: 4 }}><Tag tone={a.judgement === "cut" ? "green" : a.judgement === "in_shot" ? "cyan" : "amber"}>{({ cut: "判断：切镜", in_shot: "判断：镜内动作", uncertain: "无法确定" } as any)[a.judgement]}</Tag><span className="small">{a.reason}</span></div>
                    <div className="row" style={{ marginTop: 6, alignItems: "flex-start" }}>{["before", "after"].map((k) => <img key={k} src={blob(a.frames?.[k]?.sha256)} style={{ width: 120, borderRadius: 4 }} />)}
                      <ul className="small" style={{ margin: 0 }}>{(a.evidence ?? []).map((e: any, i: number) => <li key={i}>{fmtMs(e.atMs)}.{String(e.atMs % 1000).padStart(3, "0")} {e.observation}</li>)}</ul></div>
                    <div className="muted tiny">模型解释只作参考，切点仍由你拆、并或确认。</div></>}
                </div>))}</div>}
            <div className="grid3">{[["camera","机位与景别"],["motion","摄影机运动"],["sound","声音"],["narrative","叙事作用"]].map(([key,label])=><label key={key}>{label}<input value={cur[key] ?? ""} placeholder="待判断" disabled={key==="sound" && !pk.source.has_audio} onChange={e=>setCur({...cur,[key]:e.target.value})}/></label>)}</div>
            <div className="row" style={{ marginTop: 8 }}>
              <label>切点（毫秒）<input type="number" min={cur.start_ms+1} max={cur.end_ms-1} value={cutAt} onChange={e=>setCutAt(e.target.value)}/></label>
              <button className="btn" disabled={Number(cutAt)<=cur.start_ms||Number(cutAt)>=cur.end_ms} onClick={() => act.run(async () => { const r=await cmd("edit_cuts",{pack_id:packId,op:"split",code:cur.code,at_ms:Number(cutAt)});setNotice(r.affectedBeats.length?"请复核节拍 "+r.affectedBeats.join("、"):"切点已保存");setCur(null);pack.reload(); })}>按切点拆镜</button>
              <button className="btn" onClick={() => act.run(async () => { const r=await cmd("edit_cuts", { pack_id: packId, op: "merge", code: cur.code });setNotice(r.affectedBeats.length?"请复核节拍 "+r.affectedBeats.join("、"):"已合并");setCur(null); pack.reload(); })}>与下一镜合并</button>
              <button className="btn primary" onClick={() => act.run(async () => { await cmd("edit_cuts", { pack_id: packId, op: "confirm", code: cur.code, fields: { camera:cur.camera, motion:cur.motion, sound:cur.sound,narrative:cur.narrative } });setNotice(cur.code+" 已确认");pack.reload(); })}>保留并确认</button></div></div>)}
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn primary" disabled={unconfirmed > 0 || pk.state === "saved"} onClick={() => act.run(async () => { await cmd("save_pack", { pack_id: packId }); pack.reload(); })}>保存参考包并退出</button>
          {unconfirmed > 0 && <span className="muted small">还有 {unconfirmed} 镜未确认</span>}{pk.state === "saved" && <Tag tone="green">已保存</Tag>}
          <label className="btn">重定位原片（同名换源）<input type="file" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && act.run(async () => {
            const src = await upload("reference", e.target.files![0], { projectId: id });
            const np = await cmd("create_pack", { source_id: src.sourceId, episode_id: pk.episode_id, lineage: pk.lineage, request: pk.request });
            setNotice(`${src.note ?? "已登记新来源"}：另建参考包 v${np.version}，旧版本与引用保留`); go(`#/p/${id}/ref/${np.packId}`); })} /></label></div>
          {notice && <div className="card amber small" style={{ marginTop: 8 }}>{notice}</div>}
      </>}
      {tab === "beats" && <>
        {pk.beats.map((b: any) => <div key={b.id} className="card line" style={{ margin: "8px 0" }}><b>{b.code}</b> · {b.shot_codes.join("、")} · {b.event}<div className="muted small">观众新知道：{b.info_gain}</div></div>)}
        <div className="grid3" style={{ marginTop: 10 }}><input value={beat.code} onChange={(e) => setBeat({ ...beat, code: e.target.value })} /><input placeholder="事件" value={beat.event} onChange={(e) => setBeat({ ...beat, event: e.target.value })} /><input placeholder="观众新知道" value={beat.info} onChange={(e) => setBeat({ ...beat, info: e.target.value })} /></div>
        <button className="btn" style={{ marginTop: 8 }} disabled={!cur} onClick={() => act.run(async () => { await cmd("add_beat", { pack_id: packId, code: beat.code, shot_codes: [cur.code], event: beat.event, info_gain: beat.info }); pack.reload(); })}>用选中镜头 {cur?.code ?? ""} 建节拍</button>
        <h2 style={{ marginTop: 18 }}>收藏的拍法</h2>
        {pk.bookmarks.map((b: any) => <div key={b.id} className="small">★ {pk.shots.find((s: any) => s.id === b.ref_shot_id)?.code} · {b.reason} · 条件：{b.conditions || "—"}</div>)}
        <div className="row" style={{ marginTop: 8 }}><select value={ep} onChange={(e) => setEp(e.target.value)}><option value="">收藏到哪一集</option>{(p?.episodes ?? []).map((e: any) => <option key={e.id} value={e.id}>{e.display_code}</option>)}</select>
          <input placeholder="收藏这个拍法的理由" value={bookmarkReason} onChange={e=>setBookmarkReason(e.target.value)}/><input placeholder="复用条件（选填）" value={conditions} onChange={e=>setConditions(e.target.value)}/><button className="btn" disabled={!cur || !ep || !bookmarkReason.trim()} onClick={() => act.run(async () => { await cmd("bookmark", { episode_id: ep, pack_id: packId, shot_code: cur.code, reason:bookmarkReason, conditions }); pack.reload(); })}>收藏 {cur?.code ?? "选中镜头"} 的拍法</button></div>
      </>}
      {tab === "adapt" && <>
        <p className="muted small">从节拍写故事：助手写出本集人物的动机、阻碍、行动及后果，并列原事件、新事件和取舍理由。独审读取固定版本的参考包。</p>
        <div className="row"><select aria-label="改编目标集" value={ep} onChange={(e) => setEp(e.target.value)}><option value="">写到哪一集</option>{(p?.episodes ?? []).filter((e: any) => e.kickoff_id).map((e: any) => <option key={e.id} value={e.id}>{e.display_code}</option>)}</select>
          <input style={{ flex: 1 }} placeholder="例：在 B03 保留揭示顺序，把徽记换成船票" value={req} onChange={(e) => setReq(e.target.value)} />
          <button className="btn primary" disabled={!ep || pk.state !== "saved"} onClick={() => act.run(async () => { const result = await cmd("adapt_from_pack", { episode_id: ep, pack_id: packId, request: req }); go(`#/e/${ep}/story?run=${result.runId}`); })}>写改编故事</button></div>
        {pk.state !== "saved" && <div className="muted small">先保存参考包。</div>}
      </>}
      <ErrorLine error={act.error} />
    </div>
    </div>
  );
}

// ---------------------------------------------------------------- system pages
export function SystemPage({ sub }: { sub: string }) {
  if (sub === "tasks") return <div className="page"><Tasks/></div>;
  const groups:[string,[string,string][]][] = [
    ["执行与生成", [["executors","执行器连接与型号"],["providers","我的模型服务"],["images","图像通道"],["packager","H3 打包器"]]],
    ["制作资料", [["knowledge","剧本知识库与索引"],["palette","色卡资料位置"],["skills","方法包版本"]]],
    ["本机工作台", [["storage","服务、数据与备份"],["display","界面与快捷键"],["about","关于导演工作台"]]],
  ];
  const panels:Record<string,React.ReactNode> = {executors:<Executors/>,providers:<Providers/>,images:<ImageConnections/>,
    packager:<PackagerStatus/>,knowledge:<ResourceBrowser kind="knowledge" settingsOnly/>,palette:<ResourceBrowser kind="palette" settingsOnly/>,
    skills:<Skills/>,storage:<Storage/>,display:<DisplaySettings/>,about:<About/>};
  const current=Object.prototype.hasOwnProperty.call(panels,sub)?sub:"executors";
  return <div className="page settings-page"><h1>设置</h1><div className="settings-layout">
    <nav className="settings-menu" aria-label="设置分类">{groups.map(([group,items])=><div key={group}>
      <h2>{group}</h2>{items.map(([key,label])=><a key={key} href={"#/sys/settings/"+key}
        aria-current={current===key?"page":undefined}>{label}</a>)}</div>)}</nav>
    <section className="settings-panel" key={current} aria-label="设置内容">{panels[current]}</section>
  </div></div>;
}

declare const __ADW_BUILD__: {version:string; builtAt:string; commit:string|null; modified:boolean|null};

function About() {
  const {version,builtAt,commit,modified}=__ADW_BUILD__;
  return <section><h1>关于导演工作台</h1><dl className="shortcuts">
    <dt>工作台版本</dt><dd>{version}</dd>
    <dt>本页构建时间</dt><dd><time dateTime={builtAt}>{new Date(builtAt).toLocaleString("zh-CN",{hour12:false})}</time></dd>
    <dt>源码提交</dt><dd>{commit ?? "未取得提交号"}{modified ? "（含本地前端改动）" : ""}</dd>
  </dl></section>;
}

function PackagerStatus() {
  const status = useQuery<{path:string;sha256:string|null;state:string;reason?:string;lastPackage:{created_at:number;id:string}|null}>("packager_status");
  const tool=status.data;
  return <section><h1>H3 打包器</h1><button className="btn" onClick={status.reload}>重新检查打包器</button>
    {status.error && <p className="error">{status.error}</p>}{tool && <dl className="shortcuts">
      <dt>本机路径</dt><dd className="mono">{tool.path}</dd>
      <dt>文件检查</dt><dd>{tool.state==="ready"?"可读取，三个打包入口齐全":tool.reason}</dd>
      {tool.sha256 && <><dt>文件指纹</dt><dd className="mono">{tool.sha256}</dd></>}
      <dt>此版本最近成功出包</dt><dd>{tool.lastPackage ? new Date(tool.lastPackage.created_at*1000).toLocaleString()+" · "+tool.lastPackage.id : "暂无可追溯的出包记录"}</dd>
    </dl>}</section>;
}

function ImageConnections() {
  const providers = useQuery<any>("image_providers");
  const defaults=useQuery<any>("system_image_channel");
  const act=useAction();
  return <section><h1>图像通道</h1><button className="btn" onClick={providers.reload}>刷新通道状态</button>
    {defaults.data && providers.data && Object.keys(providers.data).length>0 && <section className="card"><label>系统默认生图通道 <select value={defaults.data.provider ?? ""} disabled={act.busy} onChange={e=>{const provider=e.target.value;act.run(async()=>{await cmd("save_system_image_channel",{provider});await defaults.reload();});}}>
      {Object.entries(providers.data).map(([key,item]:any)=><option key={key} value={key}>{item.capabilities.label || key}</option>)}</select></label>
      <p className="muted small">新集的手动和专家出图都使用此通道。07 工作单可以单独指定本集通道。</p></section>}
    <ErrorLine error={act.error}/>{defaults.error && <p className="error">{defaults.error}</p>}
    {providers.error && <p className="error">{providers.error}</p>}{providers.data && (Object.keys(providers.data).length
      ? <table className="table"><thead><tr><th>通道</th><th>声明支持的操作</th><th>尺寸</th><th>验证状态</th></tr></thead>
        <tbody>{Object.entries(providers.data).map(([key,item]:any)=><tr key={key}><td>{item.capabilities.label || key}</td>
          <td>{item.capabilities.operations.map((op:string)=>({generate:"生成",edit:"编辑"}[op] || op)).join(" / ")}</td>
          <td>{item.capabilities.sizes.join(" / ")}</td><td>{({simulated:"隔离测试通道",pending_verification:"待实际调用验证"}[item.verified] || item.verified)}</td></tr>)}</tbody></table>
      : <p>当前服务未配置图像通道。</p>)}
    {Object.entries(providers.data || {}).filter(([,item]:any)=>item.settings).map(([key,item]:any)=>
      <ImageAPISettings key={key} provider={key} settings={item.settings} reload={providers.reload}/>)}
    </section>;
}

function Executors() {
  const probe = useQuery<any>("probe");
  const cat = useQuery<any>("model_catalog");
  const [paths, setPaths] = useState<Record<string,string>>({});
  const act = useAction();

  return (
    <div>
      <h1>执行器连接与型号</h1><p className="muted">工作台沿用本机 CLI 的登录账号。点“验证连接”可检查登录状态，Google CLI 通过读取可用型号验证。验证不会发起生成任务。</p>
      <button className="btn" disabled={act.busy} onClick={() => act.run(async () => {await Promise.all([probe.reload(), cat.reload()]);})}>重新检测本机程序</button>{!probe.data&&!probe.error&&<p role="status">正在检测本机 CLI…</p>}<ErrorLine error={probe.error||cat.error?{message:probe.error||cat.error||""}:null}/>
      <div className="grid3">{Object.entries(probe.data ?? {}).filter(([k]) => pickable([k]).length).map(([k, v]: any) => (
        <div key={k} className={`card ${v.version ? "line" : "amber"}`}>
          <div className="spread"><b>{PROVIDER_LABEL[k] ?? k}</b><Tag tone={v.version?"green":"amber"}>{v.status==="simulated"?"模拟执行器":v.version?"已检测到程序":"程序不可用"}</Tag></div>
          <div className="small" style={{ margin: "6px 0" }}>{v.version ?? "未检测到版本"} · 代理 {v.proxy ? "已传入" : "无"}</div>
          <div className="muted tiny mono">{v.executable ?? "未找到"}</div>
          <div className="small">独审隔离　{v.isolation === "session" ? "支持独立会话" : "待验证"}</div>
          {v.missing_flags?.length > 0 && <div className="small">缺少参数　{v.missing_flags.join("、")}</div>}
          {v.checkedAt && <div className="muted tiny">检测于 {new Date(v.checkedAt * 1000).toLocaleTimeString()}</div>}
          {v.status !== "simulated" && <label className="col small">可执行文件路径<input value={paths[k] ?? v.configured ?? ""}
            placeholder="留空时自动探测" onChange={e => setPaths({...paths, [k]:e.target.value})}/>
            <button className="btn" disabled={act.busy} onClick={() => act.run(async () => {
              await setCliPath(k, paths[k] ?? v.configured ?? ""); await probe.reload();
            })}>保存路径</button></label>}
          {cat.data?.text[k]?.connection && <ExecutorConnection key={[v.executable,v.configured,v.checkedAt,cat.data.text[k].connection.revision].join(":")} provider={k} probe={v} pathDirty={(paths[k] ?? v.configured ?? "")!==(v.configured ?? "")} settings={cat.data.text[k].connection}
            models={cat.data.text[k].models} reload={cat.reload}/>}
        </div>))}</div>
      <ExecutorDefaults/>
      <h2 style={{ marginTop: 20 }}>型号与可选推理强度</h2>
      <p className="muted small">“验证连接”检查本机 CLI 的连接情况。“刷新本机型号”读取型号清单，两项操作都不会发送生成请求。</p>
      <table className="table"><thead><tr><th>执行器</th><th>型号</th><th>可选强度</th><th>型号来源</th><th>已有生成实测记录</th></tr></thead>
        <tbody>{Object.entries(cat.data?.text ?? {}).filter(([k]) => pickable([k]).length).flatMap(([prov, c]: any) => c.models.map((m: any) => (
          <tr key={prov + m.id}><td>{PROVIDER_LABEL[prov]}</td><td>{m.label}</td><td>{m.efforts.map((e: any) => e ?? "默认").join(" / ")}</td><td className="small">{m.source}</td><td className="muted small">{m.status === "verified" ? "有实测记录" : m.status === "partially_verified" ? "部分强度有记录" : "清单未提供"}</td></tr>)))}</tbody></table>

      <ErrorLine error={act.error} />
    </div>
  );
}

function Skills() {
  const [ref, setRef] = useState("short-film@v7");
  const [pid, setPid] = useState("");
  const projects = useQuery<any[]>("list_projects", {});
  const selected = useQuery<any>(pid ? "project_view" : null, { project_id: pid });
  const versions = useQuery<string[]>("pack_versions");
  useEffect(()=>{
    if (versions.data?.length && !versions.data.includes(ref)) setRef(versions.data[versions.data.length-1]);
  },[versions.data,ref]);
  const reg = useQuery<any>("regress_pack", { ref });
  const act = useAction();
  return (
    <div>
      <h1>技能包与版本</h1><div className="muted small" style={{ margin: "6px 0 16px" }}>项目在单集开工时锁定方法版本。可按项目查看通用小节和项目专属小节的差异。</div>
      <div className="row"><label>方法包 <select aria-label="方法包" value={ref} onChange={e => setRef(e.target.value)}>{(versions.data ?? [ref]).map((v: string) => <option key={v}>{v}</option>)}</select></label>
        <select aria-label="应用方法包的项目" value={pid} onChange={e => setPid(e.target.value)}><option value="">选择项目</option>{projects.data?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        {selected.data && <><span>当前 {selected.data.method_pack}</span><button className="btn" disabled={!reg.data?.ok || selected.data.method_pack === ref || act.busy}
          onClick={() => act.run(async () => { try {await cmd("adopt_pack", { project_id: pid, ref, expected_ref:selected.data.method_pack });} finally {await selected.reload();} })}>应用于新开工的集</button></>}
      </div><ErrorLine error={act.error} />
      {pid && <UploadedMethods key={pid} pid={pid}/>}
      {reg.error && <p className="error">{reg.error}</p>}
      {reg.data && <MethodChanges key={ref+":"+pid} candidate={ref} versions={reg.data.versions} pid={pid} current={selected.data?.method_pack}/>}
      {reg.data && <details className="card line"><summary>方法文件与隔离样例检查</summary><div className="spread"><b>{reg.data.ref}</b><Tag tone={reg.data.ok ? "green" : "red"}>隔离样例回归 {reg.data.ok ? "通过" : "未通过"}</Tag></div>
        {Object.entries(reg.data.files).map(([k, f]: any) => <div key={k} className="small" style={{ margin: "4px 0" }}>{f.path} <span className="mono muted">{f.sha256.slice(0, 16)}</span></div>)}
        {reg.data.results.map((r: any) => <div key={r.sample} className="muted small">样例 {r.sample}：{r.inputs} 项输入，泄漏 {r.leaked.length} 项（不计入质量统计）</div>)}</details>}
    </div>
  );
}

function Tasks() {
  const [stage, setStage] = useState("");
  const t = useQuery<any[]>("task_records", { stage: stage || null });
  const [open, setOpen] = useState<string | null>(null);
  usePoll(!!t.data?.some(task=>task.runs.some((run: {status:string})=>["running","queued"].includes(run.status))),t.reload);
  const labels:Record<string,string> = {story:"故事稿",script:"剧本",plan:"制作方案",prompts:"提示词",publish:"发布包",queued:"排队中",
    running:"运行中",succeeded:"已完成",failed:"失败",waiting_quota:"等待额度",stopped:"已停止",awaiting_decision:"等待确认",active:"进行中",
    clarifying:"等待澄清",abandoned:"已放弃",create:"起草",revise:"修订",review:"独审",cold_read:"冷读",comparison:"对照审查",proposal:"收尾提议",
    parallel:"并排起草",new:"新会话",ephemeral:"临时会话"};
  Object.assign(labels,Object.fromEntries(FILM_STAGES));
  const label = (value:string)=>labels[value] ?? value;
  return (
    <div>
      <h1>任务记录与详情</h1><div className="muted small" style={{ margin: "6px 0 16px" }}>后台视图：输入快照、运行事件、执行器、型号、技能版本、产物和自动返修记录。</div>
      <div className="row">{["", ...FILM_STAGES.map(([key])=>key), "publish"].map((s) => <button key={s} className={`pill ${stage === s ? "on" : ""}`} onClick={() => setStage(s)}>{label(s) || "全部"}</button>)}</div>
      {t.error && <p className="error">{t.error}</p>}<table className="table" style={{ marginTop: 12 }}><thead><tr><th>集</th><th>阶段</th><th>任务</th><th>状态</th><th>自动返修</th><th>运行次数</th></tr></thead>
        <tbody>{(t.data ?? []).map((x) => <Fragment key={x.id}>
          <tr key={x.id} onClick={() => setOpen(open === x.id ? null : x.id)} style={{ cursor: "pointer" }}><td>{x.display_code}</td><td>{label(x.stage)}</td><td>{x.goal.slice(0, 30)}</td><td><Tag>{label(x.state)}</Tag></td><td>{x.auto_revise ? `已用 ${x.used_rounds} 轮（上限 ${x.max_rounds} 轮）` : "未启用"}</td><td>{x.runs.length}</td></tr>
          {open === x.id && <tr key={x.id + "d"}><td colSpan={6}>{x.runs.map((r: any) => (
            <div key={r.id} className="small" style={{ margin: "4px 0" }}>{label(r.purpose)} · {PROVIDER_LABEL[r.provider] ?? r.provider} · 请求型号 {r.requested_model ?? "默认"} / 强度 {r.requested_effort ?? "默认"} · 报告 {r.reported_model ?? "未报告"} · {label(r.status)} · 会话 {label(r.session_mode)}{r.isolation_level ? "（会话隔离）" : ""} · 技能 {r.methodPack} · 指纹 <span className="mono">{r.input_hash.slice(0, 12)}</span>{r.events.length ? ` · ${r.events.length} 个事件` : ""}{r.reason && <p>{r.reason}</p>}{r.diagnostic && <p>{r.diagnostic}</p>}{r.manifestError ? <p className="error">{r.manifestError}</p> : <><div>本次读取的方法 · {r.methods.join("、") || "无"}</div><FrozenMethods runId={r.id}/></>}</div>))}</td></tr>}</Fragment>)}</tbody></table>
    </div>
  );
}

function Storage() {
  const s = useQuery<any>("storage_status");
  const [storageReport,setStorageReport] = useState<any>(null);
  const [out, setOut] = useState<any>(null);
  const [target, setTarget] = useState("");
  const act = useAction();
  return (
    <div>
      <h1>服务与存储 · 备份恢复</h1><div className="muted small" style={{ margin: "6px 0 16px" }}>检测、隔离、手动备份与恢复。凭证只显示配置状态。</div>
      {s.data && <div className="card line"><div>资料目录 <span className="mono">{s.data.dataDir}</span> · {s.data.writable ? <Tag tone="green">可写</Tag> : <Tag tone="red">不可写</Tag>}</div>
        <div className="small" style={{ marginTop: 6 }}>最近备份：{s.data.lastBackup ? `${new Date(s.data.lastBackup.created_at * 1000).toLocaleString()} · ${s.data.lastBackup.files} 个文件` : "无"}</div></div>}
      <div className="card" style={{ marginTop: 12 }}>
        <strong>最终结果长期保留 · 中间文件保留 10 天</strong>
        <p className="muted small">发布文案完成并采用后自动记录项目完成时间。备份最多保留两份，最新成功备份长期保留。</p>
        <p className="small">{s.data?.maintenance?.policy?.enabled ? "自动维护已启用，升级前文件继续保留。" : "当前仅扫描，不删除现有文件。"}</p>
        {s.data?.maintenance?.last && <p className="small">最近检查：{new Date(s.data.maintenance.last.at * 1000).toLocaleString()} · 待清理 {s.data.maintenance.last.files} 个文件，逻辑大小 {(s.data.maintenance.last.bytes / 1024 / 1024).toFixed(1)} MB（实际释放量可能更少）</p>}
        <label className="small"><input type="checkbox" disabled={act.busy} checked={!!s.data?.maintenance?.policy?.enabled} onChange={e => { const enabled=e.target.checked; act.run(async()=>{await cmd("set_storage_cleanup",{enabled});s.reload();}); }}/> 自动清理升级后产生的到期中间文件</label>
        <button className="btn" disabled={act.busy} onClick={() => act.run(async () => { setStorageReport(await cmd("storage_scan", {})); s.reload(); })}>检查存储</button>
        {storageReport && <details className="small"><summary>查看待清理明细 · {storageReport.files} 个文件</summary>{storageReport.candidates.slice(0,100).map((f:any)=><p key={f.path}>{f.path} · {(f.bytes/1024/1024).toFixed(1)} MB{f.historical ? " · 升级前文件，仅列出，继续保留" : ""}</p>)}{storageReport.files>100 && <p>页面显示前 100 项。</p>}</details>}
      </div>
      <div className="row" style={{ marginTop: 12 }}><button className="btn primary" onClick={() => act.run(async () => { setOut(await cmd("backup", {})); s.reload(); })}>立即备份</button></div>
      {out?.backupId && <div className="small" style={{ marginTop: 8 }}>备份 <span className="mono">{out.backupId}</span> · {out.files} 个文件</div>}
      <p className="muted small">备份包含项目、上传技能、自定义色卡和本地导出的 H3 包。外部挂载的剧本库和导演资料包保留挂载位置。</p>
      <h2 style={{ marginTop: 20 }}>恢复到单独位置</h2>
      <div className="row"><input style={{ flex: 1 }} placeholder="恢复目录（绝对路径，须不存在）" value={target} onChange={(e) => setTarget(e.target.value)} />
        <button className="btn" disabled={!out?.backupId && !s.data?.lastBackup} onClick={() => act.run(async () => setOut({ ...out, restored: await cmd("restore", { backup_id: out?.backupId ?? s.data.lastBackup.id, target_dir: target }) }))}>验证并恢复</button></div>
      {out?.restored && <div className="ok small" style={{ marginTop: 8 }}>已恢复到 {out.restored.restoredTo}（{out.restored.files} 个文件校验通过）。结果未知的付费请求不会被重发。</div>}
      {out?.restored?.notes?.map((note:string)=><p className="small" key={note}>{note}</p>)}
      <ErrorLine error={act.error} />
    </div>
  );
}

function PublishSettings({ id }: { id: string }) {
  const cur = useQuery<any>("publish_settings", { project_id: id });
  const [packCheck, setPackCheck] = useState<any>(null);
  const [f, setF] = useState<any>(null);
  const act = useAction();
  useEffect(() => { if (cur.data !== undefined) setF(cur.data ?? { title_prefix: "", body_fixed_lines: [], fixed_tags: [], max_extra_tags: 2, forbidden_chars: ["<", ">"], title_max_chars: 100 }); }, [cur.data]);
  if (!f) return null;
  return (
    <div style={{ marginTop: 28 }}>
      <h2>发布设置（本项目专属）</h2>
      <div className="muted small" style={{ marginBottom: 8 }}>项目可设置固定文案、标签和独立复制字段。设置用于之后保存的发布包版本。</div>
      <div className="grid2">
        <label className="col small">标题前缀<input value={f.title_prefix} onChange={(e) => setF({ ...f, title_prefix: e.target.value })} /></label>
        <label className="col small">固定话题标签（逗号分隔）<input value={f.fixed_tags.join(",")} onChange={(e) => setF({ ...f, fixed_tags: e.target.value.split(/[,，]/).map((x: string) => x.trim()).filter(Boolean) })} /></label>
        <label className="col small">固定正文行（每行一条）<textarea value={f.body_fixed_lines.join("\n")} onChange={(e) => setF({ ...f, body_fixed_lines: e.target.value.split("\n").filter((x: string) => x.trim()) })} /></label>
        <label className="col small">本集最多加几个标签<input type="number" value={f.max_extra_tags} onChange={(e) => setF({ ...f, max_extra_tags: +e.target.value })} /></label>
      </div>
      {cur.data?.copy_format === "instrumental_v1" && <div className="row">
        <label className="btn">核对历史发布包<input type="file" accept=".md" hidden onChange={e => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) act.run(async () => setPackCheck(await upload("publish-pack", file, {projectId:id})));
        }}/></label>
        {packCheck && <span className={packCheck.parseStatus === "parsed" ? "ok" : "error"}>{packCheck.diagnostic || "发布包格式通过"}</span>}
      </div>}
      <h3>扩展发布字段</h3>
      {(f.additional_fields ?? []).map((field: any, i: number) => {
        const change = (value: any) => setF({...f, additional_fields: f.additional_fields.map((old: any,j: number) => j===i ? {...old,...value} : old)});
        return <div className="row" key={i}><input aria-label="字段编号" value={field.key} onChange={e => change({key:e.target.value})}/>
          <input aria-label="字段名称" value={field.label} onChange={e => change({label:e.target.value})}/>
          <label><input type="checkbox" checked={!!field.required} onChange={e => change({required:e.target.checked})}/> 必填</label>
          <button className="btn" onClick={() => setF({...f, additional_fields:f.additional_fields.filter((_:any,j:number)=>j!==i)})}>移除此字段</button></div>;
      })}
      <button className="btn" onClick={() => setF({...f, additional_fields:[...(f.additional_fields ?? []),{key:"",label:"",required:false}]})}>添加发布字段</button>
      <button className="btn" style={{ marginTop: 8 }} onClick={() => act.run(async () => { await cmd("set_publish_settings", { project_id: id, settings: f }); cur.reload(); })}>保存发布设置</button>
      {cur.data && <span className="ok small" style={{ marginLeft: 8 }}>已设置</span>}
      <ErrorLine error={act.error} />
    </div>
  );
}
