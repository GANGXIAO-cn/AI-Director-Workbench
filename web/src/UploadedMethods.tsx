import { useState } from "react";
import { cmd, upload } from "./api";
import { ErrorLine, useAction, useQuery } from "./ui";

import { FILM_STAGES } from "./workflow";
const stages = {...Object.fromEntries(FILM_STAGES),publish:"发布文案"};
export const canUseUploadedMethods = (stage:string) => Object.prototype.hasOwnProperty.call(stages,stage);
type Edition = {id:string; number:number; filename:string; sha256:string};
type Method = {id:string; title:string; versions:Edition[]};
type Library = {configured:{stage:string;path:string;synced:boolean}[];projectSelected:string[];configuredFiles:string[];methods:Method[]; selected:string[]};

function EditionReader({project, method}:{project:string; method:Method}) {
  const [chosen,setChosen]=useState("");
  const [base,setBase]=useState<string|null>(null);
  const current=method.versions.find(v=>v.id===chosen) ?? method.versions[0];
  const previous=method.versions.find(v=>v.number===current.number-1);
  const peer=base===null?previous?.id:base;
  const result=useQuery<{text:string;difference:string|null}>("custom_method_text", {
    project_id:project,version_id:current.id,compare_to:peer===current.id?null:peer || null
  });
  return <section>
    <label>查看原文版本 <select value={current.id} onChange={e=>{setChosen(e.target.value);setBase(null);}}>
      {method.versions.map(v=><option key={v.id} value={v.id}>R{v.number}</option>)}</select></label>
    {method.versions.length>1 && <label> 对比版本 <select value={peer || ""} onChange={e=>setBase(e.target.value)}>
      <option value="">只看原文</option>{method.versions.filter(v=>v.id!==current.id).map(v=><option key={v.id} value={v.id}>R{v.number}</option>)}</select></label>}
    <p className="muted tiny">{current.filename} · SHA-256 {current.sha256}</p>
    {result.error && <p className="error">{result.error}</p>}
    {result.data && <>{result.data.difference!=null && <details open><summary>版本差异</summary><pre>{result.data.difference || "两版正文一致"}</pre></details>}
      <pre>{result.data.text}</pre></>}
  </section>;
}

export function ProjectMethodSync({pid,eid,stage}:{pid:string;eid?:string;stage?:string}) {
  const library=useQuery<Library>("custom_methods",{project_id:pid,episode_id:eid,stage});
  const act=useAction();
  const [notice,setNotice]=useState("");
  if(library.error)return <p className="error">{library.error}</p>;
  const data=library.data;
  if(!data)return null;
  if(!Array.isArray(data.configured) || !Array.isArray(data.projectSelected))
    return <p role="status">后台服务版本较旧，请重启工作台以使用项目方法同步。</p>;
  const missing=data.configured.filter(item=>!item.synced);
  const different=eid && stage && JSON.stringify(data.selected)!==JSON.stringify(data.projectSelected);
  if(!missing.length && !different && !notice)return null;
  return <section className="card">
    {!!missing.length && <><b>本项目有 {missing.length} 份创作方法可更新</b>
      {eid ? <a className="btn" href={`#/p/${pid}/settings`}>去项目设定更新方法</a> : <>
        <ul>{missing.map(item=><li key={item.stage+item.path}>{item.path} · {stages[item.stage]}</li>)}</ul>
        <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
          await cmd("sync_project_methods",{project_id:pid});await library.reload();setNotice("项目的创作方法已更新。");
        })}>更新项目的创作方法</button></>}
    </>}
    {different && <><p>{stages[stage!]} 从下一次任务起采用项目选择的 {data.projectSelected.length} 份方法。</p>
      <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
        await cmd("adopt_project_stage_methods",{project_id:pid,episode_id:eid,stage,expected:data.selected,
          project_expected:data.projectSelected});await library.reload();setNotice("本步之后发起的任务将使用项目当前的方法。");
      })}>本集采用项目当前的方法</button></>}
    {notice && <p role="status">{notice}</p>}<ErrorLine error={act.error}/>
  </section>;
}

