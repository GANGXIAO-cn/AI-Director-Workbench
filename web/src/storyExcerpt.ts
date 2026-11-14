import {useRef} from "react";
import {commandId,importManuscript,q} from "./api";
import type {Excerpt} from "./Resources";
export function useStoryImport(){
  const importKey=useRef({key:"",id:""});
  return async function storyImport(pid:string,original?:Excerpt|null) {
    if (!original) return undefined;
    const part=await q<Excerpt>("script_excerpt",{path:original.path,offset:original.offset,expected_sha256:original.sha256});
    part.text=Array.from(part.text).slice(0,Array.from(original.text).length).join("");
    if(part.offset!==original.offset || part.text!==original.text || !part.text.trim()) throw new Error("原文已变更，请重新选择片段");
    const key=JSON.stringify([pid,part.sha256,part.offset,part.path,part.text.length]);
    if(importKey.current.key!==key) importKey.current={key,id:commandId()};
    const body="改编来源：《"+part.title+"》 · 第 "+(part.offset+1)+"–"+(part.offset+Array.from(part.text).length)+" 字\n"+part.path+"\nSHA-256 "+part.sha256+"\n\n"+part.text;
    const r=await importManuscript(pid,new File([body],part.title+".txt",{type:"text/plain"}),"story_source",undefined,importKey.current.id);
    if(r.parseStatus!=="parsed") throw new Error("未能读取故事原文");
    return r.importId as string;
  }
}
