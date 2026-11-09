import { MarkedExcerpt, ScriptBodySearch, scriptTypes, type ScriptRow } from "./ScriptBodySearch";
import { ProjectLibrary } from "./ProjectLibrary";
import { useState } from "react";
import { cmd, q } from "./api";
import { ErrorLine, Modal, useAction, useQuery } from "./ui";

type Kind = "knowledge" | "palette";
type Excerpt = ScriptRow & {offset:number; length:number; text:string; metadata:string; reason?:string};
type Color = {hex:string; name_zh?:string; ratio?:number; code?:string; emotion?:string};
type Card = {id:string; name:string; english:string; category:string; summary:string; cover:string; palette:{primary:Color[]; emotional:Color[]}};
type Detail = Card & {body:string; prompts:Record<string,string>; stills:string[]; sources:string; collectionGuide?:string};
type Props = {kind:Kind; episodeId?:string; stage?:string; projectId?:string; onChoose?:(e:Excerpt)=>void; methodOnly?:boolean; initialQuery?:string; recommended?:boolean};

function Swatches({colors}:{colors:Color[]}) {
  return <div className="swatches">{colors.map((c,i)=><span key={i} title={(c.name_zh||c.emotion||c.code||"")+" "+c.hex} style={{backgroundColor:c.hex, flex:c.ratio || 1}} />)}</div>;
}
type ExcerptSource={path:string;heading:string;firstLine:number;lastLine:number};
type FrozenPalette={id:string;name:string;visual:string;sha256:string;source:string;sourceBundle?:string;image?:string;video?:string;imageSource?:ExcerptSource;videoSource?:ExcerptSource;fileHashes?:Record<string,string>};
export function PaletteTrace({cards,label}:{cards:FrozenPalette[];label:string}) {
  if(!cards?.length) return null;
  return <details className="palette-trace"><summary>{label} · {cards.map(card=>card.name).join("、")}</summary>
    {cards.map(c=><div key={c.id} className="card"><strong>{c.id} · {c.name}</strong><p className="field">{c.visual}</p>
      <small>{[c.sourceBundle,c.source].filter(Boolean).join(" / ")}</small><p className="tiny">快照 SHA-256 {c.sha256}</p>
      {(["image","video"] as const).map(kind=>{
        const source=c[kind==="image"?"imageSource":"videoSource"];
        return c[kind]&&<details key={kind}><summary>{kind==="image"?"本次图像提示词参考":"本次视频提示词参考"}</summary>
          {source&&<small>{source.path} · {source.heading} · 第 {source.firstLine}–{source.lastLine} 行</small>}<pre>{c[kind]}</pre></details>;
      })}
      {c.fileHashes&&<details><summary>核对原文件</summary>{Object.entries(c.fileHashes).map(([name,hash])=><p className="tiny" key={name}>{name} · {hash}</p>)}</details>}</div>)}</details>;
}
const still = (id:string, filename:string) => "/api/palette-image/"+encodeURIComponent(id)+"?filename="+encodeURIComponent(filename);
function PaletteSelection({projectId,rows,open}:{projectId:string;rows:Card[];open:(id:string)=>void}) {
  const saved=useQuery<{id:string;name:string;sha256:string}[]>(projectId?"project_palettes":null,{project_id:projectId});
  const [draft,setDraft]=useState<{id:string;name:string}[]|null>(null);
  const selected=draft ?? saved.data ?? [];
  const act=useAction();
  function toggle(card:Card) {
    const exists=selected.some(c=>c.id===card.id);
    setDraft(exists?selected.filter(c=>c.id!==card.id):[...selected,{id:card.id,name:card.name}]);
  }
  async function save(chosen:{id:string}[]) {
    await cmd("save_palettes",{project_id:projectId,card_ids:chosen.map(c=>c.id),expected_hashes:saved.data?.map(c=>c.sha256)});
    await saved.reload(); setDraft(null);
  }
  return <>
    {projectId && <div className="palette-controls"><b>项目色卡 · {selected.length}/3</b>
      {selected.map(c=><button className="pill" key={c.id} onClick={()=>setDraft(selected.filter(x=>x.id!==c.id))}>{c.name} ×</button>)}
      <button className="btn primary" disabled={act.busy || !saved.data || draft===null} onClick={()=>act.run(()=>save(selected))}>用于本项目</button>
      {draft===null && !!saved.data?.length && <button className="btn" disabled={act.busy} onClick={()=>act.run(()=>save(selected))}>从资料包更新所选色卡</button>}
      <button className="btn" disabled={act.busy || !saved.data} onClick={()=>act.run(()=>save([]))}>本项目不使用色卡</button></div>}
    <ErrorLine error={act.error}/>{saved.error && <p className="error">{saved.error}</p>}
    <div className="director-grid">{rows.map(card=>{
      const checked=selected.some(c=>c.id===card.id);
      return <article className="director-card" key={card.id}>
        <button className="director-open" onClick={()=>open(card.id)}>{card.cover && <img loading="lazy" alt={card.name+"的影调参考"} src={still(card.id,card.cover)}/>}
        <Swatches colors={card.palette.primary}/><strong>{card.name}</strong><small>{card.id} · {card.english}</small></button>
        <p>{card.summary}</p>{projectId && <label><input type="checkbox" checked={checked} disabled={!saved.data || (!checked && selected.length>=3)} onChange={()=>toggle(card)}/>用于光色与构图</label>}
      </article>;
    })}</div>
  </>;
}

