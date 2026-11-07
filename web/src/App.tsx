import { useCallback, useEffect, useState } from "react";
import {
  ApiError, Artifact, EpisodeView, Issue, Project, Setting, Task, Version,
  commandId, get, post, put, upload,
} from "./api";

type Route = { page: "home" } | { page: "project"; id: string } | { page: "episode"; id: string } | { page: "settings" };

function parse(): Route {
  const [, page, id] = location.hash.split("/");
  if (page === "project" && id) return { page, id };
  if (page === "episode" && id) return { page, id };
  if (page === "settings") return { page };
  return { page: "home" };
}
const go = (hash: string) => (location.hash = hash);

const STRENGTH = { required: "必守", variable: "可变" } as const;
const ORIGIN: Record<string, string> = { ai: "AI 稿", manual: "手稿", combined: "组合稿" };
const RUN_STATUS: Record<string, string> = {
  queued: "排队中", running: "运行中", succeeded: "完成", failed: "失败", waiting_quota: "等待额度", stopped: "已停",
};
const PURPOSE: Record<string, string> = { create: "起草", revise: "修改", review: "独立审查" };
const FIELDS: [string, string][] = [["synopsis", "故事梗概"], ["cast", "本集角色表"], ["handoff", "交接说明"], ["notes", "开发辅助"]];

function useError() {
  const [error, setError] = useState<string | null>(null);
  const guard = useCallback(
    (fn: () => Promise<unknown>) => async () => {
      setError(null);
      try { await fn(); } catch (e) { setError(e instanceof ApiError ? `${e.status}：${e.message}` : String(e)); }
    }, []);
  return { error, guard, setError };
}

