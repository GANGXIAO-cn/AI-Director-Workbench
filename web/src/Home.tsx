/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { blob, cmd, q, upload } from "./api";
import { ErrorLine, go, useAction, useQuery } from "./ui";

const TABS: [string, string][] = [["import", "⇧ 导入故事稿 / 剧本"], ["idea", "✎ 从一个想法开始"], ["episode", "＋ 给项目开一集"],
  ["delivery", "↗ 图文交付"], ["reference", "从参考片写剧本"]];
const CHIPS = ["/ 从已成稿选题开一集", "/ 选题研究", "/ 承接上一集开新一集", "/ 审一份已有故事稿", "/ 修改进行中的一段"];
const FEATURES: [string, string, string][] = [["故事开发", "选题 → 故事稿 → 独审", "linear-gradient(135deg,#3b5aa8,#7aa7e0)"],
  ["双模型协作", "Claude 写 · Codex 审", "linear-gradient(135deg,#5b3aa8,#c06ae0)"],
  ["九宫格分镜", "grid / flex 段落设计", "linear-gradient(135deg,#1f6f5a,#7ad0a8)"],
  ["素材与选图", "必用 · 补选 · 灰度", "linear-gradient(135deg,#8a5a2a,#e0b07a)"],
  ["专家模式", "授权后自动连续执行", "linear-gradient(135deg,#0f4c6a,#22d3ee)"]];

export function SideNav({ active }: { active: string }) {
  const projects = useQuery<any[]>("list_projects");
  const [recent, setRecent] = useState<any[]>([]);
  useEffect(() => {
    (async () => {
      const ps = projects.data ?? [];
      const all: any[] = [];
      for (const p of ps.slice(0, 3)) {
        const v = await q("project_view", { project_id: p.id });
        all.push(...v.episodes.filter((e: any) => e.kickoff_id).slice(-3).map((e: any) => ({ ...e, project: p.name })));
      }
      setRecent(all.slice(0, 5));
    })();
  }, [projects.data]);
  return (
    <aside className="sidenav">
      <div className="brand"><div className="logo" style={{ width: 26, height: 26, borderRadius: 7, background: "linear-gradient(135deg,#22d3ee,#3b82f6)", display: "grid", placeItems: "center", color: "#fff", fontSize: 13 }}>导</div>导演工作台</div>
      {[["idea", "◇ 创作", "#/"], ["projects", "◉ 项目", "#/projects"], ["library", "◆ 资产库", "#/library"],
        ["topics", "✦ 选题池", "#/topics"], ["knowledge", "▤ 知识库", "#/knowledge"]].map(([k, label, href]) => (
        <a key={k} href={href} className={`nav ${active === k ? "on" : ""}`}>{label}</a>))}
      <a className="nav on" style={{ marginTop: 14, background: "#1c1d21" }} href={recent[0] ? `#/e/${recent[0].id}/story` : "#/"}>▣ 导演工作区 <span className="tag blue">双模型</span></a>
      <div className="sec">最近剧集</div>
      {recent.map((e) => <a key={e.id} className="nav small" href={`#/e/${e.id}/story`}>▧ {e.display_code} {e.title}</a>)}
      <div className="sec">其他项目</div>
      {(projects.data ?? []).map((p) => <a key={p.id} className="nav small" href={`#/p/${p.id}`}>▨ {p.name}</a>)}
      <div style={{ marginTop: "auto" }} />
      <a className="nav small" href="#/sys/executors">• 执行与分工</a>
      <a className="nav small" href="#/sys/skills">• 技能包</a>
      <a className="nav small" href="#/sys/storage">• 设置</a>
    </aside>
  );
}

let CONFIGS: any[] = [];
fetch("/api/configs").then((r) => r.json()).then((c) => { CONFIGS = c; });

async function ensureProject(pid: string, name: string) {
  if (pid && !pid.startsWith("__new")) return pid;
  const config = pid.startsWith("__new:") ? pid.slice(6) : "_template";
  return (await cmd("create_project", { config_id: config, name: config === "_template" ? name : undefined })).projectId;
}

