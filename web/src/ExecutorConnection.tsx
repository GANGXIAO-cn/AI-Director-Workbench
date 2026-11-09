import {useState} from "react";
import {cmd,q} from "./api";
import {ErrorLine, useAction} from "./ui";

type Connection={revision:number;proxy:string;model:string|null;effort:string|null};
type Model={id:string|null;label:string;efforts:(string|null)[]};
export function ExecutorConnection({provider,settings,models,reload,probe,pathDirty}:{provider:string;settings:Connection;models:Model[];probe:{executable?:string;version?:string;missing_flags?:string[];status:string};pathDirty:boolean;reload:()=>Promise<void>}) {
  const [model,setModel]=useState(settings.model);
  const [effort,setEffort]=useState(settings.effort);
  const [proxyMode,setProxyMode]=useState(["system","off"].includes(settings.proxy)?settings.proxy:"custom");
  const [endpoint,setEndpoint]=useState(proxyMode==="custom"?settings.proxy:"");
  const act=useAction();
  const [notice,setNotice]=useState("");
  const proxy=proxyMode==="custom"?endpoint:proxyMode;
  const dirty=model!==settings.model || effort!==settings.effort || proxy!==settings.proxy;
  const choices=models.find(m=>m.id===model)?.efforts ?? [null];
  const [verification,setVerification]=useState<{text:string;login?:boolean}|null>(null);
  const [checking,setChecking]=useState(false);
  const shellName = probe.executable ? "'"+probe.executable.replaceAll("'","'\\''")+"'" : provider;
  const loginCommand = shellName+(provider==="codex"?" login":provider==="claude"?" auth login":"");
  async function verify() {
    setChecking(true);
    setVerification(null);
    try {
      if (probe.status==="simulated") {setVerification({text:"当前使用本地模拟执行器。"});return;}
      if (!probe.executable || !probe.version) {setVerification({text:"无法运行 CLI，请检查可执行文件路径，保存后重新检测。"});return;}
      if (probe.missing_flags?.length) {setVerification({text:"当前 CLI 缺少工作台所需参数："+probe.missing_flags.join("、")});return;}
      if (provider==="antigravity") {
        await cmd("refresh_cli_models",{provider});
        setVerification({text:"连接已验证，Google CLI 已返回可用型号。"});
        await reload();
      } else {
        const user=await q<{authenticated:boolean|null;accountLabel?:string;unavailable?:boolean}>("executor_identity",{provider});
        if (user.unavailable) setVerification({text:"未找到 CLI，请检查并保存程序路径。"});
        else if (user.authenticated) setVerification({text:"本机登录已验证"+(user.accountLabel?" · "+user.accountLabel:"")});
        else setVerification({text:user.authenticated===false?"CLI 提示尚未登录。":"CLI 未能确认当前登录状态。",login:true});
      }
    } catch(e) {
      const text=e instanceof Error?e.message:"无法完成连接验证，请重试。";
      setVerification({text,login:provider==="antigravity" && text.includes("登录")});
    } finally {setChecking(false);}
  }
  return <div className="col">
    <button className="btn primary" disabled={checking||act.busy||dirty||pathDirty} onClick={verify}>{checking?"验证中…":"验证连接"}</button>
    {(dirty||pathDirty)&&<small>请先保存改动，再验证连接。</small>}
    {verification&&!dirty&&!pathDirty&&<div className="connection-result" role="status">{verification.text}
      {verification.login&&<p>在本机终端运行 <code>{loginCommand}</code>，按提示登录，然后再点“验证连接”。</p>}</div>}

    <label>默认型号 <select value={model??""} onChange={e=>{setModel(e.target.value||null);setEffort(null);}}>
      {models.map(m=><option key={m.id??""} value={m.id??""}>{m.label}</option>)}</select></label>
    {choices.length>1&&<label>默认强度 <select value={effort??""} onChange={e=>setEffort(e.target.value||null)}>
      {choices.map(v=><option key={v??""} value={v??""}>{v??"执行器默认"}</option>)}</select></label>}
    <label>连接代理 <select value={proxyMode} onChange={e=>setProxyMode(e.target.value)}>
      <option value="system">跟随本机</option><option value="off">直连</option><option value="custom">指定本机代理</option></select></label>
    {proxyMode==="custom"&&<label>本机代理地址 <input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="http://127.0.0.1:端口"/></label>}
    <button className="btn" disabled={!dirty||act.busy||checking} onClick={()=>act.run(async()=>{
      await cmd("save_executor_connection",{provider,revision:settings.revision,model,effort,proxy});
      await reload();
    })}>保存连接设置</button>
    <button className="btn" disabled={act.busy||checking} onClick={()=>act.run(async()=>{
      setNotice("");
      const res=await cmd<{message:string}>("refresh_cli_models",{provider});
      await reload();setNotice(res.message);
    })}>刷新本机型号</button>
    {notice&&<p role="status">{notice}</p>}
    <ErrorLine error={act.error}/>
  </div>;
}
