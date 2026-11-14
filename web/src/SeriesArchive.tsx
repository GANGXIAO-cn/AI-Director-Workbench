import { useEffect, useState } from "react";
import { blob } from "./api";

type Entry = {path:string; title:string; section:string; sha256:string};

function useBlobText(digest?:string) {
  const [result,setResult] = useState({digest:"",text:"",error:""});
  useEffect(()=>{
    if (!digest) return;
    const controller = new AbortController();
    fetch(blob(digest)!,{signal:controller.signal}).then(response=>{
      if (!response.ok) throw new Error("无法读取项目资料");
      return response.text();
    }).then(text=>setResult({digest,text,error:""})).catch(error=>{
      if (!controller.signal.aborted) setResult({digest,text:"",error:String(error)});
    });
    return ()=>controller.abort();
  },[digest]);
  return result.digest===digest ? result : undefined;
}

export function SeriesArchive({digest}:{digest?:string}) {
  const catalog = useBlobText(digest);
  const [section,setSection] = useState("角色与系列");
  const [path,setPath] = useState("系列设定.md");
  const files:Entry[] = catalog?.text ? JSON.parse(catalog.text) : [];
  const group = files.filter(item=>item.section===section);
  const selected = group.find(item=>item.path===path) ?? group[0];
  const picture = selected?.path.endsWith(".png");
  const content = useBlobText(picture ? undefined : selected?.sha256);
  if (!digest) return null;
  return <section className="card"><h2>系列创作资料</h2>
    <div className="row"><label>资料分类 <select value={section} onChange={e=>setSection(e.target.value)}>
      {[...new Set(files.map(item=>item.section))].map(name=><option key={name}>{name}</option>)}
    </select></label><label>资料原件 <select value={selected?.path ?? ""} onChange={e=>setPath(e.target.value)}>
      {group.map(item=><option key={item.path} value={item.path}>{item.title}</option>)}
    </select></label></div>
    {(catalog?.error || content?.error) && <p className="error">{catalog?.error || content?.error}</p>}
    {selected && <><small>{selected.path} · <a href={blob(selected.sha256)} download={selected.path.split("/").pop()}>下载原件</a></small>
      {picture ? <div><img src={blob(selected.sha256)} alt={selected.title} style={{maxWidth:"100%",maxHeight:440}}/></div> :
        <pre style={{whiteSpace:"pre-wrap",maxHeight:440,overflow:"auto"}}>{content?.text}</pre>}</>}
  </section>;
}