export default function Home({ tab }: { tab?: string }) {
  const [mode, setMode] = useState(TABS.some(([k]) => k === tab) ? tab! : "idea");
  const [text, setText] = useState("");
  const [projectId, setProjectId] = useState("");
  const [pop, setPop] = useState<"" | "style" | "topic" | "settings" | "project">("");
  const [file, setFile] = useState<File | null>(null);
  const [purpose, setPurpose] = useState("story_source");
  const [code, setCode] = useState("");
  const [recognized, setRecognized] = useState<any[]>([]);
  const projects = useQuery<any[]>("list_projects");
  const project = useQuery<any>(projectId ? "project_overview" : null, { project_id: projectId });
  const topics = useQuery<any[]>(projectId ? "topics" : null, { project_id: projectId });
  const exec = useQuery<any>("probe");
  const act = useAction();
  const list = projects.data ?? [];
  useEffect(() => { if (!projectId && list.length) setProjectId(list.find((p) => p.config_id === "puggy")?.id ?? list[0].id); }, [list.length]);
  useEffect(() => {
    if (tab && ["library", "topics", "knowledge"].includes(tab) && list.length) go(`#/p/${(list.find((p) => p.config_id === "puggy") ?? list[0]).id}/${tab}`);
  }, [tab, list.length]);
  const nextCode = () => {
    const eps = project.data?.episodes ?? [];
    const nums = eps.map((e: any) => parseInt(e.display_code.replace(/\D/g, ""), 10)).filter((n: number) => !isNaN(n));
    const prefix = eps[0]?.display_code.replace(/\d+/g, "") || "E";
    return `${prefix}${String((nums.length ? Math.max(...nums) : 0) + 1).padStart(2, "0")}`;
  };
  const send = () => act.run(async () => {
    if (mode === "idea" || mode === "episode") {
      const pid = await ensureProject(projectId, text.slice(0, 12) || "新项目");
      const ep = await cmd("create_episode", { project_id: pid, display_code: code || (pid === projectId ? nextCode() : "E01") });
      sessionStorage.setItem("kickoff-prefill", JSON.stringify({ episodeId: ep.episodeId, idea: text }));
      go(`#/p/${pid}`);
    } else if (mode === "import") {
      if (!file) throw new Error("请先选择故事稿或剧本文件");
      const pid = await ensureProject(projectId, file.name.replace(/\.[^.]+$/, "").slice(0, 12));
      const fresh = pid !== projectId;
      setProjectId(pid);
      const ep = await cmd("create_episode", { project_id: pid, display_code: code || (fresh ? "E01" : nextCode()) });
      const fd = new FormData();
      fd.append("file", file); fd.append("purpose", purpose); fd.append("episodeId", ep.episodeId);
      const token = (await (await fetch("/api/session")).json()).token;
      const res = await fetch(`/api/projects/${pid}/imports`, { method: "POST", headers: { "x-adw-token": token }, body: fd });
      const r = await res.json();
      if (!res.ok) throw new Error(r.error);
      setRecognized([{ name: file.name, purpose, episode: ep.displayCode ?? (code || "E01"), episodeId: ep.episodeId, projectId: pid, ...r }]);
    } else if (mode === "reference") {
      if (!file) throw new Error("请选择本地参考片");
      const pid = await ensureProject(projectId, text.slice(0, 12) || "参考片项目");
      setProjectId(pid);
      const src = await upload("reference", file, { projectId: pid });
      if (src.state !== "readable") throw new Error("原片不可读：" + JSON.stringify(src.probe));
      const pack = await cmd("create_pack", { source_id: src.sourceId, request: text });
      go(`#/p/${pid}/ref/${pack.packId}`);
    } else if (mode === "delivery") {
      go(`#/p/${projectId}`);
    }
  });
  const statusOf = (name: string) => exec.data?.[name]?.status === "available";
  return (
    <div className="app">
      <SideNav active={tab === "projects" ? "projects" : "idea"} />
      <main className="main home">
        <div className="row" style={{ justifyContent: "flex-end", paddingTop: 16 }}>
          {["claude", "codex"].map((n) => <span key={n} className="chip"><span className="dot" style={{ background: statusOf(n) ? "var(--green)" : "var(--muted)" }} />{n === "claude" ? "Claude Code" : "Codex"}</span>)}
          <a className="chip" href="#/sys/storage">本地交付包</a>
          <span style={{ width: 26, height: 26, borderRadius: "50%", background: "linear-gradient(135deg,#22d3ee,#3b82f6)" }} />
        </div>
        <div className="hero">
          <h1>和导演助手聊聊这一集</h1>
          <div className="bigbox" style={{ position: "relative" }}>
            <div className="tabs5">{TABS.map(([k, label]) => (
              <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>{label}{k === "delivery" && <span className="tag blue" style={{ marginLeft: 6 }}>New</span>}</button>))}</div>
            <div className="row" style={{ padding: 14, alignItems: "flex-start", flexWrap: "nowrap" }}>
              {(mode === "import" || mode === "reference") ? (
                <label className="drop" style={{ cursor: "pointer" }}>
                  <input type="file" style={{ display: "none" }} accept={mode === "reference" ? "video/*" : ".md,.txt"} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                  <div>＋<br />{file ? file.name : mode === "reference" ? "本地参考片" : "故事稿 / 剧本 / 参考图"}<br /><span className="tiny">{mode === "reference" ? "默认研究全片" : "或从选题池拖入卡片"}</span></div>
                </label>
              ) : <div className="drop">＋<br />故事稿 / 剧本 / 参考图<br /><span className="tiny">或从选题池拖入卡片</span></div>}
              <textarea style={{ border: 0, background: "transparent", minHeight: 120 }} value={text} onChange={(e) => setText(e.target.value)}
                placeholder={mode === "reference" ? "希望借鉴什么？例：借检票亭里认人的节奏，把故事放到末班渡轮上" :
                  `${nextCode()}：MILO 在车道上开了家洗车店，第一位客户是女主人的红色汽车……`} />
            </div>
            {mode === "import" && (
              <div className="row" style={{ padding: "0 14px 10px" }}>
                <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                  <option value="story_source">故事稿 → 故事来源</option><option value="script_source">剧本 → 故事来源</option>
                </select>
                <input placeholder={`归到 ${nextCode()}`} value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 120 }} />
                <span className="muted small">导入的故事稿、剧本只作故事来源，开工后先写故事稿并过独审。</span>
              </div>
            )}
            <div className="row" style={{ padding: "10px 14px", borderTop: "1px solid var(--line)" }}>
              <button className="btn ghost" onClick={() => setPop(pop === "project" ? "" : "project")}>☺ {list.find((p) => p.id === projectId)?.name ?? (projectId.startsWith("__new:") ? `新项目（${CONFIGS.find((c) => "__new:" + c.configId === projectId)?.name}）` : "新项目")} ▾</button>
              <button className="btn ghost" onClick={() => setPop(pop === "style" ? "" : "style")}>◐ 风格</button>
              <button className="btn ghost" onClick={() => setPop(pop === "topic" ? "" : "topic")}>✦ 选题</button>
              <span className="chip">▯ {project.data?.project?.spec_preset?.aspectRatio ?? "9:16"}</span>
              <button className="btn ghost" onClick={() => setPop(pop === "settings" ? "" : "settings")}>⇄ 规格预设：{project.data?.project?.spec_preset?.targetDuration === "over_60" ? "竖屏短片" : "通用"} · Claude 写 / Codex 审 · 手工</button>
              <span className="muted small" style={{ marginLeft: "auto" }}>选题池 {(topics.data ?? []).filter((t) => t.status === "已成稿").length} 张已成稿</span>
              <button className="btn primary" style={{ borderRadius: "50%", width: 34, height: 34, padding: 0 }} disabled={act.busy} onClick={send}>↑</button>
            </div>
            {pop && <Popover kind={pop} close={() => setPop("")} projects={list} projectId={projectId} setProjectId={setProjectId}
              project={project.data} topics={topics.data ?? []} pickTopic={(t: any) => { setText(`${t.code}：${t.premise}`); setPop(""); }} />}
          </div>
          <ErrorLine error={act.error} />
          {recognized.length > 0 && (
            <div className="card line" style={{ width: 900, margin: "14px auto", textAlign: "left" }}>
              <h3>识别与归位</h3>
              {recognized.map((r) => (
                <div key={r.importId} className="spread">
                  <span>{r.name} → {r.episode} · {r.purpose === "script_source" ? "剧本（故事来源）" : "故事稿（故事来源）"} · {r.parseStatus === "parsed" ? <span className="ok">已解析</span> : <span className="error">{r.diagnostic}</span>}</span>
                  <button className="btn primary" onClick={() => { sessionStorage.setItem("kickoff-prefill", JSON.stringify({ episodeId: r.episodeId, importId: r.importId })); go(`#/p/${r.projectId}`); }}>去开工 {r.episode}</button>
                </div>))}
            </div>
          )}
          <div className="row" style={{ justifyContent: "center", marginTop: 22 }}>
            {CHIPS.map((c) => <button key={c} className="btn ghost" style={{ borderRadius: 20 }} onClick={() => setText(c.slice(2))}>{c}</button>)}
          </div>
        </div>
        <h2 style={{ marginTop: 40 }}>亮点功能</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 12 }}>
          {FEATURES.map(([t, d, bg]) => <div key={t} className="feature"><div className="art" style={{ background: bg }} /><div><b>{t}</b><div className="muted tiny">{d}</div></div></div>)}
        </div>
        <div className="spread" style={{ marginTop: 30 }}><h2>我的项目</h2><span className="muted small">全部 ›</span></div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
          {list.map((p) => <ProjectCard key={p.id} p={p} />)}
        </div>
      </main>
    </div>
  );
}

