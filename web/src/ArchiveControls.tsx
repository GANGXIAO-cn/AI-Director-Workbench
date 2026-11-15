import { useState } from "react";
import { cmd } from "./api";
import { ErrorLine, useAction, useQuery } from "./ui";

export function ArchiveControls({projectId, reload}:{projectId?:string;reload:()=>Promise<unknown>}) {
  const [open,setOpen]=useState(false);
  const [archived,setArchived]=useState(false);
  const [selected,setSelected]=useState<string[]>([]);
  const items=useQuery<{id:string;name:string;archived:number}[]>(open?"archive_items":null,projectId?{project_id:projectId}:{});
  const act=useAction();
  const label=projectId?"分集":"项目";
  const shown=(items.data??[]).filter(r=>Boolean(r.archived)===archived);
  return <details className="card" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>{label}归档与恢复</summary>
    <p className="small">归档后从日常列表和选题研究中排除。已保存的内容可随时查看和恢复。</p>
    <div className="row">{[false,true].map(value=><button className="btn" key={String(value)} aria-pressed={archived===value} onClick={()=>{setArchived(value);setSelected([]);}}>{value?"已归档":"使用中"}{label}</button>)}</div>
    {shown.map(r=><div key={r.id} className="row"><label><input type="checkbox" checked={selected.includes(r.id)} onChange={e=>setSelected(e.target.checked?[...selected,r.id]:selected.filter(id=>id!==r.id))}/> {r.name}</label><a href={projectId?'#/e/'+r.id+'/story':'#/p/'+r.id}>查看</a></div>)}
    {items.data&&!shown.length&&<p>没有{archived?"已归档":"使用中"}的{label}。</p>}
    <button className="btn" disabled={!selected.length||act.busy} onClick={()=>act.run(async()=>{
      await cmd("set_archived",{kind:projectId?"episode":"project",ids:selected,archived:!archived});
      setSelected([]);await items.reload();await reload();dispatchEvent(new Event("projects-changed"));
    })}>{archived?"恢复":"归档"}所选{label}（{selected.length}）</button>
    <ErrorLine error={act.error}/>{items.error&&<p>{items.error}</p>}
  </details>;
}
