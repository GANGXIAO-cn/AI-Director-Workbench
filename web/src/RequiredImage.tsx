import { useState } from "react";
import { cmd } from "./api";
import { ErrorLine, Modal, useAction } from "./ui";

export function RequiredImage({ctx, asset, close}: {ctx:any; asset:any; close:()=>void}) {
  const previous = asset.requirement;
  const [reason,setReason] = useState<string>(previous?.reason || "");
  const [targets,setTargets] = useState<string[]>(previous?.targetSegments || []);
  const [fitness,setFitness] = useState<string>(previous?.fitness || "not_tested");
  const [limitations,setLimitations] = useState<string>(previous?.limitations || "");
  const plan = ctx.view.artifacts.find((a:any)=>a.stage==="plan");
  const segments = plan?.versions.find((v:any)=>v.id===plan.adopted_version_id)?.content?.segments ?? [];
  const act = useAction();
  const limited = ["limited","blocked"].includes(fitness);
  async function save(enabled:boolean) {
    await cmd("mark_image",{ episode_id:ctx.eid, version_id:asset.id, mark:"required", enabled,
      ...(enabled ? {requirement:{reason,targetSegments:targets,fitness,limitations}} : {}) });
    ctx.reload();
    close();
  }
  return <Modal width={460} onClose={close}><div className="col">
    <h2>必用图要求</h2><b>{asset.name} · v{asset.version_no}</b>
    <label className="col">选择理由<input aria-label="选择理由" placeholder="可不填" value={reason} onChange={e=>setReason(e.target.value)}/></label>
    <fieldset><legend>目标段（留空表示整集至少一段）</legend>
      {Array.from(new Set<string>([...segments.map((s:any)=>s.id),...targets])).map(sid=><label key={sid} style={{marginRight:12}}>
        <input type="checkbox" checked={targets.includes(sid)} onChange={e=>setTargets(e.target.checked?[...targets,sid]:targets.filter(s=>s!==sid))}/>{sid}</label>)}
    </fieldset>
    {previous?.shotIds.length>0 && <small>已绑定镜号 {previous.shotIds.join("、")}</small>}
    <label className="col">用途适配<select aria-label="用途适配" value={fitness} onChange={e=>setFitness(e.target.value)}>
      <option value="not_tested">尚未评估</option><option value="pass">可用</option>
      <option value="limited">用途受限</option><option value="blocked">当前用途受阻</option></select></label>
    {(limited || limitations) && <label className="col">用途限制或缺陷范围<textarea aria-label="用途限制或缺陷范围" value={limitations} onChange={e=>setLimitations(e.target.value)}/></label>}
    <div className="row"><button className="btn primary" disabled={act.busy || (limited && !limitations.trim())}
      onClick={()=>act.run(()=>save(true))}>保存必用要求</button>
      {previous && <button className="btn" disabled={act.busy} onClick={()=>act.run(()=>save(false))}>取消必用</button>}
      <button className="btn" onClick={close}>关闭</button></div><ErrorLine error={act.error}/>
  </div></Modal>;
}
