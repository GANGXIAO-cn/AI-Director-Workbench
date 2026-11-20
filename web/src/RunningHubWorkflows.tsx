import {useEffect,useState} from "react";
import {videoApi} from "./api";
import {ErrorLine,useAction} from "./ui";

type Workflow = {kind:"h3"|"suno";name:string;workflow:string;url:string};

function WorkflowRow({item,saved,editable}:{item:Workflow;saved:(rows:Workflow[])=>void;editable:boolean}){
  const [draft,setDraft]=useState<string|null>(null);
  const act=useAction();
  return <div className="row wrap">
    <strong>{item.name}</strong>
    <a href={item.url} target="_blank" rel="noreferrer" aria-label={`打开 ${item.name} 工作流 ${item.workflow}`}>{item.workflow}</a>
    {editable && (draft===null ? <button className="btn" onClick={()=>{act.clear();setDraft(item.workflow);}}>更换工作流</button> :
      <form className="row wrap" onSubmit={e=>{e.preventDefault();act.run(async()=>{
        saved(await videoApi<Workflow[]>("PUT","workflows/"+item.kind,{workflow:draft.trim()}));setDraft(null);
      });}}>
        <label>在线工作流编号 <input autoFocus required pattern="[0-9]+" inputMode="numeric" value={draft} onChange={e=>setDraft(e.target.value)}/></label>
        <button className="btn primary" disabled={act.busy}>保存</button>
        <button type="button" className="btn" disabled={act.busy} onClick={()=>setDraft(null)}>取消</button>
      </form>)}
    <ErrorLine error={act.error}/>
  </div>;
}

export function RunningHubWorkflows({editable=false}:{editable?:boolean}){
  const [rows,setRows]=useState<Workflow[]>([]);
  const [error,setError]=useState("");
  useEffect(()=>{
    let live=true;
    videoApi<Workflow[]>("GET","workflows").then(v=>{if(live)setRows(v);}).catch(e=>{if(live)setError(String(e));});
    return ()=>{live=false;};
  },[]);
  return <section aria-label="在线工作流" className="card">
    <div className="spread"><h2>在线工作流</h2>{!editable&&<a className="btn" href="#/sys/settings/workflows">管理工作流</a>}</div>
    {rows.map(item=><WorkflowRow key={item.kind} item={item} editable={editable} saved={setRows}/>)}
    {error&&<p className="error">{error}</p>}
  </section>;
}
