import { ProjectPresets } from "./ProjectPresets";
import { useProjectRoute } from "./projectNavigation";
import {specLabels,specChoices} from "./specs";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { blob, cmd, q } from "./api";
import { ErrorLine, Modal, Pills, SegBar, Tag, fmtMs, useAction, useQuery } from "./ui";

export default function Project({ id }: { id: string }) {
  const { hash, active } = useProjectRoute();
  const o = useQuery<any>("project_overview", { project_id: id }, [active]);
  const [kick, setKick] = useState<{ episodeId?: string; displayCode?:string; leftoverFacts?:string[]; idea?: string; importId?: string; topicId?: string } | null>(null);
  const [adding, setAdding] = useState(false);
  const act = useAction();
  useEffect(() => {
    if (!active) return;
    const scoped = "kickoff-prefill:"+id;
    const key = sessionStorage.getItem(scoped) ? scoped : "kickoff-prefill";
    const pre = sessionStorage.getItem(key);
    if (pre) {
      let input;
      try { input = JSON.parse(pre); } catch { /* ignore invalid session data */ }
      if (input?.projectId === id) { sessionStorage.removeItem(key); setKick(input); }
    }
  }, [id, active, hash]);
  useEffect(() => {
    const k = new URLSearchParams(hash.split("?")[1] ?? "").get("kick");
    if (k) setKick({ episodeId: k });
  }, [hash]);
  if (!o.data) return <div className="page">{o.error ?? "读取中…"}</div>;
  const { project, episodes, settings, todos } = o.data;
  const activeEpisode=[...episodes].filter(e=>e.kickoff_id && e.state!=="delivered").sort((a,b)=>b.created_at-a.created_at)[0];
  const inProduction = episodes.filter((e: any) => e.label !== "待开工" && !e.label.startsWith("图文已交付")).length;
  const nextCode = () => {
    const nums = episodes.map((e: any) => parseInt(e.display_code.replace(/\D/g, ""), 10)).filter((n: number) => !isNaN(n));
    const prefix = episodes[0]?.display_code.replace(/\d+/g, "") || "E";
    return `${prefix}${String((nums.length ? Math.max(...nums) : 0) + 1).padStart(2, "0")}`;
  };
  const addEpisode = (code:string,facts:string) => setKick({displayCode:code || nextCode(),leftoverFacts:facts.split("\n").map(x=>x.trim()).filter(Boolean)});
  return (
    <div>
      <div className="spread" style={{ padding: "12px 24px" }}>
        <b>{project.name}</b>
        <div className="row">
          <a className="chip" href="#/sys/executors">⚒ 技能与分工</a>
          <a className="chip" href={`#/p/${id}/preset`}>规格预设</a>
          <span className="chip"><span className="dot" />Claude Code</span><span className="chip"><span className="dot" />Codex</span>
        </div>
      </div>
      <div className="page" style={{ paddingTop: 8 }}>
        <div className="spread">
          <div><h1 style={{ fontSize: "2rem" }}>{project.name}</h1>
            <div className="muted small" style={{ marginTop: 6 }}>{episodes.length} 集 · 各集规格可以不同 · 进行中 {inProduction} 集</div></div>
          <div className="row">
            <button className="btn" onClick={() => setAdding(true)} disabled={act.busy}>＋ 新增一集</button>
            <a className="btn" href={`#/p/${id}/groups`}>≡ 连续故事</a>
          </div>
        </div>
        {adding && <AddEpisode code={nextCode()} onClose={() => setAdding(false)} onAdd={(c: string, f: string, kick: boolean) => {
          setAdding(false);
          if (kick) addEpisode(c, f);
          else act.run(async () => { await cmd("create_episode", { project_id: id, display_code: c, leftover_facts: f.split("\n").map((x) => x.trim()).filter(Boolean) }); o.reload(); });
        }} />}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, margin: "18px 0" }}>
          {todos.slice(0, 2).map((t: any, i: number) => (
            <div key={i} className={`card ${t.kind === "closure" ? "cyan" : "amber"}`}>
              <div className="row"><Tag tone={t.kind === "closure" ? "cyan" : "amber"}>{t.kind === "closure" ? "下一步" : "缺口"}</Tag><b>{t.title}</b></div>
              <div className="muted small" style={{ margin: "8px 0" }}>{t.kind === "closure" ? "可逐条查看出处，写进设定或只留台账。" : "只阻塞依赖它的段；其他段可以继续。"}</div>
              <a className="btn primary" href={t.kind === "closure" ? `#/p/${id}/closure/${t.objectId}` : t.kind==="reconcile" ? `#/e/${t.episodeId}/assets?batch=${t.objectId}` : `#/e/${t.episodeId}/space`}>{t.action} ›</a>
            </div>))}
          {todos.length === 0 && <div className="card"><b>没有待办</b><div className="muted small" style={{ marginTop: 6 }}>可以新增一集，或查看已交付的集。</div></div>}
          <div className="card">
            <div className="spread"><b>设定 · {settings.total} 条</b><a className="btn" href={`#/p/${id}/settings`}>查看设定 ›</a></div>
            <div className="row" style={{ margin: "8px 0" }}><Tag tone="red">必须守住 {settings.required}</Tag><Tag tone="blue">可变着用 {settings.variable}</Tag></div>
            <div className="muted tiny">各集只读引用；改动只对之后的集生效</div>
          </div>
        </div>
        <div className="grid2">
          {episodes.map((e: any) => <EpisodeCard key={e.id} e={e} projectId={id} onKick={() => setKick({ episodeId: e.id })} reload={o.reload} />)}
        </div>
        <div className="row" style={{ marginTop: 20 }}>
          {[["free", "自由对话"], ["library", "复用资产库"], ["knowledge", "知识库"], ["topics", "选题池"], ["baseline", "制作基准"], ["expert", "专家模式授权"]].map(([k, l]) => (
            <a key={k} className="btn ghost" href={`#/p/${id}/${k}`}>{l}</a>))}
        </div>
        <ErrorLine error={act.error} />
      </div>
      {kick && <Kickoff projectId={id} prefill={kick} onClose={() => { setKick(null); o.reload(); }} />}
      {activeEpisode && <a className="chip" style={{position:"fixed",right:24,bottom:24}} href={`#/e/${activeEpisode.id}/story`}>导演助手 · {activeEpisode.display_code}</a>}
    </div>
  );
}

