import { useState } from "react";
import { commandId, upload } from "./api";
import { ErrorLine, useAction } from "./ui";

type Asset = {id:string; name:string; kind:string; version_no:number};

export function MediaPreview({episode, asset, label}:{episode:string; asset:Asset; label:string}) {
  const url = `/api/reference-media/${encodeURIComponent(episode)}/${encodeURIComponent(asset.id)}`;
  const props = {src:url, controls:true, preload:"metadata", "aria-label":label + "预览", style:{width:"100%"}};
  return <>{asset.kind === "video" ? <video {...props}/> : <audio {...props}/>}
    <a className="small" href={url} download={asset.name}>{asset.name}</a></>;
}

export function PromptMedia({episode, assets, reload, attach, busy}:{
  episode:string; assets:Asset[]; reload:()=>Promise<void>; attach:(id:string,purpose:string)=>Promise<unknown>; busy:boolean
}) {
  const [selected, setSelected] = useState("");
  const [purpose, setPurpose] = useState("");
  const act = useAction();
  const file = assets.find(a=>a.id===selected);
  return <fieldset disabled={act.busy || busy} className="card line">
    <legend>音视频参考</legend>
    <div className="row">
      <label className="btn">上传到本集<input aria-label="上传本集音视频参考" type="file"
        accept=".mp4,.mov,.avi,.mkv,.mp3,.wav,.flac" style={{display:"none"}} onChange={e=>{
          const input=e.currentTarget, picked=input.files?.[0]; input.value="";
          if (picked) void act.run(async()=>{
            const saved=await upload("reference-media", picked, {episodeId:episode, commandId:commandId()});
            setSelected(saved.versionId); await reload();
          });
        }}/></label>
      <select aria-label="本集可选音视频" value={selected} onChange={e=>setSelected(e.target.value)}>
        <option value="">选择本集音视频</option>{assets.map(a=><option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}
      </select>
      <input aria-label="音视频参考用途" placeholder="参考用途，如转身节奏" value={purpose} onChange={e=>setPurpose(e.target.value)}/>
      <button className="btn primary" disabled={!file || !purpose.trim()} onClick={async()=>{
        if (await attach(selected, purpose)) {setSelected(""); setPurpose("");}
      }}>用于本段</button>
    </div>
    <small>每份 2–15 秒。同段视频最多 1 份，音频最多 3 份，每类合计最多 15 秒。</small>
    {file && <div style={{maxWidth:300}}><MediaPreview episode={episode} asset={file} label="所选文件"/></div>}
    <ErrorLine error={act.error}/>
  </fieldset>;
}
