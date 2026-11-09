import {useState} from "react";
import {cmd} from "./api";
import {ErrorLine, useAction} from "./ui";

type Connection={revision:number;proxy:string;model:string|null;effort:string|null};
type Model={id:string|null;label:string;efforts:(string|null)[]};
export function ExecutorConnection({provider,settings,models,reload}:{provider:string;settings:Connection;models:Model[];reload:()=>Promise<void>}) {
  const [model,setModel]=useState(settings.model);
  const [effort,setEffort]=useState(settings.effort);
  const [proxyMode,setProxyMode]=useState(["system","off"].includes(settings.proxy)?settings.proxy:"custom");
  const [endpoint,setEndpoint]=useState(proxyMode==="custom"?settings.proxy:"");
  const act=useAction();
  const [notice,setNotice]=useState("");
  const proxy=proxyMode==="custom"?endpoint:proxyMode;
  const dirty=model!==settings.model || effort!==settings.effort || proxy!==settings.proxy;
  const choices=models.find(m=>m.id===model)?.efforts ?? [null];
  return <div className="col">
    <label>默认型号 <select value={model??""} onChange={e=>{setModel(e.target.value||null);setEffort(null);}}>
      {models.map(m=><option key={m.id??""} value={m.id??""}>{m.label}</option>)}</select></label>
    {choices.length>1&&<label>默认强度 <select value={effort??""} onChange={e=>setEffort(e.target.value||null)}>
      {choices.map(v=><option key={v??""} value={v??""}>{v??"执行器默认"}</option>)}</select></label>}
    <label>连接代理 <select value={proxyMode} onChange={e=>setProxyMode(e.target.value)}>
      <option value="system">跟随本机</option><option value="off">直连</option><option value="custom">指定本机代理</option></select></label>
    {proxyMode==="custom"&&<label>本机代理地址 <input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="http://127.0.0.1:端口"/></label>}
    <button className="btn" disabled={!dirty||act.busy} onClick={()=>act.run(async()=>{
      await cmd("save_executor_connection",{provider,revision:settings.revision,model,effort,proxy});
      await reload();
    })}>保存连接设置</button>
    <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
      setNotice("");
      const res=await cmd<{message:string}>("refresh_cli_models",{provider});
      await reload();setNotice(res.message);
    })}>刷新本机型号</button>
    {notice&&<p role="status">{notice}</p>}
    <ErrorLine error={act.error}/>
  </div>;
}
