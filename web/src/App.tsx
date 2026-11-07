import { useEffect, useState } from "react";
import Home from "./Home";
import Project from "./Project";
import Workspace from "./Workspace";
import { ProjectPage, SystemPage } from "./ProjectPages";

type Route =
  | { page: "home"; tab?: string }
  | { page: "project"; id: string; sub?: string; arg?: string }
  | { page: "episode"; id: string; stage: string }
  | { page: "sys"; sub: string };

function parse(): Route {
  const [path] = location.hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "p" && parts[1]) return { page: "project", id: parts[1], sub: parts[2], arg: parts[3] };
  if (parts[0] === "e" && parts[1]) return { page: "episode", id: parts[1], stage: parts[2] ?? "story" };
  if (parts[0] === "sys") return { page: "sys", sub: parts[1] ?? "executors" };
  return { page: "home", tab: parts[0] };
}

export default function App() {
  const [route, setRoute] = useState(parse());
  useEffect(() => {
    const on = () => setRoute(parse());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  if (route.page === "home") return <Home tab={route.tab} />;
  if (route.page === "episode") return <Workspace id={route.id} stage={route.stage} />;
  if (route.page === "sys") return <Shell><SystemPage sub={route.sub} /></Shell>;
  if (route.sub) return <Shell projectId={route.id}><ProjectPage id={route.id} sub={route.sub} arg={route.arg} /></Shell>;
  return <Shell projectId={route.id}><Project id={route.id} /></Shell>;
}

export function Shell({ children, projectId }: { children: React.ReactNode; projectId?: string }) {
  return (
    <div className="app">
      <nav className="rail">
        <a href="#/" title="首页"><div className="logo">导</div></a>
        {projectId && <a href={`#/p/${projectId}`} title="项目主页" className="on">▶</a>}
        <a href="#/sys/tasks" title="任务记录">▤</a>
        <a href="#/sys/executors" title="执行与分工">⚙</a>
        <a href="#/sys/storage" className="bottom" title="服务与存储">?</a>
      </nav>
      <main className="main">{children}</main>
    </div>
  );
}