function NewPalette({close, saved}:{close:()=>void;saved:(id:string)=>void}) {
  const [form,setForm]=useState({name:"",summary:"",image_prompt:"",video_prompt:"",source:""});
  const [colors,setColors]=useState("");
  const [file,setFile]=useState<File|null>(null);
  const act=useAction();
  async function save() {
    let screenshot="";
    if(file) {
      if(file.size>8*1024*1024) throw new Error("请选择小于 8 MB 的截图");
      screenshot = await new Promise<string>((resolve,reject)=>{const reader=new FileReader();
        reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("截图读取失败"));reader.readAsDataURL(file);});
    }
    const result=await cmd("add_palette",{...form,colors:colors.split(/[,，、\s]+/).filter(Boolean),screenshot});
    saved(result.id);
  }
  return <Modal width={650} onClose={close}><h2>添加我的色卡</h2><div className="col">
    {Object.entries({name:"色卡名称",summary:"光影、色彩与构图",image_prompt:"图像提示词",video_prompt:"视频提示词",source:"来源与许可"}).map(([key,label])=>
      <label key={key}>{label}<textarea rows={key==="name"?1:2} value={form[key as keyof typeof form]} onChange={e=>setForm({...form,[key]:e.target.value})}/></label>)}
    <label>主色值<input value={colors} placeholder="#29465B, #E9B76B" onChange={e=>setColors(e.target.value)}/></label>
    <label>影调截图<input type="file" accept="image/png,image/jpeg" onChange={e=>setFile(e.target.files?.[0] ?? null)}/></label>
    <ErrorLine error={act.error}/><div className="row"><button className="btn primary" disabled={act.busy} onClick={()=>act.run(save)}>保存色卡</button><button className="btn" onClick={close}>取消</button></div></div></Modal>;
}

export function EpisodePaletteSettings({eid,stage,close}:{eid:string;stage:string;close:()=>void}) {
  const current=useQuery<any>("episode_palettes",{episode_id:eid,stage});
  const [mode,setMode]=useState<string|null>(null),[query,setQuery]=useState("");
  const [selected,setSelected]=useState<{id:string;name:string}[]|null>(null);
  const effective=mode ?? current.data?.mode ?? "inherit";
  const picked=selected ?? current.data?.cards ?? [];
  const listing=useQuery<{rows:Card[]}>(effective==="custom"?"browse_palettes":null,{query});
  const act=useAction();
  return <Modal width={750} onClose={close}><h2>本集{stage==="assets"?"图像资产":"提示词"}色卡</h2><p>色卡用于光色与构图，角色、服装和场景沿用本集设定。</p>
    <select aria-label="本集色卡使用方式" value={effective} onChange={e=>setMode(e.target.value)}><option value="inherit">沿用项目色卡</option><option value="off">本阶段不使用</option><option value="custom">本阶段指定</option></select>
    {effective==="inherit" && <p>{(current.data?.projectCards ?? []).map((c:Card)=>c.name).join("、") || "项目尚未选择色卡"}</p>}
    {effective==="custom" && <><div className="row">{picked.map((c:Card)=><button className="pill" key={c.id} onClick={()=>setSelected(picked.filter((x:Card)=>x.id!==c.id))}>{c.name} ×</button>)}</div>
      <input aria-label="搜索本阶段色卡" placeholder="查导演、卡名或视觉关键词" value={query} onChange={e=>setQuery(e.target.value)}/>
      <div className="palette-options">{listing.data?.rows.map(c=><label key={c.id}><input type="checkbox" checked={picked.some((x:Card)=>x.id===c.id)}
        disabled={picked.length>=3&&!picked.some((x:Card)=>x.id===c.id)} onChange={e=>setSelected(e.target.checked?[...picked,c]:picked.filter((x:Card)=>x.id!==c.id))}/>{c.name}</label>)}</div></>}
    <ErrorLine error={act.error}/>{(listing.error||current.error)&&<p className="error">{listing.error||current.error}</p>}
    <div className="row"><button className="btn primary" disabled={act.busy||!current.data} onClick={()=>act.run(async()=>{await cmd("set_episode_palettes",{episode_id:eid,stage,mode:effective,card_ids:picked.map((c:Card)=>c.id)});close();})}>保存本集选择</button><button className="btn" onClick={close}>关闭</button></div></Modal>;
}

