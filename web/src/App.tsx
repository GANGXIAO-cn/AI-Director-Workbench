import { ActivityLog } from "./ActivityLog";
import { SideNav } from "./SideNav";
import { useEffect, useLayoutEffect, useState } from "react";
import { ResourceBrowser } from "./Resources";
import Home from "./Home";
import { ProjectRoute, useProjectList } from "./projectNavigation";
import { cmd } from "./api";
import { useQuery } from "./ui";
import Project from "./Project";
import Workspace from "./Workspace";
import { ProjectPage, SystemPage } from "./ProjectPages";

type Route =
  | { page: "home"; tab?: string }
  | { page: "project"; id: string; sub?: string; arg?: string }
  | { page: "episode"; id: string; stage: string }
  | { page: "sys"; sub: string };

function parse(hash: string): Route {
  const [path] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "p" && parts[1]) return { page: "project", id: parts[1], sub: parts[2], arg: parts[3] };
  if (parts[0] === "e" && parts[1]) return { page: "episode", id: parts[1], stage: parts[2] ?? "story" };
  if (parts[0] === "sys") return { page: "sys", sub: parts[1] ?? "executors" };
  return { page: "home", tab: parts[0] };
}

type OpenProject = { id: string; hash: string; mounted?: boolean };
const tabsKey = "director.open-projects.v1";

function restoreTabs(): OpenProject[] {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(tabsKey) || "[]");
    if (!Array.isArray(saved)) return [];
    const result: OpenProject[] = [];
    for (const item of saved) {
      if (typeof item?.id !== "string" || typeof item.hash !== "string" || result.some(t => t.id === item.id)) continue;
      const route = parse(item.hash);
      if (route.page === "episode" || (route.page === "project" && route.id === item.id)) {
        if (item.hash.startsWith("#/")) result.push({ id: item.id, hash: item.hash });
      }
    }
    return result;
  } catch { return []; }
}

function ProjectPanel({ hash }: { hash: string }) {
  const route = parse(hash);
  if (route.page === "episode") return <Workspace key={route.id} id={route.id} stage={route.stage} />;
  if (route.page !== "project") return null;
  return <Shell>{route.sub
    ? <ProjectPage id={route.id} sub={route.sub} arg={route.arg} />
    : <Project id={route.id} />}</Shell>;
}

export default function App() {
  const [hash, setHash] = useState(location.hash);
  const [tabs, setTabs] = useState(restoreTabs);
  const projects = useProjectList();
  const locationInfo = useQuery<{externalProject?:boolean;dataDir:string}>("storage_status");
  const route = parse(hash);
  const episodeId = route.page === "episode" ? route.id : null;
  const owner = useQuery<{project_id: string}>(episodeId ? "episode_owner" : null, { episode_id: episodeId });
  const active = route.page === "project" ? route.id : owner.data?.project_id;
  const names = new Map((projects.data || []).map(p => [p.id, p.name]));
  useEffect(() => {
    const on = () => setHash(location.hash);
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (active) {
      localStorage.setItem("lastProject", active);
      void cmd("visit_project", {project_id:active}).then(() => dispatchEvent(new Event("projects-changed"))).catch(() => projects.reload());
    }
  }, [active, projects.reload]);
  useLayoutEffect(() => {
    if (!active) return;
    setTabs(previous => {
      const tab = previous.find(t => t.id === active);
      if (tab?.hash === hash && tab.mounted) return previous;
      const current = { id: active, hash, mounted: true };
      return tab ? previous.map(t => t.id === active ? current : t) : [...previous, current];
    });
  }, [active, hash]);
  useEffect(() => {
    try { sessionStorage.setItem(tabsKey, JSON.stringify(tabs.map(({id,hash}) => ({id,hash})))); } catch { /* storage disabled */ }
  }, [tabs]);
  function close(id: string) {
    const remaining = tabs.filter(t => t.id !== id);
    if (id === active) {
      const neighbor = remaining[Math.min(tabs.findIndex(t => t.id === id), remaining.length - 1)];
      location.hash = neighbor?.hash || "#/projects";
    }
    setTabs(remaining);
  }
  function navigateFrom(tab: OpenProject, next: string) {
    if (location.hash === tab.hash) {
      location.hash = next;
    } else {
      // A command can finish after the user has switched tabs. Continue only in its original project.
      setTabs(previous => previous.map(t => t.id === tab.id && t.hash === tab.hash ? {...t, hash:next} : t));
    }
  }
  const projectPage = route.page === "project" || route.page === "episode";
  const navActive = route.page === "episode" ? "episode" : route.page === "sys" ? route.sub
    : route.page === "project" ? (["library","topics"].includes(route.sub || "") ? route.sub! : "project")
    : route.tab || "idea";
  return <div className="project-desktop">
    {locationInfo.data?.externalProject && <div className="folder-location">独立项目 · 保存到 {locationInfo.data.dataDir}</div>}
    <div className="desktop-tools">{!!tabs.length && <nav aria-label="已打开的项目" className="project-tabs">
      {tabs.map(t => <span key={t.id} className={t.id === active ? "active" : ""}>
        <a aria-current={t.id === active ? "page" : undefined} href={t.hash}>{names.get(t.id) || "项目"}</a>
        <button type="button" aria-label={`关闭项目标签 ${names.get(t.id) || ""}`} onClick={() => close(t.id)}>×</button>
      </span>)}
      <a className="open-project" href="#/projects">＋ 打开项目</a>
    </nav>}<ActivityLog projects={projects.data || []} current={active}/></div>
    <div className="desktop-body"><SideNav active={navActive} projects={projects.data || []} projectId={active}
      episode={route.page === "episode"} hash={hash}/>
    {tabs.filter(t => t.mounted).map(t => <div className="desktop-page" key={t.id} hidden={active !== t.id}>
      <ProjectRoute.Provider value={{ hash: t.hash, active: active === t.id, go: next => navigateFrom(t, next) }}><ProjectPanel hash={t.hash} /></ProjectRoute.Provider>
    </div>)}
    {!projectPage && <div className="desktop-page">{route.page === "sys" ? <Shell><SystemPage sub={route.sub} /></Shell>
      : route.tab === "knowledge" || route.tab === "palette" ? <Shell><ResourceBrowser key={route.tab} kind={route.tab}/></Shell>
      : <Home tab={route.tab} projects={projects} />}</div>}
    {projectPage && !active && <div className="page">{owner.error || "读取项目中…"} {owner.error && <a href="#/projects">返回项目</a>}</div>}
  </div></div>;
}

function Shell({children}: {children:React.ReactNode}) {
  return <div className="app"><main className="main">{children}</main></div>;
}
