import { useState } from "react";
import { blob } from "./api";
import { ErrorLine, useAction, useQuery } from "./ui";

const rolesField="实际可见角色ID", propsField="关键道具状态";
export function ShotBoard({rows,save}:{rows:Record<string,string>[];save:(table:string)=>Promise<void>}) {
  const [draft,setDraft]=useState(()=>rows.map(r=>({...r})));
  const act=useAction();
  const fields=[...new Set([...Object.keys(rows[0] || {}),rolesField,propsField])];
  function edit(index:number, field:string, value:string) {
    setDraft(draft.map((row,i)=>i===index?{...row,[field]:value}:row));
  }
  async function submit() {
    const values=draft.map(r=>fields.map(k=>(r[k]||"").trim()));
    if(values.some(row=>row.some(v=>/[|\r\n]/.test(v)))) throw new Error("表格单元格不能含竖线或换行");
    const lines=[fields,fields.map(()=>"---"),...values].map(row=>"|"+row.join("|")+"|");
    await save(lines.join("\n")+"\n");
  }
  return <details><summary>逐镜出镜与关键道具</summary>
    <p>填写本镜实际可见的角色，多人用顿号分隔。道具按 ID=状态 填写，多项用分号分隔。空镜或没有关键道具时填“无”。</p>
    <table className="table"><thead><tr><th>生成段 / 实际镜号</th><th>段内区间</th><th>{rolesField}</th><th>{propsField}</th></tr></thead>
      <tbody>{draft.map((r,i)=><tr key={i}><td>{r["段ID"]} / {r["镜头S"]}</td><td>{r["段内区间"]}</td>
        {[rolesField,propsField].map(field=><td key={field}><input aria-label={r["段ID"]+" "+r["镜头S"]+" "+r["段内区间"]+" "+field} value={r[field]||""}
          onChange={e=>edit(i,field,e.target.value)}/></td>)}</tr>)}</tbody></table>
    <button className="btn" disabled={act.busy || !draft.length || draft.some(r=>!r[rolesField]?.trim() || !r[propsField]?.trim())}
      onClick={()=>act.run(submit)}>保存逐镜表为方案新版</button><ErrorLine error={act.error}/>
  </details>;
}

function pairs(text:string, label:string) {
  const result:Record<string,string>=Object.create(null);
  for(const line of text.split(/[；;\n]/).map(v=>v.trim()).filter(Boolean)) {
    const split=line.indexOf("=");
    const key=line.slice(0,split).trim(), value=line.slice(split+1).trim();
    if(split<1 || !value || Object.hasOwn(result,key)) throw new Error(label+"须按 ID=值 逐项填写，同一个 ID 只填一次");
    result[key]=value;
  }
  return result;
}
const serialize=(obj:Record<string,string>={})=>Object.entries(obj).map(([k,v])=>k+"="+v).join("\n");

