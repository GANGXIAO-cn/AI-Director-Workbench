import { useState } from "react";
import { cmd } from "./api";
import { ErrorLine, useAction } from "./ui";
import { NEXT_FILM_STAGE } from "./workflow";

export function CooperationSelect({value, onChange, disabled}: {
  value: string; onChange: (mode: string) => void; disabled?: boolean;
}) {
  return <label>合作档位 <select value={value} disabled={disabled} onChange={e=>onChange(e.target.value)}>
    <option value="beginner">新手</option><option value="advanced">进阶</option><option value="expert">高手</option>
  </select></label>;
}

export function EpisodeCooperation({eid, mode, reload}: {eid:string; mode:string; reload:()=>Promise<void>}) {
  const act = useAction();
  return <div><CooperationSelect value={mode} disabled={act.busy} onChange={next=>act.run(async()=>{
    await cmd("set_cooperation_mode", {episode_id:eid, mode:next, expected_mode:mode}); await reload();
  })}/><ErrorLine error={act.error}/></div>;
}

export function OptionalStage({eid, stage, skippedReason, reload}: {
  eid:string; stage:string; skippedReason:string; reload:()=>Promise<void>;
}) {
  const [editing,setEditing] = useState(false);
  const [reason,setReason] = useState("");
  const act = useAction();
  const save = (skipped:boolean) => act.run(async()=>{
    await cmd("set_optional_stage",{episode_id:eid,stage,skipped,reason,expected_reason:skippedReason});
    await reload(); setEditing(false); setReason("");
  });
  if (skippedReason) return <section className="card"><p>本步已跳过 · {skippedReason}</p>
    <button className="btn" disabled={act.busy} onClick={()=>save(false)}>恢复这一步</button>
    {NEXT_FILM_STAGE[stage] && <a className="btn primary" href={`#/e/${eid}/${NEXT_FILM_STAGE[stage]}`}>继续下一阶段</a>}
    <ErrorLine error={act.error}/></section>;
  return <section className="card">{editing ? <form onSubmit={e=>{e.preventDefault();save(true);}}>
    <label>跳过原因 <input value={reason} onChange={e=>setReason(e.target.value)} autoFocus placeholder="可不填"/></label>
    <button className="btn" disabled={act.busy}>确认跳过</button>
    <button type="button" className="btn ghost" disabled={act.busy} onClick={()=>{setEditing(false);setReason("");act.clear();}}>取消</button>
  </form> : <button className="btn ghost" onClick={()=>setEditing(true)}>跳过这一步</button>}<ErrorLine error={act.error}/></section>;
}

type StageEdit = {id:string; revision:number;editState:"open"|"protected"|"editing"};
export async function changeStageEdit(art:StageEdit, editing:boolean) {
  return cmd("set_stage_edit",{artifact_id:art.id,expected_revision:art.revision,editing});
}

export function RevisionBar({art,reload}: {art:StageEdit;reload:()=>Promise<void>}) {
  const act=useAction();
  if (art.editState==="open") return null;
  const locked=art.editState==="protected";
  return <section className="row revision-bar" aria-label="阶段改稿状态"><span>{locked?"已采用稿受保护":"改稿中 · 新稿另存"}</span>
    <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
      await changeStageEdit(art,locked);await reload();
    })}>{locked?"修改本阶段":"结束修改"}</button><ErrorLine error={act.error}/></section>;
}
