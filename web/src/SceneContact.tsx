import { useState } from "react";
import { blob, cmd } from "./api";
import { ErrorLine, useAction, useQuery } from "./ui";

type ImageRef={name?:string;id:string;path:string;sha256:string;anchor?:boolean;role?:string;reference_kind?:string;reference_label?:string};
type Page={filename:string;sha256:string;sourceIndexes:number[]};
type Scene={scene_id:string;segments:string[];sources:ImageRef[];pages:Page[];role_references:ImageRef[];reference_warnings:string[]};
type Contact={rulesNote?:string;fingerprint:string;unmapped_asset_ids:string[];scenes:Scene[]};

export function OriginalZoom({name,sha}:{name:string;sha:string}) {
  const [zoom,setZoom] = useState(100);
  return <><label>缩放 <input aria-label={name+" 缩放"} type="range" min={100} max={400} step={25} value={zoom}
    onChange={e=>setZoom(Number(e.target.value))}/></label> <output>{zoom}%</output>
    <div tabIndex={0} aria-label={name+" 原图细节"} style={{overflow:"auto",maxHeight:"65vh"}}>
      <img alt={name+" 原图"} src={blob(sha)} style={{display:"block",width:zoom+"%",maxWidth:"none",height:"auto"}}/>
    </div></>;
}

function Original({item}:{item:ImageRef}){
  const name = item.reference_label || item.name || item.id;
  const [open,setOpen] = useState(false);
  return <><a href={blob(item.sha256)} target="_blank" rel="noreferrer" title={item.path+" · "+item.sha256}>
    {item.anchor?"锁定图 · ":""}{name}</a>
    <details onToggle={e=>setOpen(e.currentTarget.open)}>
      <summary aria-label={"放大细节 · "+name}>放大细节</summary>
      {open && <OriginalZoom name={name} sha={item.sha256}/>}
    </details></>;
}

export function SceneContact({pid,receipt,eid}:{pid?:string;receipt?:string;eid?:string}) {
  const [report,setReport]=useState<Contact|null>(null);
  const [sceneNumber,setScene]=useState(0),[pageNumber,setPage]=useState(0);
  const [directory,setDirectory]=useState(""),[exported,setExported]=useState("");
  const act=useAction();
  const args=eid?{episode_id:eid}:{project_id:pid,receipt};
  const scene=report?.scenes[sceneNumber], page=scene?.pages[pageNumber];
  return <section className="card">
    <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
      setExported("");if(report){setReport(null);return;}
      setReport(await cmd<Contact>(eid?"episode_contact_sheet":"historical_contact_sheet",args));setScene(0);setPage(0);
    })}>{report?"收起场景对照图":"查看场景对照图"}</button>
    <ErrorLine error={act.error}/>
    {report && scene && page && <>
      {report.rulesNote&&<p>{report.rulesNote}</p>}
      <p>视觉审核待进行。角色栏列出相关段声明出镜的角色，点击名称可打开原图。</p>
      {!!report.unmapped_asset_ids.length&&<p>未登记场景 · {report.unmapped_asset_ids.join("、")}</p>}
      <div className="row"><label>场景 <select value={sceneNumber} onChange={e=>{setScene(Number(e.target.value));setPage(0);}}>
        {report.scenes.map((s,i)=><option key={s.scene_id} value={i}>{s.scene_id}</option>)}</select></label>
        <button className="btn" disabled={!pageNumber} onClick={()=>setPage(pageNumber-1)}>上一张</button>
        <span>{pageNumber+1} / {scene.pages.length}</span>
        <button className="btn" disabled={pageNumber+1===scene.pages.length} onClick={()=>setPage(pageNumber+1)}>下一张</button></div>
      <p>相关段 · {scene.segments.join("、")}</p>
      {!scene.sources.some(item=>item.anchor)&&<p>未登记锁定图</p>}
      {scene.reference_warnings.map(w=><p key={w}>{w}</p>)}
      <a href={blob(page.sha256)} target="_blank" rel="noreferrer"><img style={{maxWidth:"100%",maxHeight:"65vh"}} alt={scene.scene_id+"场景对照图"} src={blob(page.sha256)}/></a>
      <ol>{page.sourceIndexes.map(i=><li key={scene.scene_id+"-"+i+"-"+scene.sources[i].sha256}><Original item={scene.sources[i]}/></li>)}</ol>
      {!!scene.role_references.length&&<ol>{scene.role_references.map((r,i)=><li key={r.role+"-"+r.id}>
        R{i+1} · {r.reference_kind==="identity"?"身份原图":"服装参考"} · <Original item={r}/></li>)}</ol>}
      <details><summary>导出离线对照页与原图</summary><label>新目录的绝对路径 <input value={directory} onChange={e=>{setDirectory(e.target.value);setExported("");}}/></label>
        <button className="btn" disabled={act.busy||!directory.trim()} onClick={()=>act.run(async()=>{
          const result=await cmd<{index:string}>(eid?"export_episode_contact_sheet":"export_historical_contact_sheet",{...args,directory:directory.trim(),...(eid?{expected:report.fingerprint}:{})});setExported(result.index);
        })}>导出场景对照页</button>{exported&&<p role="status">{exported}</p>}</details>
    </>}
  </section>;
}

