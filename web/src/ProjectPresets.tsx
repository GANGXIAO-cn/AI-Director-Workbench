import { useState } from "react";
import { cmd } from "./api";
import { specChoices, specLabels, frameOrientation } from "./specs";
import { ErrorLine, useAction, useQuery } from "./ui";

type Values = Record<string, string | number>;
type Preset = {name:string;rev:number;values:Values};
type Episode = {id:string;code:string;name:string|null;rev:number|null;base:Values|null;effective:Values};
const nameOf=(name:string)=>name==="default"?"项目默认":name;
const revision=(rev:number)=>rev===0?"初始版":`第 ${rev} 版`;
const format=(key:string,value:string|number)=>specChoices[key]?.[String(value)] || String(value);
const groups = [
  {title:"形态与时长",keys:["filmFormat","targetDuration","segmentMaxSeconds","aspectRatio"]},
  {title:"画面与声音",keys:["style","shootingFormat","shotRhythm","postText","sound"]},
  {title:"交付与 H3 默认",keys:["packaging","h3Resolution","h3Candidates","h3InstanceType"]},
];
type Draft = {source:Preset; values:Values; copying:boolean; name:string};

type Preview = {fingerprint:string;preview:{impacts:{target:string;reason:string}[];kept:string[]}};
function EpisodeOverride({ep,refresh}:{ep:Episode;refresh:()=>Promise<void>}) {
  const [open,setOpen]=useState(false),[key,setKey]=useState("aspectRatio"),[value,setValue]=useState("");
  const [checked,setChecked]=useState<Preview|null>(null);
  const action=useAction();
  const choose=(k:string)=>{setKey(k);setValue(String(ep.effective[k]??""));setChecked(null);action.clear();};
  if (!open) return <button className="btn" onClick={()=>{choose("aspectRatio");setOpen(true);}}>调整本集规格</button>;
  return <div className="col"><label>本集调整项 <select value={key} onChange={e=>choose(e.target.value)}>
    {groups.flatMap(g=>g.keys).map(k=><option key={k} value={k}>{specLabels[k]}</option>)}</select></label>
    <label>本集新值 {specChoices[key]?<select value={value} onChange={e=>{setValue(e.target.value);setChecked(null);}}>
      {Object.entries(specChoices[key]).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>:
      <input type={key==="segmentMaxSeconds"?"number":"text"} min={key==="segmentMaxSeconds"?5:undefined} max={key==="segmentMaxSeconds"?15:undefined}
        value={value} onChange={e=>{setValue(e.target.value);setChecked(null);}}/>}</label>
    <button className="btn" disabled={action.busy||value===String(ep.effective[key]??"")} onClick={()=>action.run(async()=>{
      setChecked(await cmd<Preview>("episode_spec_change",{episode_id:ep.id,key,value}));})}>预览本集变更</button>
    {checked&&<div className="field"><p>{format(key,ep.effective[key]??"未设置")} → {format(key,value)}</p>
      {checked.preview.impacts.map((item,i)=><p key={i}>{item.target} · {item.reason}</p>)}
      {checked.preview.kept.length>0&&<p>保留独立身份图 · {checked.preview.kept.join("、")}</p>}
      <button className="btn primary" disabled={action.busy} onClick={()=>action.run(async()=>{
        await cmd("episode_spec_change",{episode_id:ep.id,key,value,confirm:true,expected_preview:checked.fingerprint});
        await refresh();setOpen(false);})}>确认本集变更</button></div>}
    <ErrorLine error={action.error}/><button className="btn" onClick={()=>setOpen(false)}>收起调整</button></div>;
}

export function ProjectPresets({pid,showProjectLink=false}:{pid:string;showProjectLink?:boolean}) {
  const data=useQuery<{presets:Preset[];episodes:Episode[]}>("project_presets",{project_id:pid});
  const [name,setName]=useState("default");
  const [draft,setDraft]=useState<Draft|null>(null);
  const [notice,setNotice]=useState("");
  const action=useAction();
  if (!data.data) return <p>{data.error || "正在读取规格预设…"}</p>;
  const selected=data.data.presets.find(p=>p.name===name) ?? data.data.presets[0];
  const values=draft?.values ?? selected.values;
  const differences = Object.keys({...selected.values,...values}).filter(k=>String(selected.values[k]??"")!==String(values[k]??""));
  const existing=data.data.episodes.filter(e=>e.name===selected.name);
  function edit(copying:boolean) {
    setDraft({source:selected, values:{...selected.values},copying,name:""});setNotice("");action.clear();
  }
  async function save() {
    if (!draft) return;
    const result=await cmd<{name:string;rev:number}>("update_preset",{project_id:pid,name:draft.source.name,
      expected_revision:draft.source.rev,values:draft.values,copy_as:draft.copying?draft.name.trim():null});
    await data.reload(); setName(result.name);setDraft(null);
    setNotice(`已保存「${nameOf(result.name)}」${revision(result.rev)}`);
  }
  return <section className="project-presets">
    <header className="spread"><div><h1>规格预设 · {frameOrientation(String(values.aspectRatio))}</h1>
      <p className="muted small">每集开工时复制选定版本，可单独调整。</p></div>
      <div className="row"><select aria-label="查看项目预设" disabled={!!draft} value={selected.name} onChange={e=>{setName(e.target.value);setNotice("");}}>
        {data.data.presets.map(p=><option key={p.name} value={p.name}>{nameOf(p.name)} · {revision(p.rev)}</option>)}</select>
        {!draft&&<><button className="btn" onClick={()=>edit(true)}>复制为新预设</button><button className="btn" onClick={()=>edit(false)}>改这个预设</button></>}</div></header>
    {draft?.copying&&<label className="field">新预设名称 <input maxLength={80} value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})}/></label>}
    <div className="preset-layout"><div className="preset-fields">
      {groups.map(group=><section className="card" key={group.title}><h2>{group.title}</h2>
        {group.keys.filter(key=>draft || key.startsWith("h3") || values[key]!=null && values[key]!=="").map(key=><div key={key} className="preset-row">
          <span className="muted">{specLabels[key]}</span>{draft ? specChoices[key]?<select aria-label={specLabels[key]} value={values[key]??""} onChange={e=>setDraft({...draft,values:{...values,[key]:e.target.value}})}>
            {Object.entries(specChoices[key]).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>:
          <input aria-label={specLabels[key]} type={key==="segmentMaxSeconds"?"number":"text"} min={key==="segmentMaxSeconds"?5:undefined} max={key==="segmentMaxSeconds"?15:undefined} value={values[key]??""} onChange={e=>setDraft({...draft,values:{...values,[key]:e.target.value}})}/>:
          <span>{format(key,values[key]??"")}{key==="segmentMaxSeconds"?" 秒":""}</span>}</div>)}</section>)}
      </div><aside className="card"><h2>{draft?"本次改动的影响":"已开工集的有效规格"}</h2>
        {draft&&<><p>{draft.copying?"新集可选用这个副本。":"之后选用该预设的新集会读取保存后的规格。"}</p>
          {differences.map(k=><p key={k}>{specLabels[k]||k} · {format(k,selected.values[k]??"未设置")} → {format(k,values[k]??"未设置")}</p>)}
          <p>已有 {existing.length} 集登记选用「{nameOf(selected.name)}」，保留各自的开工副本及单集调整。</p></>}
        {data.data.episodes.map(ep=><details key={ep.id}><summary>{ep.code} · {ep.effective.aspectRatio} · {ep.name?nameOf(ep.name)+" "+revision(ep.rev!):"旧集，未登记预设来源"}</summary>
          {Object.entries(ep.effective).map(([k,v])=><div className="preset-row" key={k}><span>{specLabels[k]??k}</span>
            <span>{format(k,v)} {ep.base && ep.base[k]!==v && <b> · 本集覆盖</b>}</span></div>)}{!draft&&<EpisodeOverride ep={ep} refresh={data.reload}/>}</details>)}
        {!data.data.episodes.length&&<><p>本项目还没有已开工的集。开工时选择预设，之后可在这里查看该集的有效规格。</p>
          {showProjectLink && !draft && <a className="btn" href={`#/p/${pid}`}>去项目选择或新增一集</a>}</>}</aside></div>
    {draft&&<div className="row"><button className="btn primary" disabled={action.busy || (draft.copying?!draft.name.trim():!differences.length)} onClick={()=>action.run(save)}>
      {draft.copying?"保存新预设":"保存修改"}</button><button className="btn" onClick={()=>{setDraft(null);action.clear();data.reload();}}>取消</button></div>}
    <ErrorLine error={action.error}/>{notice&&<p role="status">{notice}</p>}
  </section>;
}