function MethodLibrary({pid,eid,stage}:{pid:string;eid?:string;stage:string}) {
  const library=useQuery<Library>("custom_methods",{project_id:pid,stage,episode_id:eid});
  const [target,setTarget]=useState("");
  const [title,setTitle]=useState("");
  const [file,setFile]=useState<File|null>(null);
  const [fileKey,setFileKey]=useState(0);
  const [reading,setReading]=useState("");
  const [draft,setDraft]=useState<{before:string[]; selected:string[]}|null>(null);
  const [notice,setNotice]=useState("");
  const act=useAction();
  const methods=library.data?.methods ?? [];
  const selected=draft?.selected ?? library.data?.selected ?? [];
  const uploadTo=methods.find(m=>m.id===target);
  async function saveFile() {
    if(!file)return;
    const outcome=await upload<{methodId:string;number:number}>("custom-method",file,{
      projectId:pid,title,methodId:target || null,expectedRevision:uploadTo?.versions[0].id
    });
    await library.reload();
    setFile(null);setFileKey(n=>n+1);setTitle("");setTarget(outcome.methodId);setReading(outcome.methodId);
    setNotice(`已收录 R${outcome.number}，可在“采用版本”中选用。`);
  }
  function choose(method:Method,id:string) {
    const includes=(v:string)=>method.versions.some(e=>e.id===v);
    const next=selected.flatMap(v=>includes(v)?(id?[id]:[]):[v]);
    if(id && !selected.some(includes))next.push(id);
    setDraft({before:draft?.before ?? library.data?.selected ?? [],selected:next});setNotice("");
  }
  async function saveSelection() {
    if(!draft)return;
    try {
      await cmd("set_custom_methods",{project_id:pid,episode_id:eid,stage,versions:draft.selected,expected:draft.before});
      setDraft(null);setNotice(eid?"已保存，下次在本阶段起草或修订时使用。":"已保存，新开工集沿用此选择。");
    } finally {await library.reload();}
  }
  return <div className="method-changes">
    <div className="row">
      <label>上传到 <select value={target} onChange={e=>{setTarget(e.target.value);setNotice("");}}>
        <option value="">新技能</option>{methods.map(m=><option key={m.id} value={m.id}>{m.title} · 上传新版</option>)}</select></label>
      {!target && <label>技能名称 <input value={title} maxLength={100} onChange={e=>setTitle(e.target.value)}/></label>}
      <input key={fileKey} aria-label="技能 Markdown 文件" type="file" accept=".md,text/markdown" onChange={e=>setFile(e.target.files?.[0] ?? null)}/>
      <button className="btn" disabled={!library.data || !file || (!target&&!title.trim()) || act.busy} onClick={()=>act.run(saveFile)}>上传 SKILL.md</button>
    </div>
    {library.error && <p className="error">{library.error}</p>}<ErrorLine error={act.error}/>
    {methods.map(m=><article key={m.id} className="card">
      <div className="spread"><b>{m.title}</b><label>采用版本 <select aria-label={m.title+"采用版本"} value={selected.find(id=>m.versions.some(v=>v.id===id)) ?? ""}
        onChange={e=>choose(m,e.target.value)}><option value="">本阶段不使用</option>
        {m.versions.map(v=><option key={v.id} value={v.id}>R{v.number}</option>)}</select></label>
        <button className="btn" onClick={()=>setReading(reading===m.id?"":m.id)}>{reading===m.id?"收起原文":"查看原文与版本"}</button></div>
      {reading===m.id && <EditionReader project={pid} method={m}/>}
    </article>)}
    {draft && <div className="row"><button className="btn primary" disabled={act.busy} onClick={()=>act.run(saveSelection)}>保存自定义技能选择</button>
      <button className="btn" onClick={()=>{setDraft(null);setNotice("");}}>撤销未保存选择</button></div>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}

export function UploadedMethods({pid,eid,stage}:{pid:string;eid?:string;stage?:string}) {
  const [picked,setPicked]=useState("story");
  const active=stage ?? picked;
  return <section className="card">
    <h3>{eid?"本集自定义技能":"项目自定义技能"}</h3>
    <p className="small muted">{eid?"所选版本用于本集此阶段的下一次写作。":"上传 Markdown 原文，按阶段选择新开工集使用的版本。"}</p>
    {!stage && <label>使用阶段 <select value={picked} onChange={e=>setPicked(e.target.value)}>
      {Object.entries(stages).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>}
    <MethodLibrary key={[pid,eid,active].join("/")} pid={pid} eid={eid} stage={active}/>
  </section>;
}
