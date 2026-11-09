import { useEffect, useRef, useState } from "react";
import { BookOpen, Boxes, Clapperboard, FileClock, FolderOpen, Images, ListChecks, Menu,
  MessagesSquare, NotebookPen, Palette, PanelLeftClose, PanelsTopLeft, PencilLine, Settings,
  type LucideIcon } from "lucide-react";

import { resourceOwner } from "./projectNavigation";

type EpisodeLink = { id:string; display_code:string; title:string; created_at:number };
type ProjectLink = { id:string; name:string; recentVisible:boolean; recent:EpisodeLink[] };
type NavItem = [id:string, label:string, href:string, icon:LucideIcon];

export function SideNav({active, projects, projectId, episode, hash}:{
  active:string; projects:ProjectLink[]; projectId?:string; episode:boolean; hash:string;
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
  const owner = resourceOwner(projects,projectId);
  const recent = projects.filter(p=>p.recentVisible).flatMap(p=>p.recent || []).sort((a,b)=>b.created_at-a.created_at).slice(0,5);
  useEffect(()=>{
    if (episode && projectId) {
      try { sessionStorage.setItem("director.workspace."+projectId,hash); } catch { /* optional */ }
    }
  },[episode,projectId,hash]);
  let workspace = recent.length ? "#/e/"+recent[0].id+"/story" : "";
  try {
    const saved = owner && sessionStorage.getItem("director.workspace."+owner);
    if (saved?.startsWith("#/e/")) workspace = saved;
  } catch { /* use recent episodes */ }
  const items:NavItem[] = [["idea","创作","#/",PencilLine],["projects","项目","#/projects",FolderOpen]];
  if (owner) items.push(["project","项目主页","#/p/"+owner,PanelsTopLeft]);
  items.push(["library","资产库",owner ? "#/p/"+owner+"/library" : "#/library",Images],
    ["topics","选题池",owner ? "#/p/"+owner+"/topics" : "#/topics",NotebookPen]);
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
        {!!recent.length && <><h2>最近剧集</h2>{recent.map(e=>link([e.id,e.display_code+" "+(e.title||"未命名"),"#/e/"+e.id+"/story",FileClock]))}</>}
        {!!projects.length && <><h2>项目列表</h2>{projects.map(p=>link([p.id,p.name,"#/p/"+p.id,FolderOpen]))}</>}
      </div>
      <div className="nav-system">{system.map(link)}</div>
    </nav>
  </div>;
}
