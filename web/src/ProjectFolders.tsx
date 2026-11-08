import {useEffect, useState} from "react";
import {closeProjectFolder, cmd, openProjectFolder, projectFolders} from "./api";
import {ErrorLine, Modal, useAction} from "./ui";

export function SaveProjectFolder({pid,name,close}:{pid:string;name:string;close:()=>void}) {
  const [path,setPath] = useState("");
  const [saved,setSaved] = useState<{path:string;episodes:number;files:number} | null>(null);
  const action = useAction();
  return <Modal onClose={()=>{if(!action.busy) close();}} width={500}>
    <h2>另存独立项目</h2>
    {saved ? <p role="status">{name} 已保存到 {saved.path} · {saved.episodes} 集 · {saved.files} 个文件</p>
      : <form onSubmit={e=>{e.preventDefault();action.run(async()=>setSaved(await cmd("export_project_folder",{project_id:pid,folder:path.trim()})));}}>
        <p>带上本项目的剧集、稿件、素材、运行输入和已登记交付。全局挂载资料在打开时使用本机配置。</p>
        <label className="col">新项目文件夹绝对路径<input autoFocus value={path} onChange={e=>setPath(e.target.value)} placeholder="/Volumes/制作盘/我的项目"/></label>
        <button className="btn primary" disabled={!path.trim() || action.busy}>{action.busy?"正在复制并校验…":"另存项目"}</button>
      </form>}
    <ErrorLine error={action.error}/><button className="btn" disabled={action.busy} onClick={close}>关闭</button>
  </Modal>;
}

export function OpenProjectFolder() {
  const [path,setPath] = useState("");
  const [state,setState] = useState<Awaited<ReturnType<typeof projectFolders>> | null>(null);
  const action = useAction();
  const [relocating,setRelocating] = useState<{name:string;fingerprint:string}|null>(null);
  const refresh = async()=>setState(await projectFolders());
  useEffect(()=>{action.run(refresh);},[]);
  if (state?.external) return null;
  return <details className="card"><summary>打开项目文件夹</summary>
    {relocating && <p>重新定位「{relocating.name}」 <button className="btn" onClick={()=>setRelocating(null)}>取消定位</button></p>}
    <form onSubmit={e=>{e.preventDefault();action.run(async()=>{await openProjectFolder(path.trim(),relocating?.fingerprint);setRelocating(null);await refresh();});}}>
      <label className="col">项目文件夹绝对路径<input value={path} onChange={e=>setPath(e.target.value)} placeholder="/Volumes/制作盘/我的项目"/></label>
      <button className="btn" disabled={!path.trim() || action.busy}>{action.busy?"正在读取…":"打开目录"}</button>
    </form>
    {state?.opened.map(folder=><div className="row" key={folder.path}><span>{folder.path}</span>
      <a className="btn primary" href={folder.url} target="_blank" rel="noopener noreferrer">进入独立项目</a>
      <button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{await closeProjectFolder(folder.path);await refresh();})}>关闭目录</button>
    </div>)}
    {state?.recent.filter(item=>!state.opened.some(open=>open.path===item.path)).map(item=><div className="row" key={item.path}>
      <span>{item.name} · {item.path}{item.available?"":" · 目录离线"}</span>
      {item.available ? <button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{
        await openProjectFolder(item.path,item.fingerprint);await refresh();
      })}>重新打开</button> : <button className="btn" onClick={()=>{setPath("");setRelocating(item);}}>重新定位</button>}
    </div>)}
    <ErrorLine error={action.error}/>
  </details>;
}