function AddEpisode({ code, onClose, onAdd }: any) {
  const [c, setC] = useState(code);
  const [f, setF] = useState("");
  return (
    <Modal onClose={onClose} width={460}>
      <h2>新增一集</h2>
      <p className="muted small">新集读取已确认设定；开工时再选独立成集或承接前集。</p>
      <div className="formrow"><span className="muted">集号</span><input value={c} onChange={(e) => setC(e.target.value)} /></div>
      <div className="formrow"><span className="muted">本集遗留事实</span><textarea placeholder="（可选）每行一条，例：厨房门上了锁" value={f} onChange={(e) => setF(e.target.value)} /></div>
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={onClose}>取消</button>
        <button className="btn" onClick={() => onAdd(c, f, false)}>只新增</button>
        <button className="btn primary" onClick={() => onAdd(c, f, true)}>新增并去开工</button></div>
    </Modal>
  );
}

function EpisodeCard({ e, projectId, onKick, reload }: { e: any; projectId: string; onKick: () => void; reload: () => void }) {
  const [editing, setEditing] = useState(false);
  const [facts, setFacts] = useState((e.leftover_facts ?? []).join("\n"));
  const act = useAction();
  const [cover, setCover] = useState<string>();
  useEffect(() => { if (e.segments) q("episode_assets", { episode_id: e.id }).then((a: any[]) => setCover(blob(a.find((x) => !x.grid)?.sha256))); }, [e.id, e.segments]);
  const delivered = e.deliveries.some((d: any) => d.scope === "full");
  return (
    <div className="epcard">
      <div className="thumb" style={{ backgroundImage: cover ? `url(${cover})` : undefined, display: "grid", placeItems: "center", color: "#3a3b42", fontSize: "2.31rem" }}>{cover ? "" : "☺"}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Tag tone={e.tone}>{e.label}</Tag>
        <div style={{ fontWeight: 700, margin: "6px 0 4px" }}>{e.display_code} · {e.title || "未命名"}</div>
        <div className="muted small">{e.segments ? `${e.segments} 段 · ${fmtMs(e.totalMs)}` : e.kickoff_id ? "故事阶段" : "尚未开工"} · <span style={{ cursor: "pointer", textDecoration: "underline dotted" }} onClick={() => setEditing(!editing)}>遗留：{e.leftover_facts?.length ? e.leftover_facts.join("；") : "补录"}</span></div>
        {editing && <div className="row" style={{ marginTop: 6 }}><textarea style={{ minHeight: 40, flex: 1 }} value={facts} onChange={(x) => setFacts(x.target.value)} placeholder="每行一条遗留事实" />
          <button className="btn" onClick={() => act.run(async () => { await cmd("record_leftovers", { episode_id: e.id, facts: facts.split("\n").map((x: string) => x.trim()).filter(Boolean), expected_revision: e.revision }); setEditing(false); reload(); })}>保存遗留</button></div>}
        <ErrorLine error={act.error} />
        <div style={{ margin: "10px 0" }}><SegBar total={11} done={e.progress} now={!delivered && !!e.kickoff_id} /></div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          {!e.kickoff_id && <button className="btn primary" onClick={onKick}>开工</button>}
          {e.kickoff_id && !delivered && <a className="btn" href={`#/e/${e.id}/story`}>我有故事稿</a>}
          {e.kickoff_id && !delivered && <a className="btn primary" href={`#/e/${e.id}/${e.adopted.story ? (e.adopted.plan ? "assets" : "script") : "story"}`}>{e.adopted.story ? "开始制作" : "继续"}</a>}
          {delivered && <a className="btn" href={`#/e/${e.id}/delivery`}>图文页</a>}
          {delivered && e.closure && e.closure.state !== "confirmed" && <a className="btn primary" href={`#/p/${projectId}/closure/${e.closure.id}`}>收尾</a>}
          {delivered && !e.closure && <a className="btn primary" href={`#/e/${e.id}/delivery`}>交付页</a>}
        </div>
      </div>
    </div>
  );
}

