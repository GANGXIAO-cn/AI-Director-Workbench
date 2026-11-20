import { useState } from "react";
import { cmd, setProviderKey } from "./api";
import { ErrorLine, useAction, useQuery } from "./ui";

const kinds = {language: "语言", image: "图片", vision: "识图", video: "视频", audio: "音频"} as const;
type Category = keyof typeof kinds;
type Model = {id: string; categories: Category[]};
type Provider = {id: string; revision: number; name: string; address: string; auth: string; keyConfigured: boolean; envName: string;
  models: Model[]; checkedAt: number | null; syncedAt: number | null; diagnostic: string | null};
const categories = Object.keys(kinds) as Category[];
const when = (t: number) => new Date(t * 1000).toLocaleString();

function ProviderForm({current, done, cancel}: {current?: Provider; done: () => Promise<void>; cancel: () => void}) {
  const act = useAction();
  const [name, setName] = useState(current?.name ?? "");
  const [address, setAddress] = useState(current?.address ?? "");
  const [auth, setAuth] = useState(current?.auth ?? "bearer");
  return <form className="card grid" onSubmit={event => {event.preventDefault(); act.run(async () => {
    await cmd("save_provider", {name, address, auth, provider_id: current?.id, revision: current?.revision});
    await done(); cancel();
  });}}>
    <label>服务商名称<input required maxLength={120} value={name} onChange={e => setName(e.target.value)}/></label>
    <label>接口基址<input required type="url" placeholder="https://api.example.com/v1" value={address} onChange={e => setAddress(e.target.value)}/></label>
    <label>鉴权方式<select value={auth} onChange={e => setAuth(e.target.value)}><option value="bearer">Bearer 密钥</option><option value="local">本地免鉴权</option></select></label>
    {current && (address !== current.address || auth !== current.auth) && <p>更换基址或鉴权方式后，需要重新配置密钥并拉取目录，旧型号分类会清空。</p>}
    <ErrorLine error={act.error}/><div className="row"><button className="btn primary" disabled={act.busy}>保存服务商</button><button type="button" className="btn" onClick={cancel}>取消</button></div>
  </form>;
}

function ProviderCard({p, reload}: {p: Provider; reload: () => Promise<void>}) {
  const act = useAction();
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState(false);
  const [key, setKey] = useState("");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState<{id: string; categories: Category[]} | null>(null);
  const inspect = (sync: boolean) => act.run(async () => {
    try { await cmd("read_provider_models", {provider_id:p.id,revision:p.revision,sync}); } finally { await reload(); }
  });
  const models = p.models.filter(m => (filter === "all" || (filter === "unknown" ? !m.categories.length : m.categories.includes(filter as Category))) && m.id.toLowerCase().includes(search.toLowerCase()));
  return <section className="card provider-card">
    <div className="spread"><h2>{p.name}</h2><span>{p.auth === "local" ? "本地免鉴权" : p.keyConfigured ? "密钥已配置" : "密钥未配置"}</span></div>
    <div className="mono">{p.address}</div>
    <p>{categories.map(c => kinds[c] + " " + p.models.filter(m => m.categories.includes(c)).length).join(" · ")} · 未分类 {p.models.filter(m => !m.categories.length).length}</p>
    <div className="row"><button className="btn" onClick={() => {setOpen(!open); setKey("");}} aria-expanded={open}>配置与模型</button>
      <button className="btn" disabled={act.busy} onClick={() => inspect(false)}>测试连接 · 不生成</button>
      <button className="btn" disabled={act.busy} onClick={() => inspect(true)}>拉取全部模型</button></div>
    <p role="status">{p.checkedAt === null ? "尚未测试连接" : (p.diagnostic || "模型目录可达") + " · " + when(p.checkedAt)}</p>
    <p className="muted small">{p.syncedAt === null ? "尚未保存模型目录" : "最近成功拉取 " + when(p.syncedAt) + " · " + p.models.length + " 个型号"}</p>
    <ErrorLine error={act.error}/>
    {open && <div className="provider-config">
      <button className="btn" onClick={() => setEdit(!edit)}>编辑服务商</button>
      {edit && <ProviderForm current={p} done={reload} cancel={() => setEdit(false)}/>}
      {p.auth === "bearer" && <div><label>API 密钥（保存到本机配置文件）<input type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)}/></label>
        <button className="btn" disabled={!key || act.busy} onClick={() => act.run(async () => {const saved=await setProviderKey(p.id, p.revision, key); setKey(""); if(!saved.savedToConfig) throw new Error("当前运行的是旧服务，本次密钥仅在内存中。新版服务启动后可保存到配置文件。"); await reload();})}>应用密钥</button>
        <p className="small muted">工作台重启后自动读取。也可使用环境变量 <code>{p.envName}</code>。</p></div>}
      {p.models.length > 0 && <><div className="row"><label>查找型号<input value={search} onChange={e => setSearch(e.target.value)}/></label>
        <label>筛选分类<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部</option>
          {categories.map(c => <option key={c} value={c}>{kinds[c]}</option>)}<option value="unknown">未分类</option></select></label></div>
        <table className="table"><thead><tr><th>型号 ID</th><th>手工标注的用途</th></tr></thead><tbody>{models.map(m => <tr key={m.id}><td>{m.id}</td>
          <td><div className="row">{categories.map(c => <label key={c}><input type="checkbox" disabled={act.busy} checked={(pending?.id === m.id ? pending.categories : m.categories).includes(c)} onChange={e => {
            const next = e.target.checked ? [...m.categories, c] : m.categories.filter(k => k !== c);
            setPending({id:m.id,categories:next});
            act.run(async () => {try { await cmd("label_provider_model", {provider_id:p.id,revision:p.revision,model:m.id,categories:next,prior:m.categories}); } finally { await reload(); setPending(null); }});
          }}/>{kinds[c]}</label>)}</div></td></tr>)}</tbody></table>{!models.length && <p>没有符合筛选条件的型号。</p>}</>}
    </div>}
  </section>;
}

export function Providers() {
  const providers = useQuery<Provider[]>("api_providers");
  const [add, setAdd] = useState(false);
  const policy = useQuery<{quotaRetryPolicy:string}>("model_catalog");
  const change = useAction();
  return <section className="providers"><h1>我的模型服务</h1><p>拉取型号后，可在分工中选择兼容 Chat Completions 接口的模型。连接测试只读取目录。</p>
    {policy.data && <p><label>CLI 限流后<select disabled={change.busy} value={policy.data.quotaRetryPolicy} onChange={e => {
      const mode=e.target.value;
      change.run(async () => {await cmd("set_quota_retry_policy",{policy:mode}); await policy.reload();});
    }}><option value="manual_cli">手动选择原 CLI 或其他本机 CLI 重试</option><option value="same_cli">只允许原 CLI 手动重试</option></select></label></p>}
    <ErrorLine error={change.error}/>
    <button className="btn primary" onClick={() => setAdd(!add)}>添加服务商</button>
    {add && <ProviderForm done={providers.reload} cancel={() => setAdd(false)}/>}
    {providers.error && <p className="error">{providers.error}</p>}
    {providers.data?.length === 0 && !add && <p>尚未添加服务商。</p>}
    {providers.data?.map(p => <ProviderCard key={p.id+":"+p.envName} p={p} reload={providers.reload}/>)}
  </section>;
}