type Options={images:Record<string,string[]>;locks:Record<string,string>;outfits:Record<string,string>};
type Catalog={scenes:string[];roles:string[];source:string;images:(ImageRef&{scene_ids:string[]})[];outfits:ImageRef[];
  locks:Record<string,{label:string;name:string;version:number}>;options:Options;fingerprint:string};

export function EpisodeSceneContact({eid}:{eid:string}) {
  const [open,setOpen]=useState(false);
  return <details className="card" onToggle={e=>setOpen(e.currentTarget.open)}><summary>本集采用图与场景对照</summary>
    {open&&<EpisodeContactForm key={eid} eid={eid}/>}</details>;
}

function EpisodeContactForm({eid}:{eid:string}){
  const query=useQuery<Catalog>("episode_contact_options",{episode_id:eid});
  const [draft,setDraft]=useState<{options:Options;base:string}|null>(null);
  const [view,setView]=useState(0);
  const act=useAction();
  const data=query.data;
  const options=draft?.options ?? data?.options;
  if(!data || !options)return query.error?<p className="error">{query.error}</p>:<p>正在读取采用稿…</p>;
  const edit=(next:Options)=>setDraft({options:next,base:draft?.base ?? data.fingerprint});
  function setSingle(key:"locks"|"outfits",id:string,value:string) {
    const updated={...options![key]};
    if(value)updated[id]=value;else delete updated[id];
    edit({...options!,[key]:updated});
  }
  return <>
    <p>{data.source} · {data.images.length} 份场景、状态或九宫格素材</p>
    {data.images.map(a=>{
      const selected=options.images[a.id] ?? a.scene_ids;
      return <fieldset key={a.id}><legend>{a.name}</legend>
        {data.scenes.map(s=><label key={s}><input type="checkbox" checked={selected.includes(s)} onChange={e=>{
          edit({...options,images:{...options.images,[a.id]:e.target.checked?[...selected,s]:selected.filter(i=>i!==s)}});
        }}/>{s} </label>)}
        {a.id in options.images && <button className="btn" onClick={()=>{const images={...options.images};delete images[a.id];edit({...options,images});}}>恢复镜头引用</button>}
      </fieldset>;
    })}
    {data.images.length>0 && <>
      {data.scenes.map(s=><label key={s} className="formrow">{s} 的锁定图 <select value={options.locks[s] ?? ""} onChange={e=>setSingle("locks",s,e.target.value)}>
        <option value="">未指定</option>{Object.entries(data.locks).map(([id,l])=><option key={id} value={id}>{l.label} · {l.name} v{l.version}</option>)}</select></label>)}
      {data.outfits.map(a=><label key={a.id} className="formrow">{a.name} 的服装参考角色 <select value={options.outfits[a.id] ?? ""} onChange={e=>setSingle("outfits",a.id,e.target.value)}>
        <option value="">未指定</option>{data.roles.map(r=><option key={r}>{r}</option>)}</select></label>)}
      {draft && <div className="row"><button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
        try{await cmd("save_contact_options",{episode_id:eid,options:draft.options,expected:draft.base});setView(v=>v+1);setDraft(null);}
        finally{await query.reload();}
      })}>保存场景对照设置</button><button className="btn" onClick={()=>setDraft(null)}>撤销未保存设置</button></div>}
      {!draft&&<SceneContact key={view} eid={eid}/>}
    </>}
    {!data.images.length && <p>请先在制作方案中绑定起幅或落幅图，或在采用的分段提示词中引用场景图。</p>}
    <ErrorLine error={act.error}/>
  </>;
}