export function Kickoff({ projectId, prefill, onClose }: { projectId: string; prefill: any; onClose: () => void }) {
  const { go } = useProjectRoute();
  const [source, setSource] = useState<"idea" | "topic" | "import">(prefill.importId ? "import" : prefill.topicId ? "topic" : "idea");
  const [idea, setIdea] = useState(prefill.idea ?? "");
  const [goal,setGoal] = useState(prefill.goal ?? "");
  const [relation, setRelation] = useState(prefill.predecessorId ? "continue" : "independent");
  const [pre, setPre] = useState(prefill.predecessorId ?? "");
  const [importId, setImportId] = useState(prefill.importId ?? "");
  const [topicId, setTopicId] = useState(prefill.topicId ?? "");
  const [mode, setMode] = useState<"manual" | "expert">(prefill.mode ?? "manual");
  const [split, setSplit] = useState<"default" | "custom">(prefill.creator && (prefill.creator !== "claude" || prefill.reviewer !== "codex") ? "custom" : "default");
  const [creator, setCreator] = useState(prefill.creator ?? "claude");
  const [reviewer, setReviewer] = useState(prefill.reviewer ?? "codex");
  const [cm, setCm] = useState<{ model: string | null; effort: string | null }>({ model: null, effort: null });
  const [rm, setRm] = useState<{ model: string | null; effort: string | null }>({ model: null, effort: null });
  const [managePresets,setManagePresets]=useState(false);
  const [ratioOverride,setRatioOverride]=useState(prefill.ratio || "");
  const [presetName,setPresetName]=useState("default");
  const preview = useQuery<any>("kickoff_preview", { episode_id: prefill.episodeId ?? null, project_id:projectId, display_code:prefill.displayCode, relation: relation === "continue" && pre ? "continue" : "independent", predecessor_ids: relation === "continue" && pre ? [pre] : [] });
  const topics = useQuery<any[]>("topics", { project_id: projectId });
  const catalog = useQuery<any>("model_catalog");
  const act = useAction();
  useEffect(() => { if (preview.data && !importId && source === "import") setImportId(preview.data.imports.find((i:any) => i.parse_status === "parsed")?.id || ""); }, [preview.data, importId, source]);
  useEffect(() => { if (preview.data?.imports.some((i:any)=>i.parse_status === "parsed") && !prefill.idea && !prefill.topicId) setSource("import"); }, [preview.data?.imports.length]);
  if (!preview.data) return null;
  const p = preview.data;
  const chosen=p.presets.find((item:any)=>item.name===presetName) ?? p.presets[0];
  const effectiveSpec={...chosen.values,...(prefill.style?{style:prefill.style}:{}),...(ratioOverride?{aspectRatio:ratioOverride}:{})};
  const validRatio=Object.keys(specChoices.aspectRatio).includes(effectiveSpec.aspectRatio);
  const duration=Number(effectiveSpec.segmentMaxSeconds ?? 15);
  const validDuration=Number.isInteger(duration)&&duration>=5&&duration<=15;
  const required = p.settings.filter((s: any) => s.strength === "required").length;
  const start = () => act.run(async () => {
    const started=await cmd("start_episode", {
      episode_id: prefill.episodeId ?? null, project_id:projectId, display_code:prefill.displayCode, leftover_facts:prefill.leftoverFacts, source_type: source, executor_plan: { create: { provider: creator, ...cm }, review: { provider: reviewer, ...rm } },
      preset_name:chosen.name,preset_revision:chosen.rev,
      spec_overrides: { ...(prefill.style ? { style: prefill.style } : {}), ...(ratioOverride ? {aspectRatio:ratioOverride} : {}) },
      goal: source === "idea" ? "" : goal,
      source_import_id: source === "import" ? importId : undefined, idea_text: source === "idea" ? idea : undefined,
      topic_id: source === "topic" ? topicId : undefined, relation: relation === "continue" && pre ? "continue" : "independent",
      predecessor_ids: relation === "continue" && pre ? [pre] : [],
    });
    if (mode === "expert") { go(`#/p/${projectId}/expert?episode=${started.episodeId}`); return; }
    go(`#/e/${started.episodeId}/story`);
  });
  const lastPre = p.predecessorsAvailable[p.predecessorsAvailable.length - 1];
  return (
    <Modal onClose={onClose}>
      <div className="spread"><h2 style={{ fontSize: "1.38rem" }}>开工 · {p.episode.display_code}</h2>{lastPre && <Tag tone="cyan">按上一集预填</Tag>}</div>
      <p className="muted small">每一集开工时决定这一集怎么做。所有集走同一条流水线，这里只定本集的做法。</p>
      <div className="formrow"><span className="muted">故事来源</span><div className="col">
        <Pills value={source} onChange={setSource} options={[["idea", "新想法"], ["topic", "选题池"], ["import", "导入故事稿 / 剧本"]]} />
        {source === "idea" && <textarea value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="一句话想法" />}
        {source === "topic" && <select value={topicId} onChange={(e) => setTopicId(e.target.value)}><option value="">选择选题卡</option>
          {(topics.data ?? []).filter((t) => t.status === "候选" && !t.episode_id).map((t) => <option key={t.id} value={t.id}>{t.code} · {t.premise}</option>)}</select>}
        {source === "import" && (p.imports.length ? <select value={importId} onChange={(e) => setImportId(e.target.value)}>
          {p.imports.map((i: any) => <option key={i.id} value={i.id} disabled={i.parse_status !== "parsed"}>{i.original_name}{i.parse_status !== "parsed" ? "（解析失败）" : ""}（{i.purpose === "script_source" ? "剧本" : "故事稿"}，作为故事来源）</option>)}</select>
          : <span className="muted small">本集没有导入材料。可在首页「导入故事稿 / 剧本」归位后再开工。</span>)}
      </div></div>
      {source !== "idea" && <label className="formrow"><span className="muted">本集补充要求</span><textarea value={goal} onChange={e=>setGoal(e.target.value)}/></label>}
      <div className="formrow"><span className="muted">和前面各集的关系</span><div className="col">
        <div className="row">
          <button className={`pill ${relation === "independent" ? "on" : ""}`} onClick={() => setRelation("independent")}>独立成集</button>
          {p.predecessorsAvailable.filter((e: any,i: number) => i >= p.predecessorsAvailable.length - 3 || e.id === pre).map((e: any) => <button key={e.id} className={`pill ${relation === "continue" && pre === e.id ? "on" : ""}`} onClick={() => { setRelation("continue"); setPre(e.id); }}>承接 {e.display_code}</button>)}
          {p.canContinue && <a className="pill" href={`#/p/${projectId}/groups`}>与几集组成连续故事</a>}
        </div>
        {!p.canContinue && <span className="muted small">项目里还没有其他集，第 1 集只能独立成集。</span>}
        {relation === "continue" && pre && <span className="muted small">已带入 {p.predecessorsAvailable.find((e: any) => e.id === pre)?.display_code} 的遗留：{p.carriedFacts.join("；") || "无（可回项目页补录或改为独立）"}</span>}
      </div></div>
      <div className="formrow"><span className="muted">规格预设</span><div className="col">
        <div className="row"><select aria-label="本集使用的规格预设" value={chosen.name} onChange={e=>setPresetName(e.target.value)}>
          {p.presets.map((item:any)=><option value={item.name} key={item.name}>{item.name==="default"?"项目默认":item.name} · {item.values.aspectRatio}</option>)}</select>
          <select aria-label="本集画幅覆盖" value={ratioOverride} onChange={e=>setRatioOverride(e.target.value)}><option value="">沿用预设画幅</option>{Object.keys(specChoices.aspectRatio).map(r=><option key={r} value={r}>{r}</option>)}</select>
          <button className="pill" onClick={()=>setManagePresets(true)}>管理预设</button></div>
        <span className="muted small">{prefill.style ? "本集风格：" + prefill.style : "沿用项目风格"}</span></div></div>
      <div className="formrow"><span className="muted">运行模式</span><Pills value={mode} onChange={setMode} options={[["manual", "手工"], ["expert", "专家"]]} /></div>
      <div className="formrow"><span className="muted">模型分工</span><div className="col">
        <Pills value={split} onChange={v => { setSplit(v); if (v === "default") {setCreator("claude");setReviewer("codex");setCm({model:null,effort:null});setRm({model:null,effort:null});} }} options={[["default", "默认"], ["custom", "本集调整"]]} />
        {split === "custom" ? (
          <div className="row">
            <ModelPick label="故事 写" provider={creator} setProvider={(p: string) => { setCreator(p); setCm({ model: null, effort: null }); }} sel={cm} setSel={setCm} catalog={catalog.data} />
            <ModelPick label="审" provider={reviewer} setProvider={(p: string) => { setReviewer(p); setRm({ model: null, effort: null }); }} sel={rm} setSel={setRm} catalog={catalog.data} />
          </div>
        ) : <span className="muted small">默认：故事 Claude Code 写 · Codex 审；提示词 Codex 写 · Claude 两遍独审</span>}
        {reviewer === creator && <span className="muted small">单模型时必需审核仍另开会话，不取消。</span>}
      </div></div>
      <div className="card" style={{ margin: "14px 0" }}>
        <div className="small" style={{ marginBottom: 6 }}>本集会读取（只读）</div>
        <ul className="small" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
          <li>设定 {p.settings.length} 条：{p.settings.length ? `必须守住 ${required} 条会进入故事独审的检查项` : "设定为空，首集可独立成集"}</li>
          {relation === "continue" && pre && <li>前集遗留：{p.carriedFacts.join("；") || "无"}</li>}
          <li>规格：{Object.entries(effectiveSpec).map(([k,v])=>(specLabels[k] ?? k)+" "+(specChoices[k]?.[String(v)] ?? String(v))).join(" · ")}</li>
        </ul>
      </div>
      <div className="card"><Tag tone={validRatio && validDuration?"green":"red"}>{validRatio && validDuration?"画幅与段长符合 H3 规格":"请修正规格"}</Tag><p className="small">{!validRatio && "画幅不受支持。"}{!validDuration && "每段上限须为 5–15 之间的整数秒。"}本集必守设定在故事独审时逐条核对。</p></div>
      {managePresets&&<Modal width={1040} onClose={()=>{setManagePresets(false);preview.reload();}}>
        <button className="btn" onClick={()=>{setManagePresets(false);preview.reload();}}>返回开工设置</button>
        <ProjectPresets pid={projectId}/></Modal>}
      <ErrorLine error={act.error} />{act.error&&<button className="btn" onClick={()=>preview.reload()}>刷新开工预览</button>}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn primary" disabled={act.busy || !validRatio || !validDuration || (source === "idea" ? !idea.trim() : source === "topic" ? !topicId : !importId)} onClick={start}>开工，进入故事稿</button>
      </div>
    </Modal>
  );
}

