import {useEffect,useState} from "react";
import {videoApi} from "./api";
import {useAction, ErrorLine} from "./ui";

type Profile = {name:string;site:string;instance:string;capacity:number;pool:string;poolCapacity:number;connected:boolean};
const instanceNames: Record<string, string> = {
  default: "Standard（24 GB）",
  plus: "Plus（48 GB）",
  ultra: "Ultra（84 GB）",
};
const emptyAccount = {name:"",site:"https://www.runninghub.ai",instance:"plus",capacity:1,pool:"",pool_capacity:1,key:""};

export function RunningHubKeys(){
  const [profiles,setProfiles]=useState<Profile[]>([]);
  const [account,setAccount]=useState(emptyAccount);
  const [editing,setEditing]=useState(false);
  const [notice,setNotice]=useState("");
  const [error,setError]=useState("");
  const act=useAction();
  useEffect(()=>{let live=true;videoApi<Profile[]>("GET","accounts").then(rows=>{if(live)setProfiles(rows);})
    .catch(e=>{if(live)setError(String(e));});return ()=>{live=false;};},[]);
  function select(p?:Profile){
    setEditing(!!p);setNotice("");
    setAccount(p?{...p,key:"",pool_capacity:p.poolCapacity}:{...emptyAccount,site:profiles[0]?.site??emptyAccount.site});
  }
  return <section><div className="spread"><h2>RunningHub API Key 管理</h2><button className="btn" onClick={()=>select()}>新增 Key</button></div>
    {error&&<p className="error">{error}</p>}
    {profiles.map(p=><div className="spread card" key={p.name}><strong>{p.name}</strong>
      <span>{p.connected?"已连接":"待连接"} · {instanceNames[p.instance] ?? p.instance} · 并发 {p.capacity}{p.pool && ` · ${p.pool} 共用 ${p.poolCapacity}`}</span>
      <button className="btn" onClick={()=>select(p)}>编辑 {p.name}</button></div>)}
    <form className="card" onSubmit={e=>{e.preventDefault();act.run(async()=>{
      if(!editing && profiles.some(p=>p.name===account.name.trim())) throw new Error("账户别名已存在，可编辑现有 Key 或使用其他别名");
      const saved=await videoApi<{savedKeyToConfig?:boolean}>("PUT","accounts",account);setAccount({...account,key:""});setEditing(true);
      setProfiles(await videoApi<Profile[]>("GET","accounts"));setNotice(account.key && !saved.savedKeyToConfig ? "当前运行的是旧服务，本次密钥仅在内存中。新版服务启动后可保存到配置文件。" : "账户已保存");
    });}}>
      <h3>{editing?"编辑 "+account.name:"新增 RunningHub Key"}</h3>
      <div className="grid3">
        <label>账户别名<input required disabled={editing} value={account.name} onChange={e=>setAccount({...account,name:e.target.value})}/></label>
        <label>站点<select value={account.site} onChange={e=>setAccount({...account,site:e.target.value})}>{["https://www.runninghub.ai","https://www.runninghub.cn"].map(s=><option key={s}>{s}</option>)}</select></label>
        <label>API Key（保存到本机配置文件）<input required={!editing} placeholder={editing?"留空沿用当前 Key":""} type="password" autoComplete="off" value={account.key} onChange={e=>setAccount({...account,key:e.target.value})}/></label>
        <label>此 Key 并发数<input type="number" min={1} value={account.capacity} onChange={e=>setAccount({...account,capacity:Number(e.target.value)})}/></label>
        <label>档位<select value={account.instance} onChange={e=>setAccount({...account,instance:e.target.value})}>{Object.entries(instanceNames).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
        <label>共享额度账户名（可留空）<input value={account.pool} onChange={e=>setAccount({...account,pool:e.target.value})}/></label>
        {account.pool&&<label>共享总并发<input type="number" min={1} value={account.pool_capacity} onChange={e=>setAccount({...account,pool_capacity:Number(e.target.value)})}/></label>}
      </div>
      <button className="btn primary" disabled={act.busy}>保存账户</button>
      <span role="status">{notice}</span><ErrorLine error={act.error}/>
    </form></section>;
}