type IndexResult = {path?:string;added:number;changed:number;removed:number;documents:number;indexedDocuments:number};

function AddScript({root,categories,close,saved}:{root:string;categories:string[];close:()=>void;saved:(result:IndexResult,title:string)=>void}) {
  const [title,setTitle]=useState(""),[content,setContent]=useState(""),[category,setCategory]=useState("07-用户授权作品");
  const act=useAction();
  async function readFile(file:File) {
    if (file.size>16*1024*1024) throw new Error("请选择小于 16 MB 的 UTF-8 文本文件");
    const text=new TextDecoder("utf-8",{fatal:true}).decode(await file.arrayBuffer());
    setContent(text);
    if (!title) setTitle(file.name.replace(/\.(md|txt)$/i,""));
  }
  return <Modal width={700} onClose={close}><h2>添加剧本</h2><p className="muted">保存为 UTF-8 Markdown，并同步到当前目录的检索索引。</p>
    <div className="col"><small>{root}</small><label>读取本地文本<input type="file" accept=".md,.txt" onChange={e=>{const file=e.target.files?.[0];if(file)act.run(()=>readFile(file));}}/></label>
      <label>剧本名称<input value={title} onChange={e=>setTitle(e.target.value)}/></label>
      <label>保存分类<select value={category} onChange={e=>setCategory(e.target.value)}>{Array.from(new Set(["07-用户授权作品",...categories])).map(c=><option key={c}>{c}</option>)}</select></label>
      <label>剧本正文<textarea rows={9} value={content} onChange={e=>setContent(e.target.value)}/></label><ErrorLine error={act.error}/>
      <div className="row"><button className="btn primary" disabled={act.busy||!title.trim()||!content.trim()} onClick={()=>act.run(async()=>saved(await cmd<IndexResult>("add_script_document",{expected_path:root,title,category,content}),title.trim()))}>保存并加入索引</button>
        <button className="btn" onClick={close}>取消</button></div></div></Modal>;
}

