import { useEffect, useRef, useState } from "react";

import { commandId, commandWithFiles } from "./api";

export function clipboardFiles(data:DataTransfer) {
  if (data.files.length) return Array.from(data.files);
  return Array.from(data.items).filter(item=>item.kind==="file")
    .map(item=>item.getAsFile()).filter((file):file is File=>file!==null);
}

export function useDirectorCommand() {
  const pending = useRef<{payload:string; files:File[]; id:string} | null>(null);
  return async (name:string, args:Record<string,unknown>, files:File[]) => {
    const payload = JSON.stringify([name,args]);
    let attempt = pending.current;
    if (!attempt || attempt.payload!==payload || attempt.files.length!==files.length ||
        !attempt.files.every((file,index)=>file===files[index])) {
      attempt = {payload,files:[...files],id:commandId()};
      pending.current = attempt;
    }
    const result = await commandWithFiles(name,{...args,command_id:attempt.id},files);
    if (pending.current===attempt) pending.current=null;
    return result;
  };
}

export function isPicture(file:File) {
  return ["image/png","image/jpeg"].includes(file.type) || /\.(png|jpe?g)$/i.test(file.name);
}

export function addDirectorFiles(current:File[], incoming:File[], imagesOnly=false) {
  for (const file of incoming) {
    if (!isPicture(file) && (imagesOnly || !/\.(md|txt|docx|pdf)$/i.test(file.name)))
      throw new Error(imagesOnly ? "图片支持 PNG 或 JPEG" : "附件支持 PNG、JPEG、MD、TXT、DOCX 或 PDF");
  }
  const files = [...current,...incoming];
  if (files.length>6 || files.reduce((total,f)=>total+f.size,0)>30*1024*1024)
    throw new Error("每次最多附 6 份文件，合计 30 MiB");
  return files;
}

function FilePreview({file}:{file:File}) {
  const [url,setUrl] = useState("");
  useEffect(()=>{
    if (!isPicture(file)) {setUrl("");return;}
    const object = URL.createObjectURL(file);
    setUrl(object);
    return ()=>URL.revokeObjectURL(object);
  },[file]);
  return <>{url && <img src={url} alt={file.name} width={48} height={48} style={{objectFit:"contain"}}/>}{file.name}</>;
}

export function DirectorFiles({files,remove}:{files:File[];remove?:(index:number)=>void}) {
  if (!files.length) return null;
  return <ul className="director-files" aria-label="已选附件">{files.map((file,i)=>
    <li key={i}><FilePreview file={file}/>{remove && <button className="btn ghost" aria-label={"移除 "+file.name}
      onClick={()=>remove(i)}>×</button>}</li>)}</ul>;
}