function ModelPick({ label, provider, setProvider, sel, setSel, catalog }: any) {
  const models = catalog?.text?.[provider]?.models ?? [];
  const m = models.find((x: any) => x.id === sel.model) ?? models[0];
  const [warn, setWarn] = useState("");
  const pickEffort = async (effort: string | null) => {
    try { await q("validate_selection", { provider, model: sel.model, effort }); setWarn(""); setSel({ ...sel, effort }); }
    catch (e: any) { setWarn(e.message); setSel({ ...sel, effort: null }); }
  };
  return (
    <div className="col" style={{ gap: 4 }}>
      <div className="row small">{label}
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>{Object.keys(catalog?.text ?? { codex: 1, claude: 1, antigravity: 1 }).map((k) => <option key={k}>{k}</option>)}</select>
        <select value={sel.model ?? ""} onChange={(e) => setSel({ model: e.target.value || null, effort: null })}>{models.map((x: any) => <option key={x.label} value={x.id ?? ""}>{x.label}{x.status !== "verified" ? "（待检测）" : ""}</option>)}</select>
        <select value={sel.effort ?? ""} onChange={(e) => pickEffort(e.target.value || null)}>{["", "low", "medium", "high", "xhigh", "max"].map((x) => <option key={x} value={x}>{x || "默认强度"}{x && !(m?.efforts ?? []).includes(x) ? "（不兼容）" : ""}</option>)}</select>
      </div>
      {warn && <span className="error tiny">{warn}</span>}
    </div>
  );
}