export function ResourceBrowser({kind,episodeId,stage,projectId="",onChoose,methodOnly=false,initialQuery="",recommended=false}:Props) {
  const books=kind==="knowledge";
  const [adding,setAdding]=useState(false);
  const [indexResult,setIndexResult]=useState<IndexResult|null>(null);
  const mounts=useQuery<Record<string,string>>("resource_mounts");
  const [path,setPath]=useState<string|null>(null);
  const [search,setSearch]=useState(initialQuery),[query,setQuery]=useState(initialQuery);
  const [category,setCategory]=useState(""),[page,setPage]=useState(1);
  const [excerpt,setExcerpt]=useState<Excerpt|null>(null),[cardId,setCardId]=useState("");
  const [bodySearch,setBodySearch]=useState(false),[docKind,setDocKind]=useState("");
  const [searchRevision,setSearchRevision]=useState(0);
  const selectedKind=methodOnly?"method":docKind;
  const [reason,setReason]=useState("借鉴写法与结构");
  const act=useAction();
  const scripts=useQuery<{rows:ScriptRow[];total:number;categories:string[]}>(books?"browse_scripts":null,{query:bodySearch?"":query,category,page,kind:selectedKind,recommended:recommended&&!bodySearch,episode_id:episodeId,stage});
  const palettes=useQuery<{rows:Card[];total:number;categories:string[]}>(books?null:"browse_palettes",{query,category,page});
  const detail=useQuery<Detail>(cardId?"palette_detail":null,{card_id:cardId});
  const projects=useQuery<{id:string;name:string}[]>(!books&&!projectId?"list_projects":null);
  const [pid,setPid]=useState(projectId || new URLSearchParams(location.hash.split("?")[1]).get("project") || "");
  const listing=books?scripts:palettes;
  const pages=Math.max(1,Math.ceil((listing.data?.total ?? 0)/(books?40:24)));
  const read=(name:string,offset=0,needle="",match?:ScriptRow["match"],expectedHash?:string)=>act.run(async()=>{
    const part=await q<Excerpt>("script_excerpt",{path:name,offset,query:needle,expected_sha256:expectedHash});
    if(match && match.offset>=part.offset && match.offset+Array.from(match.text).length<=part.offset+Array.from(part.text).length) part.match=match;
    setExcerpt(part);
  });
  return <section className="resource-browser">
    <h2>{books?"剧本知识库":"导演风格与色卡"}</h2>
    <details><summary>本机资料位置</summary><div className="row"><input aria-label="资料路径" value={path ?? mounts.data?.[kind] ?? ""} onChange={e=>setPath(e.target.value)} placeholder={books?"含 knowledgeIndex.sqlite 的目录":"色卡目录或 ZIP"}/>
      <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("mount_resource",{kind,path:path ?? mounts.data?.[kind]});await mounts.reload();setPath(null);setPage(1);await listing.reload();})}>挂载并刷新</button></div></details>
    {books && !recommended && <div className="row">
      <button className="btn" disabled={act.busy||!mounts.data?.knowledge} onClick={()=>setAdding(true)}>添加剧本</button>
      <button className="btn" disabled={act.busy||!mounts.data?.knowledge} onClick={()=>act.run(async()=>{
        setIndexResult(await cmd<IndexResult>("refresh_script_index",{expected_path:mounts.data?.knowledge}));setPage(1);await scripts.reload();
      })}>{act.busy?"处理中…":"更新所选目录索引"}</button>
      {indexResult&&<span role="status">索引新增 {indexResult.added}、更新 {indexResult.changed}、移出 {indexResult.removed} · 共 {indexResult.documents} 份，正文加速 {indexResult.indexedDocuments} 份</span>}
    </div>}
    {!books && <button className="btn" onClick={()=>setAdding(true)}>添加我的色卡</button>}
    {adding && (books?<AddScript root={mounts.data?.knowledge ?? ""} categories={scripts.data?.categories ?? []} close={()=>setAdding(false)} saved={(result,title)=>{
      setAdding(false);setIndexResult(result);setCategory("");setPage(1);setQuery(title);setSearch(title);scripts.reload();
    }}/>:<NewPalette close={()=>setAdding(false)} saved={id=>{setAdding(false);palettes.reload();setCardId(id);}}/>)}
    {!books&&!projectId && <label>应用到项目 <select value={pid} onChange={e=>setPid(e.target.value)}><option value="">只浏览</option>{projects.data?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
    <form className="resource-search" onSubmit={e=>{e.preventDefault();setQuery(search.trim());setPage(1);setSearchRevision(n=>n+1);}}>
      <input aria-label="搜索资料" value={search} placeholder={books?"书名、题材或创作难点":"导演、风格或视觉关键词"} onChange={e=>setSearch(e.target.value)}/>
      {books && <><select aria-label="检索方式" value={bodySearch?"body":"index"} onChange={e=>{setBodySearch(e.target.value==="body");setPage(1);}}>
        <option value="index">{recommended?"本步推荐":"目录与已索引正文"}</option><option value="body">全部正文精确查找</option></select>
        {!methodOnly && <select aria-label="资料类型" value={docKind} onChange={e=>{setDocKind(e.target.value);setPage(1);}}>
          <option value="">全部类型</option>{Object.entries(scriptTypes).filter(([k])=>!recommended||k!=="novel").map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>}</>}
      <select aria-label="资料分类" value={category} onChange={e=>{setCategory(e.target.value);setPage(1);}}><option value="">全部分类</option>{listing.data?.categories.map(c=><option key={c}>{c}</option>)}</select><button className="btn">搜索</button>
      {recommended&&!bodySearch&&<button type="button" className="btn" onClick={()=>scripts.reload()}>刷新推荐</button>}
    </form>
    <ErrorLine error={act.error}/>{listing.error && <p className="error">{listing.error}</p>}
    {books&&bodySearch?<ScriptBodySearch key={JSON.stringify([query,category,selectedKind,searchRevision,mounts.data?.knowledge])} query={query} category={category} kind={selectedKind} recommended={recommended} open={r=>read(r.path,r.matchOffset,"",r.match,r.sha256)}/>:books?<div className="script-list">{scripts.data?.rows.map(r=><article key={r.path}><button onClick={()=>read(r.path,0,"",r.match,r.sha256)}>
      <strong>{r.title}</strong><span>{r.category} · {scriptTypes[r.kind]}</span><small>{r.path}</small></button>
      {r.match?<button className="match-evidence" onClick={()=>read(r.path,r.matchOffset,"",r.match,r.sha256)}>正文命中「{r.match.text}」 · {r.evidence}</button>:r.evidence&&<small>{r.evidence}</small>}</article>)}</div>
      :<PaletteSelection key={pid} rows={palettes.data?.rows ?? []} projectId={pid} open={setCardId}/>}
    {!bodySearch && listing.data && <div className="row"><span>{listing.data.total} 份 · 第 {page} / {pages} 页</span><button className="btn" disabled={page<=1} onClick={()=>setPage(page-1)}>上一页</button><button className="btn" disabled={page>=pages} onClick={()=>setPage(page+1)}>下一页</button></div>}
    {excerpt && <Modal width={850} onClose={()=>setExcerpt(null)}><h2>{excerpt.title}</h2><ErrorLine error={act.error}/><small>{excerpt.path} · SHA-256 {excerpt.sha256.slice(0,16)}</small>{excerpt.metadata&&<details><summary>原文元信息</summary><pre>{excerpt.metadata}</pre></details>}<MarkedExcerpt text={excerpt.text} offset={excerpt.offset} match={excerpt.match}/><div className="row">
      <button className="btn" disabled={!excerpt.offset} onClick={()=>read(excerpt.path,Math.max(0,excerpt.offset-2400),"",excerpt.match,excerpt.sha256)}>上一段</button>
      <span>{excerpt.offset+1}–{Math.min(excerpt.length,excerpt.offset+2400)} / {excerpt.length}</span><button className="btn" disabled={excerpt.offset+2400>=excerpt.length} onClick={()=>read(excerpt.path,excerpt.offset+2400,"",excerpt.match,excerpt.sha256)}>下一段</button>
      {onChoose && <><input aria-label="借鉴方式" value={reason} onChange={e=>setReason(e.target.value)}/><button className="btn primary" onClick={()=>{onChoose({...excerpt,reason});setExcerpt(null);}}>引用此段</button></>}
      <button className="btn" onClick={()=>setExcerpt(null)}>返回资料列表</button></div></Modal>}
    {cardId && <Modal width={880} onClose={()=>setCardId("")}><button className="btn" onClick={()=>setCardId("")}>返回色卡库</button>{detail.error && <p className="error">{detail.error}</p>}{detail.data && <>
      <h2>{detail.data.name} · {detail.data.english}</h2><p>{detail.data.summary}</p>
      {(["primary","emotional"] as const).filter(k=>detail.data!.palette[k].length>0).map(k=><div key={k}>
        <h3>{k==="primary"?"主色板":"情绪色板"}</h3><Swatches colors={detail.data!.palette[k]}/>
        <div className="palette-legend">{detail.data!.palette[k].map((c,i)=><span key={i}>{c.code} {c.name_zh||c.emotion} <code>{c.hex}</code>
          {c.ratio!==undefined && !detail.data!.id.startsWith("custom-")?` · ${Math.round(c.ratio*100)}%`:""}{c.emotion?" · "+c.emotion:""}</span>)}</div></div>)}
      <details open><summary>影调截图 · {detail.data.stills.length} 张</summary><div className="still-grid">{detail.data.stills.map(n=><a key={n} href={still(cardId,n)} target="_blank" rel="noreferrer"><img loading="lazy" src={still(cardId,n)} alt={n}/></a>)}</div></details>
      <details><summary>色卡主文档</summary><pre>{detail.data.body}</pre></details>{Object.entries(detail.data.prompts).map(([name,t])=><details key={name}><summary>{name}</summary><pre>{t}</pre></details>)}
      <details><summary>来源与许可</summary>{detail.data.sources?<pre>{detail.data.sources}</pre>:<p>这张色卡没有附独立的来源说明。</p>}</details>
      {detail.data.collectionGuide&&<details><summary>参考图收集指南</summary><pre>{detail.data.collectionGuide}</pre></details>}</>}</Modal>}
  </section>;
}

type StepForm = {pack:string; section?:string; fields:Partial<Record<"goal"|"format_note"|"keep",string>>};
type Step = {form?:StepForm;route:string;goal:string;keep:string;format_note:string;mode:string;references:Excerpt[];enhancements?:string[];revision?:number};
const empty:Step={route:"短片",goal:"",keep:"",format_note:"",mode:"off",references:[]};
function SavedExcerpt({record}:{record:Excerpt}) {
  return <details className="saved-script-excerpt"><summary>{record.title} · 第 {record.offset+1} 字起 · 已保存原文</summary>
    <p>{record.path}</p><p className="tiny">原文件 SHA-256 {record.sha256}</p>
    <MarkedExcerpt text={record.text} offset={record.offset} match={record.match}/></details>;
}
export function VersionSourceText({role,text}:{role:string;text:string}) {
  if (role==="citation" || role==="visual_reference") {
    try {
      const source = JSON.parse(text);
      if (role==="citation" && typeof source?.text==="string" && typeof source.path==="string" && typeof source.offset==="number") {
        return <div><SavedExcerpt record={source}/><p>借鉴方式 · {source.reason}</p></div>;
      }
      if (role==="visual_reference" && source?.name && source.sha256 && source.visual) {
        return <PaletteTrace cards={[source]} label="本次运行使用的色卡"/>;
      }
    } catch { /* Older task citations may contain plain text. */ }
  }
  return <div className="field">{text}</div>;
}
type Enhancement = {id:string; title:string; where:string; when:string; stages:string[]};

function EnhancementOptions({stage,selected,change}:{stage:string;selected:string[];change:(ids:string[])=>void}) {
  const catalog = useQuery<Enhancement[]>("enhancement_catalog");
  const [all,setAll] = useState(false);
  const methods = catalog.data ?? [];
  const visible = methods.filter(m=>all || selected.includes(m.id) || m.stages.includes(stage));
  return <fieldset><legend>增强方法 · 已选 {selected.length} 项</legend>
    <label><input type="checkbox" checked={all} onChange={e=>setAll(e.target.checked)}/>显示全部阶段的方法</label>
    {catalog.error && <p className="error">{catalog.error}</p>}
    {visible.map(m=><label key={m.id} className="row">
      <input type="checkbox" checked={selected.includes(m.id)} onChange={e=>change(e.target.checked?[...selected,m.id]:selected.filter(id=>id!==m.id))}/>
      <span>{m.id} · {m.title}<small className="muted" style={{display:"block"}}>{m.where} · {m.when}</small></span>
    </label>)}
    {catalog.data && !visible.length && <p>本阶段可以从全部方法中手动选择。</p>}
  </fieldset>;
}
export function StepRequirements({pid,eid,stage,close,toComposer}:{pid:string;eid:string;stage:string;close:()=>void;toComposer?:(text:string)=>void}) {
  const definition=useQuery<StepForm>("step_form",{episode_id:eid,stage});
  const saved=useQuery<Partial<Step>>("step_config",{episode_id:eid,stage});
  const [draft,setDraft]=useState<Step|null>(null),[browse,setBrowse]=useState(false);
  const [showHistory,setShowHistory]=useState(false);
  const history=useQuery<Step[]>(showHistory?"step_history":null,{episode_id:eid,stage});
  const act=useAction();
  const form=draft ?? {...empty,...saved.data};
  function change(update:Partial<Step>) {setDraft({...form,...update});}
  async function save(toChat:boolean) {
    const {revision,form:storedForm,...values}=form;
    const result=await cmd<Step>("save_step_config",{episode_id:eid,stage,...values,expected_revision:revision ?? 0});
    if(toChat && toComposer){
      setDraft(result);
      const goal=result.goal?.trim();
      toComposer(["按已保存的本步需求起草。",goal?.length<=180?goal:"",result.references.length?`结合所选 ${result.references.length} 段资料。`:""].filter(Boolean).join("\n"));
    }
    close();
  }
  return <Modal width={950} onClose={close}><h2>本步需求</h2><div className="col">
    {saved.data && <EnhancementOptions stage={stage} selected={form.enhancements ?? []} change={enhancements=>change({enhancements})}/>}
    {definition.data && <><small className="muted">本步字段 · {definition.data.pack} · {definition.data.section ?? "历史配置"}</small>
      {Object.entries(definition.data.fields).map(([key,label])=><label key={key}>{label}<textarea value={form[key as keyof StepForm["fields"]]}
        onChange={e=>change({[key]:e.target.value})}/></label>)}</>}
    {definition.error && <p className="error">{definition.error} <button className="btn" onClick={definition.reload}>重新读取字段</button></p>}
    {stage==="assets" ? <p className="muted small">保存后用于本集下一批图像。已建立的批次沿用各自确认的输入。</p> : <>
    <ProjectLibrary pid={pid} eid={eid} stage={stage}/>
    <label>创作路线 <select value={form.route} onChange={e=>change({route:e.target.value})}>{["短片","电影节 / 作者表达","流媒体 / 类型剧","自由自定","快速出片"].map(r=><option key={r}>{r}</option>)}</select></label>
    <label>参考与知识库 <select value={form.mode} onChange={e=>{change({mode:e.target.value});setBrowse(false);}}>
      <option value="off">不调用</option><option value="match">匹配剧本与方法书</option><option value="method">只查方法书</option></select></label>
    {form.mode!=="off" && <><button className="btn" onClick={()=>setBrowse(!browse)}>{browse?"收起资料":"选择参考片段"}</button>
      {browse && <ResourceBrowser key={form.mode} kind="knowledge" recommended episodeId={eid} stage={stage} methodOnly={form.mode==="method"} initialQuery={form.goal} onChoose={r=>{change({references:[...form.references.filter(x=>x.path!==r.path||x.offset!==r.offset),r]});setBrowse(false);}}/>}
      {form.references.map((r,i)=><div key={r.path+r.offset}><SavedExcerpt record={r}/>
        <div className="row"><label>借鉴方式 <input value={r.reason??""} onChange={e=>change({references:form.references.map((entry,j)=>j===i?{...entry,reason:e.target.value}:entry)})}/></label><button className="btn" onClick={()=>change({references:form.references.filter((_,j)=>i!==j)})}>移除</button></div></div>)}</>}
    </>}
    <ErrorLine error={act.error}/>{saved.error&&<span className="error">{saved.error}</span>}
    <div className="row"><button className="btn" disabled={!saved.data||!definition.data||act.busy} onClick={()=>act.run(()=>save(false))}>保存本步配置</button>
      {stage!=="assets" && toComposer && <button className="btn primary" disabled={!saved.data||!definition.data||act.busy} onClick={()=>act.run(()=>save(true))}>保存并带到创作对话</button>}<button className="btn" onClick={()=>setShowHistory(!showHistory)}>配置历史</button><button className="btn" onClick={close}>关闭</button></div>
    {showHistory&&<div>{history.error&&<p className="error">{history.error}</p>}{history.data?.length===0&&<p>本步还没有配置版本。</p>}
      {history.data?.map(h=><details key={h.revision}><summary>第 {h.revision} 版 · {h.goal||h.route}</summary><p>{[h.format_note,h.keep].filter(Boolean).join(" · ")}</p>
        {h.enhancements?.length ? <p>已选增强方法 · {h.enhancements.join("、")}</p> : null}
        {h.references.map(r=><div key={r.path+r.offset}><SavedExcerpt record={r}/><p>{r.reason}</p></div>)}
        {h.revision!==saved.data?.revision&&<button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
          await cmd("restore_step_revision",{episode_id:eid,stage,revision:h.revision,expected_revision:saved.data?.revision ?? 0});
          setDraft(null);await saved.reload();await history.reload();setBrowse(false);
        })}>将此配置恢复为新版</button>}</details>)}</div>}
  </div></Modal>;
}
