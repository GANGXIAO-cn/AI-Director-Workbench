import { useEffect, useRef, useState } from "react";
import { AttentionTag, useAttention, useAttentionTitle } from "./Attention";
import { BookOpen, Boxes, Clapperboard, FileClock, FolderOpen, Images, ListChecks, Menu,
  MessagesSquare, NotebookPen, Palette, PanelLeftClose, PencilLine, Settings,
  type LucideIcon } from "lucide-react";

type EpisodeLink = { id:string; display_code:string; title:string; created_at:number };
type ProjectLink = { id:string; name:string; recentVisible:boolean; recent:EpisodeLink[] };
type NavItem = [id:string, label:string, href:string, icon:LucideIcon];

export function SideNav({active, projects, projectId, owner, episode, hash}:{
  active:string; projects:ProjectLink[]; projectId?:string; owner?:string; episode:boolean; hash:string;
}) {
  const [opened,setOpened] = useState(false);
  const nav = useRef<HTMLElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(()=>setOpened(false),[hash]);
  useEffect(()=>{
    if (!opened) return;
    const close = (e:KeyboardEvent) => {
      if (e.key==="Escape") { e.preventDefault(); e.stopPropagation(); setOpened(false); toggle.current?.focus(); }
    };
    const outside = (e:PointerEvent) => { if (!nav.current?.contains(e.target as Node)) setOpened(false); };
    document.addEventListener("keydown",close,true); document.addEventListener("pointerdown",outside);
    return ()=>{document.removeEventListener("keydown",close,true);document.removeEventListener("pointerdown",outside);};
  },[opened]);
  const recent = projects.filter(p=>p.recentVisible).flatMap(p=>p.recent || []).sort((a,b)=>b.created_at-a.created_at).slice(0,5);
  useEffect(()=>{
    if (episode && projectId) {
      try { sessionStorage.setItem("director.workspace."+projectId,hash); } catch { /* optional */ }
    }
  },[episode,projectId,hash]);
  const attention=useAttention();useAttentionTitle(attention);
  let workspace = recent.length ? "#/e/"+recent[0].id+"/story" : "";
  try {
    const saved = owner && sessionStorage.getItem("director.workspace."+owner);
    if (saved?.startsWith("#/e/")) workspace = saved;
  } catch { /* use recent episodes */ }
  const items:NavItem[] = [["idea","创作","#/",PencilLine],["projects","项目","#/projects",FolderOpen]];
  items.push(["library","资产库",owner ? "#/p/"+owner+"/library" : "#/library",Images]);
  items.push(["knowledge","剧本知识库","#/knowledge",BookOpen],["palette","导演风格与色卡","#/palette",Palette],
    ["deliveries","本地交付包","#/deliveries",Boxes]);
  if (episode || workspace) items.push(["episode","导演工作区",episode?hash:workspace,MessagesSquare]);
  const system:NavItem[]=[["tasks","任务记录","#/sys/tasks",ListChecks],["settings","设置","#/sys/settings",Settings]];
  const link=([key,label,href,Icon]:NavItem)=><a key={key} className={"nav"+(active===key?" on":"")} href={href}
    aria-label={label} aria-current={active===key?"page":undefined} title={label} onClick={()=>setOpened(false)}>
    <Icon size={17} aria-hidden="true"/><span>{label}</span></a>;
  return <div className={"nav-dock"+(episode?" compact":"")}>
    <nav className={"sidenav global-nav"+(opened?" expanded":"")} aria-label="主导航" ref={nav}
      onBlur={e=>{if (opened && e.relatedTarget && !e.currentTarget.contains(e.relatedTarget as Node)) setOpened(false);}}>
      <div className="nav-brand"><a href="#/" title="导演工作台首页"><Clapperboard size={19}/><span>导演工作台</span></a>
        <button type="button" ref={toggle} aria-label={opened?"收起主导航":"展开主导航"} aria-expanded={opened}
          onClick={()=>setOpened(!opened)}>{opened?<PanelLeftClose size={19}/>:<Menu size={19}/>}</button></div>
      {items.map(link)}
      <div className="nav-history">
        {!!attention.length && <><h2>需要你处理</h2>{attention.map(a=><div key={"attn-"+a.episodeId} className="nav-episode">{link(["attn-"+a.episodeId,a.displayCode+" "+(a.title||"未命名"),"#/e/"+a.episodeId+"/story",FileClock])}<AttentionTag item={a}/></div>)}</>}
        {!!recent.length && <><h2>最近剧集</h2>{recent.map(e=><div key={e.id} className="nav-episode">{link([e.id,e.display_code+" "+(e.title||"未命名"),"#/e/"+e.id+"/story",FileClock])}<AttentionTag item={attention.find(a=>a.episodeId===e.id)}/></div>)}</>}
        {!!projects.length && <><h2>项目列表</h2>{projects.map(p=><div key={p.id}>{link([p.id,p.name,"#/p/"+p.id,FolderOpen])}
          {p.id===projectId&&<div className="project-subnav">{link(["topics","选题池","#/p/"+p.id+"/topics",NotebookPen])}</div>}</div>)}</>}
      </div>
      <div className="nav-system">{system.map(link)}</div>
    </nav>
  </div>;
}