function Observation({ctx,version,sid,asset,refItem,packet,save,draft}:any) {
  const reviewed=packet.manifest.segments[0].references.find((r:any)=>r.asset_id===asset.id)?.visual_review;
  const [shots,setShots]=useState<string[]>(refItem.shots || []);
  const [note,setNote]=useState(reviewed?.observed || "");
  const [props,setProps]=useState(serialize(reviewed?.prominent_props));
  const [conflicts,setConflicts]=useState((reviewed?.conflicts || []).join("\n"));
  const [combined,setCombined]=useState(!!reviewed?.combined);
  const [other,setOther]=useState(serialize(reviewed?.other_uses));
  const act=useAction();
  async function submit() {
    const result=await ctx.write("save_shot_observation",{version_id:version,segment:sid,asset_version_id:asset.id,expected_sha256:asset.sha256,
      draft,shots,observed:note,prominent_props:pairs(props,"显眼道具"),conflicts:conflicts.split("\n").map((s:string)=>s.trim()).filter(Boolean),
      combined,other_uses:combined?pairs(other,"附加用途"):{}});
    save(result.versionId);ctx.reload();
  }
  return <details className="card"><summary>{refItem.label} · {asset.name} · 看图与作用镜头</summary>
    <a href={blob(asset.sha256)} target="_blank" rel="noreferrer"><img src={blob(asset.sha256)} alt={asset.name+" 原图"} style={{maxWidth:"100%",maxHeight:230}}/></a>
    <fieldset><legend>作用镜头</legend>{Object.entries(packet.windowShots).map(([window,shot])=><label key={window}>
      <input type="checkbox" checked={shots.includes(window)} onChange={e=>setShots(e.target.checked?[...shots,window]:shots.filter(s=>s!==window))}/>
      {String(shot)}{window!==shot?"（"+window+"）":""} </label>)}</fieldset>
    <div className="col"><label>原图实际可见的人物、物体和状态<textarea aria-label="看图观察" value={note} onChange={e=>setNote(e.target.value)}/></label>
      <label>中性图上显眼的道具（道具ID=状态，纯身份图可留空）<textarea aria-label="显眼道具" value={props} onChange={e=>setProps(e.target.value)}/></label>
      <label>尚未解决的冲突（每行一项）<textarea aria-label="图片冲突" value={conflicts} onChange={e=>setConflicts(e.target.value)}/></label>
      <label><input type="checkbox" checked={combined} onChange={e=>setCombined(e.target.checked)}/>身份图同时提供道具参考</label>
      {combined&&<label>角色未出镜时的额外用途（实际镜号=道具ID）<textarea value={other} onChange={e=>setOther(e.target.value)}/></label>}
    </div>
    <button className="btn" disabled={act.busy || !note.trim() || !shots.length} onClick={()=>act.run(submit)}>保存看图记录为提示词新版</button><ErrorLine error={act.error}/>
  </details>;
}

function MediaScope({ctx, version, sid, entry, refItem, packet, save, draft}:any) {
  const [shots,setShots]=useState<string[]>(refItem.shots || []);
  const act=useAction();
  return <fieldset><legend>{refItem.label} · 作用镜头</legend>
    {Object.entries(packet.windowShots).map(([window,shot])=><label key={window}>
      <input type="checkbox" checked={shots.includes(window)} onChange={e=>setShots(e.target.checked?[...shots,window]:shots.filter(s=>s!==window))}/>
      {String(shot)}{window!==shot?"（"+window+"）":""} </label>)}
    <button className="btn" disabled={!shots.length || act.busy} onClick={()=>act.run(async()=>{
      const artifact=ctx.view.artifacts.find((a:any)=>a.stage==="prompts");
      const base=artifact.versions.find((v:any)=>v.id===version);
      const refs=entry.refs.map((ref:any)=>ref.assetVersionId===refItem.assetVersionId?{...ref,shots}:ref);
      const r=await ctx.write("save_manual",{artifact_id:artifact.id,base_version_id:version,
        content:{...base.content,segments:{...base.content.segments,[sid]:{...entry,refs,txt:draft.txt,
          sound:{...entry.sound,ambience:draft.ambience,dialogue:draft.dialogue}}}},note:"登记媒体作用镜头"});
      save(r.versionId);ctx.reload();
    })}>保存作用镜头</button><ErrorLine error={act.error}/>
  </fieldset>;
}

export function ReferenceReview({ctx,version,sid,entry,save,draft}:any) {
  const info=useQuery<any>("shot_reference_view",{version_id:version,segment:sid});
  if(info.error) return <p className="error">{info.error}</p>;
  if(!info.data?.enabled) return null;
  const {packet,suggestions}=info.data;
  return <section><h3>逐镜参考核对</h3>
    {Object.entries(suggestions).map(([role,shots])=><p key={role}>{role} 的身份图作用镜头 · {(shots as string[]).join("、")}</p>)}
    {entry.refs.map((r:any)=>{const a=ctx.assets.find((x:any)=>x.id===r.assetVersionId) || packet.manifest.assets.find((x:any)=>x.id===r.assetVersionId);
      return a&&r.kind==="image"?<Observation key={version+sid+a.id} ctx={ctx} version={version} sid={sid} asset={a} refItem={r} packet={packet} save={save} draft={draft}/>:<MediaScope key={version+sid+r.assetVersionId} ctx={ctx} version={version} sid={sid} entry={entry} refItem={r} packet={packet} save={save} draft={draft}/>;})}
  </section>;
}