export default function App() {
  const [route, setRoute] = useState(parse());
  useEffect(() => {
    const onHash = () => setRoute(parse());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  return (
    <div className="app">
      <header className="top">
        <a href="#/" className="brand">AI 导演工作台</a>
        <span className="muted">第 1 阶段 · 开工到独审</span>
        <a href="#/settings" className="right">执行器</a>
      </header>
      {route.page === "home" && <Home />}
      {route.page === "project" && <ProjectPage id={route.id} />}
      {route.page === "episode" && <EpisodePage id={route.id} />}
      {route.page === "settings" && <SettingsPage />}
    </div>
  );
}

function Home() {
  const [projects, setProjects] = useState<{ id: string; name: string; summary: string; episodes: number }[]>([]);
  const [configs, setConfigs] = useState<{ configId: string; name: string }[]>([]);
  const [config, setConfig] = useState("_template");
  const [name, setName] = useState("");
  const { error, guard } = useError();
  useEffect(() => { get<typeof projects>("/projects").then(setProjects); get<typeof configs>("/configs").then(setConfigs); }, []);
  const create = guard(async () => {
    const r = await post<{ projectId: string }>("/projects", { configId: config, name: name || undefined, commandId: commandId() });
    go(`#/project/${r.projectId}`);
  });
  return (
    <main className="page">
      <section className="card">
        <h2>新建项目</h2>
        <div className="row">
          <select value={config} onChange={(e) => setConfig(e.target.value)}>
            {configs.map((c) => <option key={c.configId} value={c.configId}>{c.name}</option>)}
          </select>
          <input placeholder="项目名（可留空用配置名）" value={name} onChange={(e) => setName(e.target.value)} />
          <button onClick={create}>创建</button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>
      <section className="card">
        <h2>最近项目</h2>
        {projects.length === 0 && <p className="muted">还没有项目。</p>}
        <ul className="list">
          {projects.map((p) => (
            <li key={p.id}><a href={`#/project/${p.id}`}>{p.name}</a> <span className="muted">{p.episodes} 集 · {p.summary}</span></li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function SettingsList({ settings }: { settings: Setting[] }) {
  if (!settings.length) return <p className="muted">项目设定为空。首集可独立成集，设定从之后的整理中产生。</p>;
  return (
    <ul className="settings">
      {settings.map((s) => (
        <li key={s.key}><span className={`tag ${s.strength}`}>{STRENGTH[s.strength]}</span> <code>{s.key}</code> {s.content}</li>
      ))}
    </ul>
  );
}

function ProjectPage({ id }: { id: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [code, setCode] = useState("");
  const [facts, setFacts] = useState("");
  const [purpose, setPurpose] = useState("story_source");
  const [target, setTarget] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const { error, guard } = useError();
  const load = useCallback(() => get<Project>(`/projects/${id}`).then(setProject), [id]);
  useEffect(() => { load(); }, [load]);
  if (!project) return <main className="page">读取中…</main>;
  const addEpisode = guard(async () => {
    await post(`/projects/${id}/episodes`, {
      displayCode: code, leftoverFacts: facts.split("\n").map((f) => f.trim()).filter(Boolean), commandId: commandId(),
    });
    setCode(""); setFacts(""); await load();
  });
  const doImport = guard(async () => {
    if (!file) throw new Error("请选择文件");
    const form = new FormData();
    form.append("file", file); form.append("purpose", purpose); form.append("commandId", commandId());
    if (target) form.append("episodeId", target);
    await upload(`/projects/${id}/imports`, form);
    setFile(null); await load();
  });
  return (
    <main className="page">
      <h1>{project.name}</h1>
      <p className="muted">{project.summary}</p>
      <div className="cols">
        <section className="card">
          <h2>集</h2>
          <ul className="list">
            {project.episodes.map((e) => (
              <li key={e.id}>
                <a href={`#/episode/${e.id}`}>{e.display_code} {e.title}</a>{" "}
                <span className="muted">{e.kickoff_id ? "制作中" : "待开工"}{e.leftover_facts.length ? ` · 遗留：${e.leftover_facts.join("；")}` : ""}</span>
              </li>
            ))}
          </ul>
          <h3>新增一集</h3>
          <input placeholder="集号，如 N07" value={code} onChange={(e) => setCode(e.target.value)} />
          <textarea placeholder="（可选）本集遗留事实，每行一条，例：厨房门上了锁" value={facts} onChange={(e) => setFacts(e.target.value)} />
          <button onClick={addEpisode} disabled={!code}>新增</button>
        </section>
        <section className="card">
          <h2>导入材料</h2>
          <p className="muted">故事稿、剧本只作故事来源，开工后先写故事稿并过独审。</p>
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <div className="row">
            <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
              <option value="story_source">故事稿（故事来源）</option>
              <option value="script_source">剧本（故事来源）</option>
              <option value="storyboard_ref">分镜（制作参考）</option>
              <option value="art_ref">绘图参考（本集素材）</option>
            </select>
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">暂不指定集</option>
              {project.episodes.map((e) => <option key={e.id} value={e.id}>{e.display_code}</option>)}
            </select>
            <button onClick={doImport}>导入</button>
          </div>
          <ul className="list">
            {project.imports.map((i) => (
              <li key={i.id}>{i.original_name} <span className="muted">{i.purpose} · {i.parse_status === "parsed" ? "已解析" : `解析失败：${i.diagnostic}`} · {i.sha256.slice(0, 10)}</span></li>
            ))}
          </ul>
        </section>
        <section className="card">
          <h2>项目设定</h2>
          <SettingsList settings={project.settings} />
          <h3>规格预设</h3>
          <pre>{JSON.stringify(project.spec_preset, null, 2)}</pre>
        </section>
      </div>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

type Preview = {
  settings: Setting[]; spec: Record<string, unknown>; carriedFacts: string[]; canContinue: boolean;
  predecessorsAvailable: { id: string; display_code: string; leftover_facts: string[] }[];
  imports: { id: string; original_name: string; purpose: string; parse_status: string }[];
};

function Kickoff({ episodeId, onDone }: { episodeId: string; onDone: () => void }) {
  const [sourceType, setSourceType] = useState<"import" | "idea">("import");
  const [importId, setImportId] = useState("");
  const [idea, setIdea] = useState("");
  const [goal, setGoal] = useState("");
  const [relation, setRelation] = useState("independent");
  const [pre, setPre] = useState("");
  const [creator, setCreator] = useState("codex");
  const [reviewer, setReviewer] = useState("codex");
  const [preview, setPreview] = useState<Preview | null>(null);
  const { error, guard } = useError();
  useEffect(() => {
    const qs = relation === "continue" && pre ? `?relation=continue&predecessors=${pre}` : "";
    get<Preview>(`/episodes/${episodeId}/kickoff-preview${qs}`).then((p) => {
      setPreview(p);
      if (!importId && p.imports.length) setImportId(p.imports[0].id);
      if (!p.imports.length) setSourceType("idea");
    });
  }, [episodeId, relation, pre]);
  if (!preview) return <p>读取开工信息…</p>;
  const start = guard(async () => {
    await post(`/episodes/${episodeId}/start`, {
      sourceType, sourceImportId: sourceType === "import" ? importId : undefined,
      ideaText: sourceType === "idea" ? idea : undefined, goal, relation,
      predecessorIds: relation === "continue" && pre ? [pre] : [],
      executorPlan: { create: { provider: creator }, review: { provider: reviewer } }, commandId: commandId(),
    });
    onDone();
  });
  return (
    <section className="card">
      <h2>本集开工</h2>
      <div className="cols">
        <div>
          <h3>本集来源</h3>
          <label><input type="radio" checked={sourceType === "import"} disabled={!preview.imports.length} onChange={() => setSourceType("import")} /> 导入材料</label>
          {sourceType === "import" && (
            <select value={importId} onChange={(e) => setImportId(e.target.value)}>
              {preview.imports.map((i) => <option key={i.id} value={i.id}>{i.original_name}（{i.purpose === "script_source" ? "剧本" : "故事稿"}）</option>)}
            </select>
          )}
          <label><input type="radio" checked={sourceType === "idea"} onChange={() => setSourceType("idea")} /> 一个想法</label>
          {sourceType === "idea" && <textarea value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="一只过分认真的柴犬第一次值夜班" />}
          <h3>本集要求</h3>
          <textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="可留空" />
          <h3>前集关系</h3>
          <label><input type="radio" checked={relation === "independent"} onChange={() => setRelation("independent")} /> 独立成集</label>
          <label><input type="radio" checked={relation === "continue"} disabled={!preview.canContinue} onChange={() => setRelation("continue")} /> 承接前集</label>
          {relation === "continue" && (
            <select value={pre} onChange={(e) => setPre(e.target.value)}>
              <option value="">选择前集</option>
              {preview.predecessorsAvailable.map((p) => <option key={p.id} value={p.id}>{p.display_code}</option>)}
            </select>
          )}
          {relation === "continue" && (
            <p>带入遗留：{preview.carriedFacts.length ? preview.carriedFacts.join("；") : <span className="muted">（无，可回项目页补录或改为独立）</span>}</p>
          )}
        </div>
        <div>
          <h3>项目设定</h3>
          <SettingsList settings={preview.settings} />
          <h3>规格（开工时复制到本集）</h3>
          <pre>{JSON.stringify(preview.spec, null, 2)}</pre>
          <h3>执行安排</h3>
          <div className="row">
            <label>创作 <select value={creator} onChange={(e) => setCreator(e.target.value)}><option>codex</option><option>claude</option></select></label>
            <label>独审 <select value={reviewer} onChange={(e) => setReviewer(e.target.value)}><option>codex</option><option>claude</option></select></label>
          </div>
          <p className="muted">独审每遍另开新会话、不续接，只读输入清单（会话隔离）。</p>
        </div>
      </div>
      <button className="primary" onClick={start}>确认开工</button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function IssueRow({ issue, version, onChange }: { issue: Issue; version: Version; onChange: () => void }) {
  const [reason, setReason] = useState("");
  const { error, guard } = useError();
  const waive = guard(async () => {
    await post(`/issues/${issue.id}/waivers`, { versionId: version.id, reason, commandId: commandId() });
    onChange();
  });
  return (
    <li className={issue.blocking ? "issue blocking" : "issue"}>
      <div><b>{issue.loc.anchor}</b> · {issue.category}{issue.setting_id && <> · 设定 <code>{issue.setting_id}</code></>}</div>
      {issue.setting_content && <div className="pair"><div>设定原文：{issue.setting_content}</div><div>冲突处：{issue.loc.evidence}</div></div>}
      <div>{issue.reason}</div>
      {issue.blocking ? (
        <div className="muted">必守冲突不能放行：改故事，或去项目设定修改设定本身。</div>
      ) : issue.waiver_reason ? (
        <div className="ok">已放行（仅本版本）：{issue.waiver_reason}</div>
      ) : (
        <div className="row"><input placeholder="放行理由" value={reason} onChange={(e) => setReason(e.target.value)} /><button onClick={waive}>写理由放行</button></div>
      )}
      {error && <p className="error">{error}</p>}
    </li>
  );
}

function VersionPanel({ art, version, onChange, task }: { art: Artifact; version: Version; task?: Task; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>(version.content);
  const { error, guard } = useError();
  useEffect(() => { setDraft(version.content); setEditing(false); }, [version.id]);
  const review = version.reviews[version.reviews.length - 1];
  const save = guard(async () => {
    await post(`/artifacts/${art.id}/versions`, { baseVersionId: version.id, content: draft, commandId: commandId() });
    setEditing(false); onChange();
  });
  const submit = guard(async () => { await post(`/versions/${version.id}/reviews`, { commandId: commandId(), taskId: task?.id }); onChange(); });
  const adopt = (andContinue: boolean) => guard(async () => {
    await post(`/artifacts/${art.id}/adoptions`, {
      versionId: version.id, expectedAdoptedVersionId: art.adopted_version_id, commandId: commandId(), andContinue,
    });
    onChange();
  });
  const g = { ...version.gate, adoptable: version.gate.adoptable && art.adopted_version_id !== version.id };
  const fields = art.stage === "story" ? FIELDS : [["scenes", "分场剧本"], ["notes", "说明"]] as [string, string][];
  return (
    <div>
      <div className="row">
        <h3>R{String(version.display_no).padStart(2, "0")} · {ORIGIN[version.origin]}</h3>
        {art.adopted_version_id === version.id && <span className="tag ok">已采用</span>}
        {version.base_id && <span className="muted">基于 R{String(art.versions.find((v) => v.id === version.base_id)?.display_no ?? "?").padStart(2, "0")}</span>}
      </div>
      {fields.map(([key, label]) => (
        <div key={key} className="field">
          <h4>{label}</h4>
          {editing ? <textarea value={draft[key] ?? ""} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
            : <div className="prose">{version.content[key] || <span className="muted">（空）</span>}</div>}
        </div>
      ))}
      <div className="row">
        {editing ? <><button onClick={save}>保存为新手稿</button><button onClick={() => setEditing(false)}>取消</button></>
          : <button onClick={() => setEditing(true)}>手改</button>}
        {art.stage === "story" && <button onClick={submit}>送审</button>}
        <button className="primary" disabled={!g.adoptable} onClick={adopt(false)}>采用</button>
        {art.stage === "story" && <button className="primary" disabled={!g.adoptable} onClick={adopt(true)}>采用并继续</button>}
      </div>
      {g.formatErrors.length > 0 && <p className="error">结构错误：{g.formatErrors.join("；")}</p>}
      {g.reviewRequired && !g.review && <p className="muted">必需独审未执行。</p>}
      {g.blocking.length > 0 && <p className="error">有必守设定冲突，不能采用。</p>}
      {g.unresolved.length > 0 && <p className="muted">还有 {g.unresolved.length} 条普通意见待处理。</p>}
      {review && (
        <div className="card inner">
          <h4>独立审查 第 {review.pass_no} 遍 · {review.reviewer} · 结论 {review.original_decision} · 隔离：{review.isolation_level === "session" ? "会话隔离" : review.isolation_level}</h4>
          <p className="muted">输入清单 {review.manifest_hash.slice(0, 16)}… · 会话 {review.session_id ?? "未报告"}</p>
          <details><summary>观察（{review.observations.length}）</summary>
            <ul>{review.observations.map((o, i) => <li key={i}><code>{o.objectId.slice(0, 12)}</code> {o.anchor}：{o.evidence}</li>)}</ul>
          </details>
          <ul className="issues">{review.issues.map((i) => <IssueRow key={i.id} issue={i} version={version} onChange={onChange} />)}</ul>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function ThreadPanel({ task, versionId, onChange }: { task: Task; versionId?: string; onChange: () => void }) {
  const [text, setText] = useState("");
  const [provider, setProvider] = useState("claude");
  const { error, guard } = useError();
  const send = guard(async () => {
    await post(`/tasks/${task.id}/messages`, { text, baseVersionId: versionId, commandId: commandId() });
    setText(""); onChange();
  });
  const retry = (runId: string, p?: string) => guard(async () => { await post(`/runs/${runId}/retry`, { provider: p, commandId: commandId() }); onChange(); });
  return (
    <aside className="thread">
      <h3>任务：{task.goal}</h3>
      <p className="muted">状态 {task.state} · 自动修订 {task.used_rounds}/{task.max_rounds} 轮</p>
      <ol className="messages">
        {task.messages.map((m) => <li key={m.id} className={m.role}><b>{m.role === "user" ? "导演" : m.role === "assistant" ? "助手" : "系统"}</b> {m.text}</li>)}
      </ol>
      <h4>运行</h4>
      <ul className="runs">
        {task.runs.map((r) => (
          <li key={r.id}>
            {PURPOSE[r.purpose]}{r.counted_round ? "（计轮）" : ""} · {r.provider} · {RUN_STATUS[r.status] ?? r.status}
            {r.isolation_level === "session" && " · 会话隔离"} <span className="muted">指纹 {r.input_hash.slice(0, 8)}</span>
            {r.status === "waiting_quota" && (
              <div className="quota">
                额度用尽{r.quota_resets_at ? `，预计 ${r.quota_resets_at} 恢复` : ""}。不会自动切换。
                <div className="row">
                  <button onClick={retry(r.id)}>等待后重试</button>
                  <select value={provider} onChange={(e) => setProvider(e.target.value)}><option>claude</option><option>codex</option></select>
                  <button onClick={retry(r.id, provider)}>换执行器重试</button>
                </div>
              </div>
            )}
            {r.status === "failed" && <div className="error">{r.reason}：{r.diagnostic?.slice(0, 200)} <button onClick={retry(r.id)}>重试</button></div>}
          </li>
        ))}
      </ul>
      {task.state !== "closed" && (
        <>
          <textarea placeholder="对当前版本提要求，例：只改结尾" value={text} onChange={(e) => setText(e.target.value)} />
          <button onClick={send} disabled={!text.trim()}>发送</button>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </aside>
  );
}

function ArtifactSection({ art, episodeId, onChange }: { art: Artifact; episodeId: string; onChange: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [goal, setGoal] = useState("");
  const { error, guard } = useError();
  const latest = art.versions[art.versions.length - 1];
  const version = art.versions.find((v) => v.id === selected) ?? latest;
  const task = art.tasks[art.tasks.length - 1];
  const newTask = guard(async () => {
    await post(`/episodes/${episodeId}/tasks`, { stage: art.stage, goal: goal || (art.stage === "story" ? "写本集故事稿" : "按采用故事写分场剧本"), commandId: commandId() });
    setGoal(""); onChange();
  });
  return (
    <section className="card">
      <h2>{art.stage === "story" ? "故事" : "分场剧本"}</h2>
      <div className="workspace">
        <div className="main">
          <div className="row tabs">
            {art.versions.map((v) => (
              <button key={v.id} className={v.id === version?.id ? "tab active" : "tab"} onClick={() => setSelected(v.id)}>
                R{String(v.display_no).padStart(2, "0")}{art.adopted_version_id === v.id ? " ✓" : ""}
              </button>
            ))}
          </div>
          {version ? <VersionPanel art={art} version={version} task={task} onChange={onChange} /> : <p className="muted">尚无稿件。</p>}
          <div className="row">
            <input placeholder={art.stage === "story" ? "新任务要求（如：从来源起草）" : "剧本任务要求"} value={goal} onChange={(e) => setGoal(e.target.value)} />
            <button onClick={newTask}>{art.versions.length ? "新建任务" : "起草"}</button>
          </div>
          {error && <p className="error">{error}</p>}
        </div>
        {task && <ThreadPanel task={task} versionId={version?.id} onChange={onChange} />}
      </div>
    </section>
  );
}

function EpisodePage({ id }: { id: string }) {
  const [view, setView] = useState<EpisodeView | null>(null);
  const load = useCallback(() => get<EpisodeView>(`/episodes/${id}`).then(setView), [id]);
  useEffect(() => { load(); }, [load]);
  const busy = view?.artifacts.some((a) => a.tasks.some((t) => t.runs.some((r) => r.status === "queued" || r.status === "running")));
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [busy, load]);
  if (!view) return <main className="page">读取中…</main>;
  const k = view.kickoff;
  return (
    <main className="page">
      <p><a href={`#/project/${view.episode.project_id}`}>← 项目</a></p>
      <h1>{view.episode.display_code} {view.episode.title}</h1>
      {!k && <Kickoff episodeId={id} onDone={load} />}
      {k && (
        <section className="card">
          <h2>开工记录</h2>
          <p>来源：{k.source_type === "import" ? `导入 ${view.sourceImport?.original_name}` : `想法「${k.idea_text}」`} · 前集：{k.relation === "continue" ? `承接，遗留 ${k.carried_facts.join("；") || "无"}` : "独立成集"} · 设定修订 {k.settings_revision} · 创作 {k.executor_plan.create.provider} / 独审 {k.executor_plan.review.provider}</p>
          {view.sourceImport && <details><summary>查看导入原文（{view.sourceImport.sha256.slice(0, 10)}）</summary><pre>{view.sourceImport.parsed_text}</pre></details>}
        </section>
      )}
      {k && view.artifacts.map((a) => <ArtifactSection key={a.id} art={a} episodeId={id} onChange={load} />)}
    </main>
  );
}

function SettingsPage() {
  const [probe, setProbe] = useState<Record<string, Record<string, unknown>> | null>(null);
  const [path, setPath] = useState("");
  const { error, guard } = useError();
  const load = () => get<typeof probe>("/executors").then(setProbe);
  useEffect(() => { load(); }, []);
  const save = guard(async () => { await put("/settings/claude_path", { path: path || null }); await load(); });
  return (
    <main className="page">
      <section className="card">
        <h2>执行器探测</h2>
        {!probe ? <p>探测中…</p> : Object.entries(probe).map(([name, info]) => (
          <div key={name}><h3>{name}</h3><pre>{JSON.stringify(info, null, 2)}</pre></div>
        ))}
        <h3>Claude Code 可执行文件路径</h3>
        <p className="muted">留空时按 PATH、~/.local/bin、桌面应用托管目录（按版本号）查找。</p>
        <div className="row"><input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/claude" /><button onClick={save}>保存</button></div>
        {error && <p className="error">{error}</p>}
      </section>
    </main>
  );
}