function ProjectCard({ p }: { p: any }) {
  const o = useQuery<any>("project_overview", { project_id: p.id });
  const ep = o.data?.episodes.find((e: any) => e.label !== "待开工");
  const [cover, setCover] = useState<string | undefined>();
  useEffect(() => {
    if (!ep) return;
    q("episode_assets", { episode_id: ep.id }).then((a: any[]) => setCover(blob(a.find((x) => !x.grid)?.sha256)));
  }, [ep?.id]);
  return (
    <a className="projcard" href={`#/p/${p.id}`}>
      <div className="cover" style={{ backgroundImage: cover ? `linear-gradient(transparent 50%, rgba(0,0,0,.7)), url(${cover})` : undefined }}>
        {ep && <span className="tag" style={{ position: "absolute", left: 10, top: 10 }}>{ep.label}</span>}
        <div className="t">{p.name}</div>
      </div>
      <div style={{ marginTop: 8, fontWeight: 600 }}>{p.name}</div>
      <div className="muted small">{p.episodes} 集 · 设定 {o.data?.settings.total ?? 0} 条 · {ep ? `${ep.display_code} ${ep.label}` : "尚未开工"}</div>
    </a>
  );
}

function Popover({ kind, close, projects, projectId, setProjectId, project, topics, pickTopic }: any) {
  const configs = CONFIGS;
  const style = { left: 14, bottom: 60, width: 520 } as const;
  if (kind === "project") return (
    <div className="pop" style={style}>
      <h3>选择项目</h3>
      {projects.map((p: any) => <div key={p.id} className={`pill ${p.id === projectId ? "on" : ""}`} style={{ margin: 4, display: "inline-block" }} onClick={() => { setProjectId(p.id); close(); }}>{p.name}</div>)}
      <div className={`pill ${projectId === "__new" ? "on" : ""}`} style={{ margin: 4, display: "inline-block" }} onClick={() => { setProjectId("__new"); close(); }}>＋ 新项目（设定为空）</div>
      {configs.filter((c: any) => c.configId !== "_template").map((c: any) => <div key={c.configId} className={`pill ${projectId === "__new:" + c.configId ? "on" : ""}`} style={{ margin: 4, display: "inline-block" }} onClick={() => { setProjectId("__new:" + c.configId); close(); }}>＋ 新项目（{c.name} 配置）</div>)}
    </div>);
  if (kind === "style") return (
    <div className="pop" style={style}>
      <div className="spread"><h3>风格</h3><span className="muted tiny">本项目已验证的画面基准优先，色卡只作灵感</span></div>
      <div className="label">本项目已确认</div>
      <div className="row">{(project?.project?.settings ?? []).filter((s: any) => s.category === "space" || s.category === "rule").slice(0, 3).map((s: any) => (
        <div key={s.key} className="card line small" style={{ width: 150 }}>{s.content.slice(0, 40)}…</div>))}
        {!(project?.project?.settings ?? []).length && <span className="muted small">项目设定为空：首集确认后才有已验证风格。</span>}</div>
      <div className="label">灵感色卡（不等于已采用的图）</div>
      <div className="row">{["#2b3a55", "#c9a227", "#1f5f4a", "#e07a5f"].map((c) => <span key={c} style={{ width: 40, height: 40, borderRadius: 8, background: c }} />)}</div>
    </div>);
  if (kind === "topic") return (
    <div className="pop" style={style}>
      <h3>从选题池取卡</h3>
      {topics.length === 0 && <div className="muted small">选题池为空。可在项目的选题池里加入候选。</div>}
      {topics.map((t: any) => (
        <div key={t.id} className="spread card line" style={{ margin: "6px 0", cursor: "pointer" }} onClick={() => pickTopic(t)}>
          <span><b>{t.code}</b> {t.premise}</span><span className={`tag ${t.status === "已成稿" ? "green" : t.status === "候选" ? "" : "blue"}`}>{t.status}</span>
        </div>))}
    </div>);
  return (
    <div className="pop" style={style}>
      <h3>生成设置</h3>
      <div className="col small">
        <div className="spread"><span className="muted">作品线</span><b>{project?.project?.name ?? "新项目"}</b></div>
        <div className="spread"><span className="muted">双模型分工</span><span>故事 Claude 写 · Codex 审；提示词 Codex 写 · Claude 两遍独审</span></div>
        <div className="spread"><span className="muted">运行模式</span><span>手工（专家模式在开工页授权）</span></div>
        <div className="spread"><span className="muted">预估段数</span><span>{project?.project?.spec_preset?.targetDuration === "over_60" ? "60–120 秒 ≈ 5–9 段" : "按故事长度"}</span></div>
      </div>
    </div>);
}
