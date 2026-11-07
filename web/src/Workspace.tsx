/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useState } from "react";
import { blob, cmd, deliveryFile, q, upload } from "./api";
import Assistant, { Selection, artifactFor } from "./Assistant";
import { ErrorLine, Modal, PROVIDER_LABEL, Pills, STAGES, Tag, fmtMs, go, rno, useAction, usePoll, useQuery } from "./ui";

type Ctx = {
  view: any; eid: string; reload: () => void; sel: Selection; setSel: (s: Selection) => void; assets: any[];
  batches: any[]; focus: string | null; setFocus: (v: string | null) => void; openBatch: (b: any) => void;
};

function layout(plan: any) {
  if (!plan) return { segments: [], shots: {}, total: 0 };
  const segs = [...plan.segments].sort((a: any, b: any) => a.order - b.order);
  let clock = 0;
  const shots: Record<string, any> = {};
  const out = segs.map((s: any) => {
    let local = 0;
    const own = plan.shots.filter((x: any) => x.segment === s.id).sort((a: any, b: any) => a.order - b.order);
    for (const sh of own) { shots[sh.id] = { start: clock + local, end: clock + local + sh.duration_ms }; local += sh.duration_ms; }
    const seg = { ...s, start: clock, end: clock + local, duration: local, shots: own.map((x: any) => x.id) };
    clock += local;
    return seg;
  });
  return { segments: out, shots, total: clock };
}

export default function Workspace({ id, stage }: { id: string; stage: string }) {
  const view = useQuery<any>("episode_view", { episode_id: id });
  const assets = useQuery<any[]>("episode_assets", { episode_id: id });
  const batches = useQuery<any[]>("episode_batches", { episode_id: id });
  const [sel, setSel] = useState<Selection>(null);
  const [batch, setBatch] = useState<any>(null);
  const batchParam = new URLSearchParams(location.hash.split("?")[1] ?? "").get("batch");
  useEffect(() => { if (batchParam) setBatch({ id: batchParam }); }, [batchParam]);
  const focusParam = new URLSearchParams(location.hash.split("?")[1] ?? "").get("v");
  const [focus, setFocus] = useState<string | null>(focusParam);
  useEffect(() => setFocus(focusParam), [focusParam]);
  const busy = view.data?.artifacts.some((a: any) => a.tasks.some((t: any) => t.runs.some((r: any) => ["queued", "running"].includes(r.status))));
  const reload = () => { view.reload(); assets.reload(); batches.reload(); };
  usePoll(!!busy, reload);
  if (!view.data) return <div className="page">{view.error ?? "读取中…"}</div>;
  const v = view.data;
  if (!v.kickoff) return <div className="page">本集尚未开工。<a className="btn primary" href={`#/p/${v.episode.project_id}`}>去开工</a></div>;
  const ctx: Ctx = { view: v, eid: id, reload, sel, setSel, assets: assets.data ?? [], batches: batches.data ?? [], focus, setFocus,
    openBatch: setBatch };
  const adopted = (s: string) => !!v.artifacts.find((a: any) => a.stage === s)?.adopted_version_id;
  const status = (s: string) => {
    const map: Record<string, boolean> = { story: adopted("story"), script: adopted("script"), plan: adopted("plan"),
      space: adopted("plan"), cast: adopted("plan"), assets: adopted("prompts"), prompts: adopted("prompts"),
      review: adopted("prompts"), publish: adopted("publish"), delivery: v.episode.state === "delivered", package: false };
    return map[s] ? "done" : s === stage ? "now" : "";
  };
  return (
    <div className="app">
      <nav className="rail">
        <a href="#/"><div className="logo">导</div></a>
        <a className="on" href={`#/e/${id}/${stage}`} title="目录">▤</a>
        <a href={`#/p/${v.episode.project_id}`} title="项目主页">▶</a>
        <a href="#/sys/tasks" title="任务记录">▣</a>
      </nav>
      <div className="ws" style={{ flex: 1 }}>
        <div className="catalog">
          <div className="spread" style={{ padding: "0 8px 12px" }}><b>目录</b><a className="muted" href={`#/p/${v.episode.project_id}`}>⇤</a></div>
          {STAGES.map(([k, label], i) => (
            <a key={k} className={`item ${k === stage ? "on" : ""}`} href={`#/e/${id}/${k}`}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>{label}<span className={`st ${status(k)}`} />
            </a>))}
        </div>
        <div className="center">
          <div className="topbar">
            <span className="title">{v.episode.display_code} · {v.episode.title || "未命名"}</span>
            <span className="muted small">{v.kickoff.spec.aspectRatio === "9:16" ? "竖屏短片" : "横屏"}</span>
            <span style={{ marginLeft: "auto" }} className="chip">◐ {v.kickoff.relation === "continue" ? "承接前集" : "独立成集"}</span>
            <span className="chip">▯ {v.kickoff.spec.aspectRatio}</span>
            <span className="chip">⇄ 手工</span>
          </div>
          <StageView stage={stage} ctx={ctx} />
        </div>
        <Assistant view={v} stage={stage} sel={sel} setSel={setSel} reload={reload} focus={focus} batches={ctx.batches} onOpenBatch={setBatch} />
      </div>
      {batch && <BatchModal batch={batch} assets={ctx.assets} close={() => { setBatch(null); reload(); }} />}
    </div>
  );
}

function StageView({ stage, ctx }: { stage: string; ctx: Ctx }) {
  switch (stage) {
    case "story": return <StoryView ctx={ctx} />;
    case "script": return <ScriptView ctx={ctx} />;
    case "plan": return <PlanView ctx={ctx} />;
    case "space": return <SpaceView ctx={ctx} />;
    case "cast": return <CastView ctx={ctx} />;
    case "assets": return <AssetsView ctx={ctx} />;
    case "prompts": return <PromptsView ctx={ctx} />;
    case "review": return <ReviewView ctx={ctx} />;
    case "publish": return <PublishView ctx={ctx} />;
    case "delivery": return <DeliveryView ctx={ctx} />;
    default: return <PackageView ctx={ctx} />;
  }
}

// ---------------------------------------------------------------- common pieces
function useVersion(ctx: Ctx, stage: string) {
  const art = artifactFor(ctx.view, stage);
  const versions = art?.versions ?? [];
  const current = versions.find((v: any) => v.id === ctx.focus) ?? versions.find((v: any) => v.id === art?.adopted_version_id) ?? versions[versions.length - 1];
  return { art, versions, current };
}

function VersionTabs({ ctx, art, current }: { ctx: Ctx; art: any; current: any }) {
  return <div className="row">{art.versions.map((v: any) => (
    <button key={v.id} className={`pill ${v.id === current?.id ? "on" : ""}`} onClick={() => ctx.setFocus(v.id)}>
      {rno(v.display_no)}{v.origin === "manual" ? " 手稿" : v.origin === "combined" ? " 组合" : ""}{art.adopted_version_id === v.id ? " ✓" : ""}</button>))}</div>;
}

function AdoptBar({ ctx, art, version, hint, next }: { ctx: Ctx; art: any; version: any; hint: string; next?: string }) {
  const act = useAction();
  if (!version) return null;
  const adoptedHere = art.adopted_version_id === version.id;
  const adopt = (cont: boolean) => act.run(async () => {
    const res = await cmd("adopt", { artifact_id: art.id, version_id: version.id, expected_adopted_version_id: art.adopted_version_id, and_continue: cont });
    ctx.reload();
    if (cont && next) go(`#/e/${ctx.eid}/${next}`);
    return res;
  });
  return (
    <div className="actionbar">
      <span className="muted small">· {hint}</span>
      {!adoptedHere && art.adopted_version_id && <button className="btn" onClick={() => ctx.setFocus(art.adopted_version_id)}>对比 {rno(art.versions.find((v: any) => v.id === art.adopted_version_id)?.display_no)}</button>}
      {art.stage === "story" && !version.gate.review && <button className="btn primary" onClick={() => act.run(async () => { await cmd("submit_review", { version_id: version.id }); ctx.reload(); })}>发起独立审查</button>}
      {!adoptedHere && (art.stage !== "story" || version.gate.review) && <>
        <button className="btn" disabled={!version.gate.adoptable} onClick={() => adopt(false)}>只采用</button>
        {next && <button className="btn primary" disabled={!version.gate.adoptable} onClick={() => adopt(true)}>采用并继续</button>}
      </>}
      {adoptedHere && <Tag tone="green">{rno(version.display_no)} 已采用</Tag>}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 01 故事稿 (J06, J36, J54, J59, J60)
function StoryView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "story");
  const [tab, setTab] = useState("synopsis");
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState<any>({});
  const act = useAction();
  useEffect(() => { setDraft(current?.content ?? {}); setEdit(false); }, [current?.id]);
  const k = ctx.view.kickoff;
  if (!current) return (
    <div><div className="stagehead"><div><h1>故事稿</h1><div className="muted small">尚无稿件 · 来源：{k.source_type === "import" ? ctx.view.sourceImport?.original_name : k.idea_text ?? "选题卡"}</div></div></div>
      {ctx.view.sourceImport && <div className="field small">{ctx.view.sourceImport.parsed_text}</div>}
      <p className="muted">在右侧导演助手输入要求开始起草；导入的故事稿或剧本只作故事来源，起草后先过故事独审。</p></div>);
  const review = current.reviews[current.reviews.length - 1];
  const chars = Object.values(current.content).join("").length;
  const paras = (current.content.synopsis ?? "").split("\n").filter(Boolean);
  return (
    <div>
      <div className="stagehead">
        <div><h1>故事稿</h1><div className="muted small">{rno(current.display_no)} {current.id === art.adopted_version_id ? "已采用" : "草稿"} · 累计 {chars} 字 · 来源：{k.source_type === "import" ? `导入 ${ctx.view.sourceImport?.original_name}` : k.source_type === "topic" ? "选题卡" : "一个想法"}{k.relation === "continue" ? ` · 承接遗留 ${k.carried_facts.join("；")}` : ""}</div></div>
        <VersionTabs ctx={ctx} art={art} current={current} />
      </div>
      <div className="spread"><div className="tabs">{[["synopsis", "故事梗概"], ["cast", "角色表"], ["handoff", "交接说明"], ["notes", "开发辅助"], ["review", "独立审查"]].map(([t, l]) => (
        <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div>
        <div className="row">{edit ? <><button className="btn" onClick={() => setEdit(false)}>取消</button>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current.id, content: draft }); ctx.setFocus(r.versionId); ctx.reload(); })}>保存为手稿</button></>
          : <button className="btn" onClick={() => setEdit(true)}>✎ 编辑</button>}</div></div>
      {tab === "synopsis" && (edit ? <textarea style={{ minHeight: 260, marginTop: 12 }} value={draft.synopsis ?? ""} onChange={(e) => setDraft({ ...draft, synopsis: e.target.value })} /> : (
        <div className="field" style={{ marginTop: 12, border: "1px solid #2b5fb8" }}>{paras.map((p: string, i: number) => (
          <div key={i} className={`para ${ctx.sel?.data.paragraph === i + 1 ? "sel" : ""}`} onClick={() => ctx.setSel({ label: `故事梗概 第${i + 1}段`, data: { field: "synopsis", paragraph: i + 1, text: p.slice(0, 40) } })}>{p}</div>))}</div>))}
      {tab === "cast" && (edit ? <textarea style={{ minHeight: 160, marginTop: 12 }} value={draft.cast ?? ""} onChange={(e) => setDraft({ ...draft, cast: e.target.value })} /> : <CastTable md={current.content.cast ?? ""} />)}
      {tab === "handoff" && (edit ? <textarea style={{ marginTop: 12 }} value={draft.handoff ?? ""} onChange={(e) => setDraft({ ...draft, handoff: e.target.value })} /> : <div className="field" style={{ marginTop: 12 }}>{current.content.handoff}</div>)}
      {tab === "notes" && <div className="field" style={{ marginTop: 12 }}>{current.content.notes || current.content.adaptation ? JSON.stringify(current.content.adaptation ?? current.content.notes, null, 1) : "（无）"}</div>}
      {current.gate.formatErrors.length > 0 && <div className="card red" style={{ marginTop: 12 }}><b className="error">结构错误（不能放行）</b><ul className="small">{current.gate.formatErrors.map((e: string) => <li key={e}>{e}</li>)}</ul></div>}
      {(tab === "review" || review) && <StoryReview ctx={ctx} version={current} review={review} />}
      <ErrorLine error={act.error} />
      <AdoptBar ctx={ctx} art={art} version={current} next="script"
        hint={current.gate.blocking.length ? "必须守住的设定未守住：只能改故事或改设定" : current.gate.review ? "必需审核已执行，可采用" : "故事稿通过独审后才能交给制作"} />
    </div>
  );
}

function CastTable({ md }: { md: string }) {
  const rows = md.split("\n").filter((l) => l.trim().startsWith("|")).map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
  const body = rows.filter((r) => !r.every((c) => /^:?-{3,}:?$/.test(c)));
  return <table className="table" style={{ marginTop: 12 }}><tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => i === 0 ? <th key={j}>{c}</th> : <td key={j}>{c}</td>)}</tr>)}</tbody></table>;
}

function StoryReview({ ctx, version, review }: { ctx: Ctx; version: any; review: any }) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const act = useAction();
  if (!review) return <div className="card" style={{ marginTop: 16 }}><b>独立审查</b><div className="muted small" style={{ marginTop: 6 }}>尚未送审。送审冻结这次读取的版本，审核者另开会话，只读输入清单。</div></div>;
  const cold = review.observations[0];
  return (
    <div style={{ marginTop: 18 }}>
      <div className="spread"><h2>故事独立审查 · 第 {review.pass_no} 遍</h2>
        <span className="muted small">{PROVIDER_LABEL[review.reviewer]} · 另开会话（{review.isolation_level === "session" ? "会话隔离" : review.isolation_level}）· 只读取 {rno(version.display_no)} 与项目设定 · 清单 {review.manifest_hash.slice(0, 10)}</span></div>
      <div className="grid2">
        <div className="card"><div className="label" style={{ marginTop: 0 }}>冷读复述</div><div className="small">{cold?.evidence}</div></div>
        <div className="card"><div className="label" style={{ marginTop: 0 }}>对照检查（{review.observations.length - 1} 项）</div>
          {review.observations.slice(1, 7).map((o: any, i: number) => <div key={i} className="small" style={{ margin: "3px 0" }}><code className="mono">{o.objectId.slice(0, 18)}</code> {o.evidence.slice(0, 48)}</div>)}</div>
      </div>
      <div className="row" style={{ margin: "12px 0" }}><Tag tone={review.original_decision === "pass" ? "green" : "red"}>原结论 {review.original_decision === "pass" ? "通过" : "退回"}</Tag><span className="muted small">{review.issues.length} 条意见</span></div>
      {review.issues.map((i: any) => (
        <div key={i.id} className={`card ${i.blocking ? "red" : "line"}`} style={{ marginBottom: 10 }}>
          <div className="row"><Tag tone={i.blocking ? "red" : "amber"}>{i.blocking ? (i.category === "format" ? "硬错误" : "必须守住") : "普通意见"}</Tag><b>{i.loc.anchor}</b><span className="muted small">{i.category}</span></div>
          {i.setting_content && <div className="grid2" style={{ margin: "8px 0" }}><div className="field small">设定原文：{i.setting_content}</div><div className="field small" style={{ background: "var(--red-bg)" }}>冲突句：{i.loc.evidence}</div></div>}
          <div className="small" style={{ margin: "6px 0" }}>{i.reason}</div>
          {i.blocking ? <div className="row"><button className="btn" onClick={() => ctx.setSel({ label: `${i.loc.anchor}（审查意见）`, data: { issue: i.id, anchor: i.loc.anchor } })}>改故事</button>
            <a className="btn" href={`#/p/${ctx.view.episode.project_id}/settings`}>去项目设定</a><span className="muted tiny">没有放行入口</span></div>
            : i.waiver_reason ? <div className="ok small">已放行（仅本版本）：{i.waiver_reason}</div>
              : <div className="row"><input style={{ flex: 1 }} placeholder="写理由放行（绑定本版本，改动后失效）" value={reasons[i.id] ?? ""} onChange={(e) => setReasons({ ...reasons, [i.id]: e.target.value })} />
                <button className="btn" onClick={() => act.run(async () => { await cmd("waive", { issue_id: i.id, version_id: version.id, reason: reasons[i.id] ?? "" }); ctx.reload(); })}>写理由放行</button>
                <button className="btn" onClick={() => ctx.setSel({ label: `${i.loc.anchor}（审查意见）`, data: { issue: i.id } })}>修订</button></div>}
        </div>))}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 02 剧本 (J07, J24, J25, J68, J69)
function ScriptView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "script");
  const [tab, setTab] = useState("full");
  const [cmp, setCmp] = useState<any>(null);
  const [picks, setPicks] = useState<Record<string, string>>({});
  const act = useAction();
  const adoptedId = art?.adopted_version_id;
  useEffect(() => {
    if (tab === "diff" && current && adoptedId && current.id !== adoptedId) q("compare", { a_id: adoptedId, b_id: current.id }).then(setCmp);
    else setCmp(null);
  }, [tab, current?.id, adoptedId]);
  if (!art) return <div className="empty">故事未通过独审并采用前，不能写剧本。</div>;
  if (!current) return <div><div className="stagehead"><h1>剧本</h1></div><p className="muted">在右侧导演助手输入「按采用的故事写分场剧本」开始。</p></div>;
  const scenes = current.content.scenes ?? [];
  const baseV = current.id !== adoptedId ? art.versions.find((v: any) => v.id === (current.base_id ?? adoptedId)) : null;
  const baseScenes = baseV ? Object.fromEntries((baseV.content.scenes ?? []).map((x: any) => [x.id, x.action])) : null;
  const changedCount = baseScenes ? scenes.filter((x: any) => baseScenes[x.id] !== x.action).length : 0;
  const others = art.versions.filter((v: any) => v.id !== current.id && v.id !== adoptedId);
  return (
    <div>
      <div className="stagehead">
        <div><h1>剧本</h1><div className="muted small">{rno(current.display_no)} {current.id === adoptedId ? "已采用" : "草稿"}{current.base_id ? `（基于 ${rno(art.versions.find((v: any) => v.id === current.base_id)?.display_no)}）` : ""} · {baseScenes ? `改动 ${changedCount} 处 · 绿色新增 / 红色删除` : `${scenes.length} 场`}</div></div>
        <VersionTabs ctx={ctx} art={art} current={current} />
      </div>
      <div className="spread"><div className="tabs">{[["full", "全文"], ["outline", "阶段大纲"], ["diff", "差异"], ["side", "并排比较"]].map(([t, l]) => (
        <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div></div>
      {tab === "full" && scenes.map((s: any, i: number) => { const old = baseScenes?.[s.id]; return (
        <div key={s.id} style={{ marginTop: 18 }}>
          <h2>{i + 1}. {s.id} <span className="muted small">{s.heading}</span></h2>
          {old !== undefined && old !== s.action ? <div className="para" style={{ lineHeight: 1.8 }}>{charDiff(old, s.action).map((d, k) => d.op === "same" ? <span key={k}>{d.text}</span> : d.op === "del" ? <del key={k} className="d">{d.text}</del> : <ins key={k} className="d">{d.text}</ins>)}</div> :
          s.action.split(/(?<=。)/).filter(Boolean).map((p: string, j: number) => (
            <div key={j} className={`para ${ctx.sel?.data.scene === s.id && ctx.sel?.data.sentence === j + 1 ? "sel" : ""}`} onClick={() => ctx.setSel({ label: `${s.id} 第${j + 1}句`, data: { scene: s.id, sentence: j + 1 } })}>{p}</div>))}
          {s.dialogue && <div className="muted small">对白：{s.dialogue}</div>}
          {s.source && <div className="muted tiny">出处：故事 {s.source.storyVersion?.slice(0, 6)} · 节拍 {s.source.beat} · 原镜 {s.source.refShot}</div>}
        </div>); })}
      {tab === "outline" && <table className="table" style={{ marginTop: 12 }}><tbody>{scenes.map((s: any) => <tr key={s.id}><td style={{ width: 80 }}>{s.id}</td><td className="muted">{s.heading}</td><td>{s.action.slice(0, 60)}…</td></tr>)}</tbody></table>}
      {tab === "diff" && (cmp ? <div className="field" style={{ marginTop: 12 }}>{(cmp.fields.scenes ?? []).map((d: any, i: number) => (
        d.op === "same" ? <span key={i}>{d.text}</span> : d.op === "del" ? <del key={i} className="d">{d.text}</del> : <ins key={i} className="d">{d.text}</ins>))}</div>
        : <div className="muted" style={{ marginTop: 12 }}>当前版本即采用版，没有差异。</div>)}
      {tab === "side" && (
        <div style={{ marginTop: 12 }}>
          <div className="muted small">并排显示两份草稿及共同基版；可保留任一稿，或逐项组合成新的待审稿。关闭比较保留草稿。</div>
          <div className="grid2" style={{ marginTop: 10 }}>{[current, ...others.slice(-1)].map((v: any) => (
            <div key={v.id} className="card line"><div className="spread"><b>{rno(v.display_no)} · {v.origin === "manual" ? "手工稿" : v.origin === "combined" ? "组合稿" : "AI 稿"}</b>
              <div className="row">{["scenes", "notes"].map((f) => <button key={f} className={`pill ${picks[f] === v.id ? "on" : ""}`} onClick={() => setPicks({ ...picks, [f]: v.id })}>取{f === "scenes" ? "场次" : "说明"}</button>)}</div></div>
              {(v.content.scenes ?? []).map((s: any) => <div key={s.id} className="small" style={{ margin: "6px 0" }}><b>{s.id}</b> {s.action}</div>)}
              {v.content.notes && <div className="muted small">说明：{v.content.notes}</div>}</div>))}</div>
          <div className="row" style={{ marginTop: 10 }}><button className="btn primary" disabled={Object.keys(picks).length === 0} onClick={() => act.run(async () => {
            const r = await cmd("combine", { artifact_id: art.id, picks }); ctx.setFocus(r.versionId); setTab("full"); ctx.reload(); })}>组合成新的待审稿</button>
            <button className="btn" onClick={() => setTab("full")}>关闭比较</button></div>
        </div>)}
      <ErrorLine error={act.error} />
      <AdoptBar ctx={ctx} art={art} version={current} next="plan" hint="采用前先看影响 · 重复点击不会生成重复版本" />
    </div>
  );
}

// ---------------------------------------------------------------- 03 制作方案 (J09, J47, J72–J78)
function PlanView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "plan");
  const [tab, setTab] = useState("shots");
  const [shot, setShot] = useState<string | null>(null);
  const [trace, setTrace] = useState<any>(null);
  const act = useAction();
  const lay = useMemo(() => layout(current?.content), [current?.id]);
  const pr = useVersion(ctx, "prompts").art;
  const imgFor = (seg: string) => {
    const p = pr?.versions.find((v: any) => v.id === pr.adopted_version_id)?.content.segments?.[seg];
    const vid = p?.refs?.[0]?.assetVersionId;
    return blob(ctx.assets.find((a) => a.id === vid)?.sha256);
  };
  useEffect(() => { if (shot && art?.adopted_version_id) q("trace_shot", { episode_id: ctx.eid, shot_id: shot }).then(setTrace).catch(() => setTrace(null)); }, [shot]);
  if (!art) return <div className="empty">剧本采用后才能设计镜头。</div>;
  if (!current) return <div><div className="stagehead"><h1>制作方案</h1></div><p className="muted">让助手「按剧本排镜头与生成段」，或从参考包选镜头。</p></div>;
  const shots = current.content.shots ?? [];
  const scenes: string[] = [...new Set<string>(shots.map((s: any) => s.origin?.scene ?? "参考镜头"))];
  const over = lay.segments.filter((s: any) => s.duration > 15000);
  return (
    <div>
      <div className="stagehead">
        <div><h1>制作方案</h1><div className="muted small">{rno(current.display_no)} {current.id === art.adopted_version_id ? "已采用" : "草稿"} · {shots.length} 镜 · {lay.segments.length} 段 · {Math.round(lay.total / 1000)} 秒 · 镜头时长加总等于段长</div></div>
        <div className="tabs">{[["shots", "镜头"], ["segments", "生成段"], ["table", "表格"]].map(([t, l]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div>
      </div>
      <VersionTabs ctx={ctx} art={art} current={current} />
      {over.map((s: any) => (
        <div key={s.id} className="card amber" style={{ marginTop: 10 }}>
          <b>{s.id} 共 {s.duration / 1000} 秒，超过 15 秒</b><span className="muted small"> · 须在动作完成处拆段，修复前阻断交付</span>
          <div className="row" style={{ marginTop: 8 }}>{s.shots.slice(1).map((sid: string) => (
            <button key={sid} className="btn" onClick={() => act.run(async () => { const r = await cmd("split_segment", { version_id: current.id, segment_id: s.id, first_shot_of_second: sid }); ctx.setFocus(r.versionId); ctx.reload(); })}>在 {sid} 前拆开</button>))}</div>
        </div>))}
      {tab === "shots" && scenes.map((sc) => (
        <div key={sc}>
          <h2 style={{ marginTop: 18 }}>{sc}</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(180px,1fr))", gap: 10 }}>
            {shots.filter((s: any) => (s.origin?.scene ?? "参考镜头") === sc).map((s: any) => (
              <div key={s.id} className={`shotcard ${shot === s.id ? "sel" : ""}`} onClick={() => { setShot(s.id); ctx.setSel({ label: `镜头 ${s.id}（${s.segment}）`, data: { shot: s.id, segment: s.segment, version: current.id } }); }}>
                <div className="pic" style={{ backgroundImage: imgFor(s.segment) ? `url(${imgFor(s.segment)})` : undefined }} />
                <div className="body">
                  <div className="spread"><b>{s.id} <span className="muted small">{s.duration_ms / 1000}s</span></b><Tag tone="amber">{s.segment}</Tag></div>
                  <div className="muted tiny">{s.camera} · 全片 {fmtMs(lay.shots[s.id]?.start ?? 0)}–{fmtMs(lay.shots[s.id]?.end ?? 0)}</div>
                  <div className="small" style={{ marginTop: 4 }}>{s.action}</div>
                  {s.refs?.length > 0 && <div className="tiny" style={{ marginTop: 4 }}><Tag tone="violet">参考 {s.refs.map((r: any) => r.mode).join("/")}</Tag></div>}
                </div>
              </div>))}
          </div>
        </div>))}
      {tab === "segments" && <table className="table" style={{ marginTop: 12 }}><thead><tr><th>段</th><th>策略</th><th>全片时间</th><th>段长</th><th>镜头</th></tr></thead>
        <tbody>{lay.segments.map((s: any) => <tr key={s.id}><td>{s.id}</td><td>{s.strategy}</td><td>{fmtMs(s.start)}–{fmtMs(s.end)}</td><td>{s.duration / 1000}s</td><td>{s.shots.join("、")}</td></tr>)}</tbody></table>}
      {tab === "table" && <table className="table" style={{ marginTop: 12 }}><thead><tr><th>镜</th><th>段</th><th>段内</th><th>机位</th><th>动作</th><th>出处</th></tr></thead>
        <tbody>{shots.map((s: any) => { const t = lay.shots[s.id]; const seg = lay.segments.find((x: any) => x.id === s.segment);
          return <tr key={s.id}><td>{s.id}</td><td>{s.segment}</td><td>{((t.start - seg.start) / 1000).toFixed(1)}–{((t.end - seg.start) / 1000).toFixed(1)}s</td><td>{s.camera}</td><td>{s.action}</td><td className="muted">{s.refs?.length ? "参考镜头" : s.origin?.scene}</td></tr>; })}</tbody></table>}
      <div className="segstrip">{lay.segments.map((s: any) => (
        <div key={s.id} className={s.shots.includes(shot) ? "on" : ""} style={{ flex: s.duration }}>{s.id} · {Math.round(s.duration / 100) / 10}s</div>))}</div>
      {trace && (
        <div className="card line" style={{ marginTop: 14 }}>
          <b>{trace.shot.id} 出处</b>
          {trace.refs.map((r: any, i: number) => <div key={i} className="small">原片 {r.source.name} · 参考包 v{r.packVersion} · {r.refShot} {fmtMs(r.start_ms)}–{fmtMs(r.end_ms)} · 用法 {r.mode} · {r.reason}</div>)}
          {trace.origin && <div className="small">原创镜：剧本 {trace.origin.scene}「{trace.origin.line ?? ""}」</div>}
        </div>)}
      <ErrorLine error={act.error} />
      <AdoptBar ctx={ctx} art={art} version={current} next="prompts" hint="空间锁定通过后才能出图" />
    </div>
  );
}

// ---------------------------------------------------------------- 04 空间锁定 (J39)
function SpaceView({ ctx }: { ctx: Ctx }) {
  const items = useQuery<any[]>("project_todos", { project_id: ctx.view.episode.project_id });
  const [form, setForm] = useState({ label: "", relation: "", source: "", depends: "" });
  const [rows, setRows] = useState<any[]>([]);
  const act = useAction();
  const load = async () => {
    const blocked = await q("blocked_segments", { episode_id: ctx.eid });
    setRows(Object.entries(blocked).map(([seg, rs]: any) => ({ seg, reasons: rs })));
  };
  useEffect(() => { load(); }, [ctx.eid, items.data]);
  const pending = (items.data ?? []).filter((t) => t.kind === "space" && t.episodeId === ctx.eid);
  return (
    <div>
      <div className="stagehead"><div><h1>空间锁定</h1><div className="muted small">逐条出处；未确认的用验证图，只阻塞依赖项</div></div></div>
      {pending.length === 0 && <div className="card"><span className="ok">所有空间关系已确认</span>，可以出图。</div>}
      {pending.map((t) => (
        <div key={t.objectId} className="card amber" style={{ marginBottom: 10 }}>
          <div className="row"><Tag tone="amber">待确认</Tag><b>{t.title}</b></div>
          <div className="row" style={{ marginTop: 8 }}>
            <select id={`lock-${t.objectId}`}>{ctx.assets.map((a) => <option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select>
            <button className="btn primary" onClick={() => act.run(async () => {
              const el = document.getElementById(`lock-${t.objectId}`) as HTMLSelectElement;
              await cmd("confirm_space_item", { item_id: t.objectId, lock_asset_version_id: el.value }); items.reload(); ctx.reload(); })}>用这张图确认锁定</button>
            <span className="muted small">确认后复核实际使用它的段</span>
          </div>
        </div>))}
      {rows.length > 0 && <div className="muted small" style={{ margin: "10px 0" }}>当前阻塞：{rows.map((r) => `${r.seg}（${r.reasons.join("；")}）`).join("、")}；其他段与身份图可以继续准备。</div>}
      <h2 style={{ marginTop: 20 }}>登记空间关系</h2>
      <div className="grid2">
        <input placeholder="地标 / 道具，例：桶的高度" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
        <input placeholder="相对位置，例：桶比柜台低一个爪子" value={form.relation} onChange={(e) => setForm({ ...form, relation: e.target.value })} />
        <input placeholder="出处，例：剧本阶段 2" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
        <input placeholder="依赖段，逗号分隔，例：G04" value={form.depends} onChange={(e) => setForm({ ...form, depends: e.target.value })} />
      </div>
      <button className="btn" style={{ marginTop: 10 }} onClick={() => act.run(async () => { await cmd("add_space_item", { episode_id: ctx.eid, label: form.label, relation: form.relation, source: form.source, depends: form.depends.split(/[,，、\s]+/).filter(Boolean) }); items.reload(); })}>登记（待确认）</button>
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 05 角色出镜 (J40)
function CastView({ ctx }: { ctx: Ctx }) {
  const m = useQuery<any>("cast_matrix", { episode_id: ctx.eid });
  const [role, setRole] = useState("");
  const act = useAction();
  if (!m.data) return null;
  const roles = [...new Set([...m.data.roles, ...(role ? [role] : [])])];
  const set = (seg: string, r: string, status: string) => act.run(async () => {
    const cur = m.data.rows.find((x: any) => x.segment === seg)?.cells[r];
    await cmd("set_cast", { episode_id: ctx.eid, segment_id: seg, role: r, status, asset_version_id: cur?.assetVersionId ?? ctx.assets[0]?.id ?? null, cells: cur?.cells ?? [] });
    m.reload();
  });
  return (
    <div>
      <div className="stagehead"><div><h1>角色出镜与身份覆盖</h1><div className="muted small">逐段绑定；grid 段的交付输入只取本段一张九宫格，缺口可修格或改 flex</div></div>
        <div className="row"><input placeholder="新增角色 ID" value={role} onChange={(e) => setRole(e.target.value)} style={{ width: 140 }} /></div></div>
      <table className="table"><thead><tr><th>段</th><th>策略</th>{roles.map((r) => <th key={r}>{r}</th>)}</tr></thead>
        <tbody>{m.data.rows.map((row: any) => (
          <tr key={row.segment}><td><b>{row.segment}</b></td><td>{row.strategy}</td>
            {roles.map((r) => { const c = row.cells[r]; return (
              <td key={r}>{c ? <div className="col" style={{ gap: 4 }}>
                <Tag tone={c.status === "ok" ? "green" : c.status === "unclear" ? "amber" : ""}>{c.status === "ok" ? "身份清楚" : c.status === "unclear" ? "看不清" : "未出镜"}</Tag>
                {c.cells?.length > 0 && <span className="muted tiny">格 {c.cells.join("、")}</span>}
                {c.note && <span className="muted tiny">{c.note}</span>}
                {c.options && <div className="row">{c.options.map((o: string) => <a key={o} className="btn" href={o.includes("flex") ? `#/e/${ctx.eid}/plan` : `#/e/${ctx.eid}/assets`}>{o}</a>)}</div>}
              </div> : <div className="row">{["ok", "unclear", "absent"].map((s) => <button key={s} className="btn ghost" onClick={() => set(row.segment, r, s)}>{s === "ok" ? "✓" : s === "unclear" ? "?" : "—"}</button>)}</div>}</td>); })}
          </tr>))}</tbody></table>
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 06 素材与选图 (J10, J14, J15, J18, J27, J41)
function AssetsView({ ctx }: { ctx: Ctx }) {
  const [tab, setTab] = useState("round");
  const [mode, setMode] = useState("原图");
  const [detail, setDetail] = useState<any>(null);
  const [newBatch, setNewBatch] = useState<any>(null);
  const providers = useQuery<any>("image_providers");
  const act = useAction();
  const images = ctx.assets.filter((a) => a.kind === "image").sort((a, b) => (b.grid ? 1 : 0) - (a.grid ? 1 : 0));
  const marked = (a: any, m: string) => a.marks.includes(m);
  const filter = tab === "round" ? images.filter((a) => !marked(a, "backup")) : tab === "backup" ? images.filter((a) => !marked(a, "adopted") && !marked(a, "required")) : images;
  const imgStyle = mode === "灰度" ? { filter: "grayscale(1)" } : mode === "手机尺寸" ? { maxWidth: 180, margin: "auto" } : {};
  return (
    <div>
      <div className="stagehead">
        <div><h1>素材与选图</h1><div className="muted small">{images.length} 张 · 已采用 {images.filter((a) => marked(a, "adopted")).length} · 本集图像批次 {ctx.batches.length}</div></div>
        <div className="row"><div className="tabs">{[["needs", "需求"], ["round", "本轮出图"], ["backup", "补选区"]].map(([t, l]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>))}</div>
          <button className="btn primary" onClick={() => setNewBatch({ operation: "generate" })}>出图</button>
          <label className="btn">上传<input type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && act.run(async () => { await upload("asset", e.target.files![0], { projectId: ctx.view.episode.project_id, episodeId: ctx.eid, category: "state" }); ctx.reload(); })} /></label></div>
      </div>
      {tab === "needs" ? <NeedsTable ctx={ctx} /> : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 150px", gap: 14 }}>
          {filter.slice(0, 2).map((a, i) => (
            <div key={a.id}>
              <div className={`imgbox ${detail?.id === a.id ? "card sel" : ""}`} style={{ padding: 0, cursor: "pointer" }} onClick={() => setDetail(a)}><img src={blob(a.sha256)} style={imgStyle} /></div>
              <div className="spread" style={{ marginTop: 6 }}><b>{a.name} · 候选 {String.fromCharCode(65 + i)} <span className="muted small">v{a.version_no}</span></b>
                {marked(a, "required") ? <Tag tone="green">必用 · 你选定</Tag> : marked(a, "backup") ? <Tag>补选区</Tag> : null}</div>
              <div className="row" style={{ marginTop: 6 }}>
                {["required", "recommended", "backup"].map((m) => <button key={m} className="btn ghost" onClick={() => act.run(async () => { await cmd("mark_image", { episode_id: ctx.eid, version_id: a.id, mark: m }); ctx.reload(); })}>{m === "required" ? "标必用" : m === "recommended" ? "推荐" : "补选"}</button>)}
                {!!a.grid && <button className="btn" onClick={() => setNewBatch({ operation: "edit", base: a })}>修一格</button>}
                <button className="btn" onClick={() => act.run(async () => { const r = await cmd("adopt_image", { episode_id: ctx.eid, version_id: a.id }); if (confirm(`采用前影响：${r.references.map((x: any) => x.segment).join("、") || "暂无引用"}。确认采用？`)) { await cmd("adopt_image", { episode_id: ctx.eid, version_id: a.id, confirm: true }); ctx.reload(); } })}>采用</button>
              </div>
            </div>))}
          <div className="col">
            <span className="muted small">查看方式</span>
            {["原图", "灰度", "手机尺寸", "并排"].map((m) => <button key={m} className={`pill ${mode === m ? "on" : ""}`} onClick={() => setMode(m)}>{m}</button>)}
            <span className="muted small" style={{ marginTop: 10 }}>其余候选</span>
            {filter.slice(2).map((a) => <img key={a.id} src={blob(a.sha256)} style={{ width: "100%", borderRadius: 6, cursor: "pointer" }} onClick={() => setDetail(a)} />)}
          </div>
        </div>)}
      {detail?.grid ? <GridDetail a={detail} /> : null}
      {ctx.batches.length > 0 && <h2 style={{ marginTop: 24 }}>图像批次</h2>}
      {ctx.batches.map((b) => (
        <div key={b.id} className="card line" style={{ marginBottom: 8 }}>
          <div className="spread"><span>{b.provider} · {b.operation === "edit" ? "局部重画" : "生成"} · {b.count} 张 · {b.size}</span><button className="btn" onClick={() => ctx.openBatch(b)}>打开</button></div>
          <div className="row small" style={{ marginTop: 6 }}>{b.requests.map((r: any) => <Tag key={r.id} tone={r.state === "succeeded" ? "green" : r.state === "unknown" ? "amber" : r.state === "failed" ? "red" : ""}>#{r.ordinal} {REQ[r.state] ?? r.state}</Tag>)}</div>
        </div>))}
      {newBatch && <NewBatch ctx={ctx} init={newBatch} providers={providers.data ?? {}} close={() => { setNewBatch(null); ctx.reload(); }} />}
      <ErrorLine error={act.error} />
    </div>
  );
}

const DEC: Record<string, string> = { pass: "通过", return: "退回", uncertain: "待判断" };
const REQ: Record<string, string> = { pending: "待发", submitted: "已发送", succeeded: "已返回", unknown: "待核对", failed: "失败", confirmed_not_executed: "确认未执行" };

function NeedsTable({ ctx }: { ctx: Ctx }) {
  const plan = artifactFor(ctx.view, "plan");
  const content = plan?.versions.find((v: any) => v.id === plan.adopted_version_id)?.content;
  const lay = layout(content);
  return <table className="table"><thead><tr><th>段</th><th>策略</th><th>镜号</th><th>素材用途</th><th>缺口</th></tr></thead>
    <tbody>{lay.segments.map((s: any) => { const has = ctx.assets.some((a) => a.name.startsWith(s.id));
      return <tr key={s.id}><td>{s.id}</td><td>{s.strategy}</td><td>{s.shots.join("、")}</td><td>{s.strategy === "grid" ? "本段九宫格" : "角色 / 场景 / 道具参考"}</td><td>{has ? <Tag tone="green">已有</Tag> : <Tag tone="amber">缺图</Tag>}</td></tr>; })}</tbody></table>;
}

function GridDetail({ a }: { a: any }) {
  return (
    <div className="card line" style={{ marginTop: 14 }}>
      <b>九宫格详情 · {a.name} v{a.version_no}</b>{a.changed_cells && <span className="muted small"> · 本版只改第 {a.changed_cells.join("、")} 格，其余沿用 v{a.version_no - 1}</span>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(9,1fr)", gap: 6, marginTop: 10 }}>
        {(a.cells ?? Array.from({ length: 9 }, (_, i) => ({ cell: i + 1 }))).map((c: any) => (
          <div key={c.cell} className={`card ${a.changed_cells?.includes(c.cell) ? "amber" : "line"}`} style={{ padding: 8 }}>
            <b>{c.cell}</b><div className="muted tiny">{c.moment ?? "时刻"}</div></div>))}
      </div>
    </div>
  );
}

function NewBatch({ ctx, init, providers, close }: { ctx: Ctx; init: any; providers: any; close: () => void }) {
  const names = Object.keys(providers);
  const [provider, setProvider] = useState(names[0] ?? "");
  const [prompt, setPrompt] = useState(init.base ? `局部重画 ${init.base.name} 第 5 格` : "");
  const [count, setCount] = useState(1);
  const [size, setSize] = useState("");
  const [cells, setCells] = useState<number[]>(init.base ? [5] : []);
  const [ref, setRef] = useState("");
  const [budget, setBudget] = useState("2.00");
  const [segs, setSegs] = useState("");
  const act = useAction();
  const caps = providers[provider]?.capabilities;
  useEffect(() => { if (caps && !size) setSize(caps.sizes[0]); }, [provider]);
  const create = () => act.run(async () => {
    const budget_cents = Math.round(parseFloat(budget) * 100);
    const r = init.base ? await cmd("redraw_cells", { episode_id: ctx.eid, base_version_id: init.base.id, cells, reference_version_ids: ref ? [ref] : [], prompt, provider, size, budget_cents })
      : await cmd("create_batch", { episode_id: ctx.eid, provider, operation: "generate", size, budget_cents,
        items: Array.from({ length: count }, (_, i) => ({ prompt, segments: segs.split(/[,，\s]+/).filter(Boolean), target: { name: `${prompt.slice(0, 10)} ${i + 1}`, category: "state" } })) });
    ctx.openBatch(await q("batch_view", { batch_id: r.batchId }));
    close();
  });
  return (
    <Modal onClose={close} width={620}>
      <div className="row"><Tag tone="amber">会消耗额度</Tag><h2 style={{ margin: 0 }}>{init.base ? `局部重画 ${init.base.name}` : "发起图像批次"}</h2></div>
      <div className="formrow"><span className="muted">通道</span><select value={provider} onChange={(e) => setProvider(e.target.value)}>{names.map((n) => <option key={n} value={n}>{n}（{providers[n].verified === "simulated" ? "模拟" : providers[n].verified === "pending_verification" ? "待验证" : "已验证"}）</option>)}</select></div>
      <div className="formrow"><span className="muted">要求</span><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} /></div>
      {init.base ? <>
        <div className="formrow"><span className="muted">只改格</span><div className="row">{Array.from({ length: 9 }, (_, i) => i + 1).map((c) => <button key={c} className={`pill ${cells.includes(c) ? "on" : ""}`} onClick={() => setCells(cells.includes(c) ? cells.filter((x) => x !== c) : [...cells, c])}>{c}</button>)}</div></div>
        <div className="formrow"><span className="muted">参考（仅绘图）</span><select value={ref} onChange={(e) => setRef(e.target.value)}><option value="">无</option>{ctx.assets.filter((a) => a.id !== init.base.id).map((a) => <option key={a.id} value={a.id}>{a.name} v{a.version_no}</option>)}</select></div>
      </> : <>
        <div className="formrow"><span className="muted">张数</span><input type="number" min={1} max={9} value={count} onChange={(e) => setCount(+e.target.value)} /></div>
        <div className="formrow"><span className="muted">用于段</span><input placeholder="例：G04（依赖未确认空间锁定的段会被拦下）" value={segs} onChange={(e) => setSegs(e.target.value)} /></div>
      </>}
      <div className="formrow"><span className="muted">尺寸</span><select value={size} onChange={(e) => setSize(e.target.value)}>{(caps?.sizes ?? []).map((s: string) => <option key={s}>{s}</option>)}</select></div>
      <div className="formrow"><span className="muted">本批预算上限</span><input value={budget} onChange={(e) => setBudget(e.target.value)} /></div>
      <p className="muted small">只执行这一批；结果不明会标为“待核对”，不会自动重发。</p>
      <ErrorLine error={act.error} />
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={close}>取消</button><button className="btn primary" disabled={act.busy} onClick={create}>生成摘要，去确认</button></div>
    </Modal>
  );
}

const BATCH_LABEL: Record<string, string> = { awaiting_authorization: "待确认", authorized: "已授权", completed: "完成", partial: "部分待发",
  pending_reconcile: "待核对", partially_succeeded: "部分成功", failed: "失败", cancelled: "已取消" };

function BatchModal({ batch, assets, close }: { batch: any; assets: any[]; close: () => void }) {
  const b = useQuery<any>("batch_view", { batch_id: batch.id });
  const [accept, setAccept] = useState(false);
  const act = useAction();
  usePoll(b.data?.state === "authorized", b.reload, 1000);
  if (!b.data) return null;
  const d = b.data;
  return (
    <Modal onClose={close} width={640}>
      <div className="row"><Tag tone="amber">会消耗额度</Tag><h2 style={{ margin: 0 }}>{d.operation === "edit" ? "局部重画" : "图像批次"} · {d.count} 张</h2><Tag>{BATCH_LABEL[d.state] ?? d.state}</Tag></div>
      {d.operation === "edit" && (() => { const it = d.spec.items[0]; const base = assets.find((a) => a.id === it.inputs?.[0]); return base ? (
        <div className="row" style={{ marginTop: 12, flexWrap: "nowrap" }}>
          <div style={{ position: "relative", width: 96 }}><img src={blob(base.sha256)} style={{ width: 96, borderRadius: 6, display: "block" }} />
            <div style={{ position: "absolute", inset: 0, display: "grid", gridTemplateColumns: "repeat(3,1fr)", gridTemplateRows: "repeat(3,1fr)" }}>
              {Array.from({ length: 9 }, (_, i) => <div key={i} style={{ border: it.cells?.includes(i + 1) ? "2px solid #f5b13d" : "none" }} />)}</div></div>
          <span className="muted" style={{ fontSize: 20 }}>→</span>
          <div className="small">基础图 {base.name}（{base.marks.includes("required") ? "必用" : "候选"}）<br />只改：第 {it.cells?.join("、")} 格；其余 {9 - (it.cells?.length ?? 0)} 格保持不变<br />参考：{it.inputs.slice(1).map((x: string) => assets.find((a) => a.id === x)?.name).join("、") || "无"}</div>
        </div>) : null; })()}
      <table className="table" style={{ margin: "12px 0" }}><tbody>
        <tr><td className="muted">服务</td><td>{d.provider}{d.model ? ` · ${d.model}` : ""} · {d.operation === "edit" ? "编辑（带基础图）" : "生成"}</td></tr>
        <tr><td className="muted">实际输入</td><td>{d.spec.items.map((it: any, i: number) => <div key={i} className="small">#{i + 1} {it.prompt}{it.cells ? ` · 只改第 ${it.cells.join("、")} 格` : ""}{it.inputs?.length ? ` · 输入 ${it.inputs.length} 张` : ""}</div>)}</td></tr>
        <tr><td className="muted">尺寸</td><td>{d.size}</td></tr>
        <tr><td className="muted">预算 / 费用</td><td>¥{((d.budget_cents ?? 0) / 100).toFixed(2)} · {d.cost_state === "unknown" ? "费用未知" : `估算 ¥${((d.estimate_cents ?? 0) / 100).toFixed(2)}`}</td></tr>
      </tbody></table>
      {d.requests.map((r: any) => (
        <div key={r.id} className="spread small" style={{ margin: "4px 0" }}>
          <span>#{r.ordinal} <span className="mono">{r.local_request_id}</span> {r.replaces_id ? "（补发）" : ""}</span>
          <span className="row"><Tag tone={r.state === "succeeded" ? "green" : r.state === "unknown" ? "amber" : r.state === "failed" ? "red" : ""}>{REQ[r.state] ?? r.state}</Tag>
            {r.state === "unknown" && <>
              <button className="btn" onClick={() => act.run(async () => { await cmd("reconcile_request", { request_id: r.id, finding: "not_executed" }); b.reload(); })}>核实未执行</button>
              <label className="btn">登记已完成图<input type="file" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && act.run(async () => { await upload("reconcile", e.target.files![0], { requestId: r.id }); b.reload(); })} /></label></>}
            {["confirmed_not_executed", "failed"].includes(r.state) && <button className="btn" onClick={() => act.run(async () => { await cmd("resubmit_request", { request_id: r.id }); b.reload(); })}>按原授权重新提交</button>}
          </span>
        </div>))}
      {d.state === "awaiting_authorization" && d.cost_state === "unknown" && <label className="small"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} /> 我知道费用未知，仍然提交</label>}
      <ErrorLine error={act.error} />
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        {d.state === "awaiting_authorization" && <button className="btn" onClick={() => act.run(async () => { await cmd("cancel_batch", { batch_id: d.id }); close(); })}>取消</button>}
        {d.state === "awaiting_authorization" && <button className="btn primary" onClick={() => act.run(async () => { await cmd("authorize_batch", { batch_id: d.id, summary_hash: d.summary_hash, accept_unknown_cost: accept }); await cmd("execute_batch", { batch_id: d.id }); b.reload(); })}>确认提交 {d.count} 张</button>}
        {["partial"].includes(d.state) && <button className="btn primary" onClick={() => act.run(async () => { await cmd("execute_batch", { batch_id: d.id }); b.reload(); })}>发送未发送的请求</button>}
        <button className="btn" onClick={close}>关闭</button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- 07 提示词 (J11)
function PromptsView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "prompts");
  const plan = artifactFor(ctx.view, "plan");
  const lay = layout(plan?.versions.find((v: any) => v.id === plan.adopted_version_id)?.content);
  const [seg, setSeg] = useState<string>("");
  const [txt, setTxt] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [sound, setSound] = useState<any>(null);
  const act = useAction();
  const entry = current?.content.segments?.[seg];
  useEffect(() => { if (!seg && lay.segments[0]) setSeg(lay.segments[0].id); }, [lay.segments.length]);
  useEffect(() => { setTxt(entry?.txt ?? ""); setPreview(null); setSound(null); }, [current?.id, seg]);
  if (!art) return <div className="empty">制作方案采用后才能写分段提示词。</div>;
  const segInfo = lay.segments.find((s: any) => s.id === seg);
  const errors = (current?.gate.formatErrors ?? []).filter((e: string) => e.startsWith(seg + "："));
  const save = (content: any) => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current?.id ?? null, content }); ctx.setFocus(r.versionId); ctx.reload(); });
  const move = (i: number, d: number) => act.run(async () => {
    const order = entry.refs.map((r: any) => r.assetVersionId);
    [order[i], order[i + d]] = [order[i + d], order[i]];
    setPreview({ order, ...(await cmd("reorder_refs", { version_id: current.id, segment_id: seg, new_order: order })) });
  });
  return (
    <div>
      <div className="stagehead"><div><h1>提示词</h1><div className="muted small">{current ? `${rno(current.display_no)} ${current.id === art.adopted_version_id ? "已采用" : "草稿"}` : "尚无"} · 参考图条在上，固定块自动装配</div></div>
        {current && <VersionTabs ctx={ctx} art={art} current={current} />}</div>
      <div className="row">{lay.segments.map((s: any) => <button key={s.id} className={`pill ${seg === s.id ? "on" : ""}`} onClick={() => setSeg(s.id)}>{s.id} · {s.duration / 1000}s</button>)}</div>
      {entry ? <>
        <div className="row" style={{ margin: "14px 0", alignItems: "stretch" }}>
          {entry.refs.map((r: any, i: number) => { const a = ctx.assets.find((x) => x.id === r.assetVersionId); return (
            <div key={r.assetVersionId} className="card line" style={{ width: 150, padding: 8 }}>
              <img src={blob(a?.sha256)} style={{ width: "100%", borderRadius: 6 }} />
              <div className="spread"><b>{r.label}</b><span className="row">{i > 0 && <button className="btn ghost" onClick={() => move(i, -1)}>←</button>}{i < entry.refs.length - 1 && <button className="btn ghost" onClick={() => move(i, 1)}>→</button>}</span></div>
              <div className="muted tiny">{r.role || "用途未写"}{r.grid ? " · 九宫格" : ""}</div></div>); })}
          <div className="muted small" style={{ alignSelf: "center" }}>{segInfo?.strategy === "grid" ? "grid：交付输入只取本段一张九宫格（替换本段九宫格）" : "flex：可增删、替换、重排不同参考"}</div>
        </div>
        {preview && <div className="card amber" style={{ marginBottom: 10 }}><b>重排预览</b> <span className="small">{Object.entries(preview.preview.mapping).map(([a, b]) => `${a}→${b}`).join("，")}</span>
          {preview.preview.unclearRoles.length > 0 && <div className="small">用途不明确，请核对：{preview.preview.unclearRoles.join("、")}</div>}
          <div className="field small" style={{ marginTop: 6 }}>{preview.preview.txtDiff.map((d: any, i: number) => d.op === "same" ? <span key={i}>{d.text}</span> : d.op === "del" ? <del key={i} className="d">{d.text}</del> : <ins key={i} className="d">{d.text}</ins>)}</div>
          <div className="row" style={{ marginTop: 6 }}><button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("reorder_refs", { version_id: current.id, segment_id: seg, new_order: preview.order, confirm: true }); ctx.setFocus(r.versionId); setPreview(null); ctx.reload(); })}>确认重排</button><button className="btn" onClick={() => setPreview(null)}>取消</button></div></div>}
        <textarea style={{ minHeight: 260, fontSize: 13 }} value={txt} onChange={(e) => setTxt(e.target.value)} />
        {errors.length > 0 && <div className="error">{errors.join("；")}</div>}
        <div className="spread" style={{ marginTop: 10 }}>
          <div className="row small">声音：<Tag tone={entry.sound?.bgm ? "green" : ""}>{entry.sound?.bgm ? "有背景音乐" : "无背景音乐"}</Tag>
            {(entry.sound?.ambience ?? []).map((x: string) => <Tag key={x}>{x}</Tag>)}{(entry.sound?.dialogue ?? []).map((x: string) => <Tag key={x} tone="blue">{x}</Tag>)}
            {entry.sound?.silent && <Tag tone="amber">全静音</Tag>}</div>
          <div className="row">
            <button className="btn" onClick={() => act.run(async () => { const r = await cmd("set_sound", { version_id: current.id, segment_id: seg, change: { bgm: !entry.sound?.bgm } }); ctx.setFocus(r.versionId); ctx.reload(); })}>{entry.sound?.bgm ? "去掉背景音乐" : "加背景音乐"}</button>
            <button className="btn" onClick={() => act.run(async () => { const r = await cmd("set_sound", { version_id: current.id, segment_id: seg, change: { silent: true } }); if (r.needsConfirm) setSound(r); else { ctx.setFocus(r.versionId); ctx.reload(); } })}>全静音</button>
            <button className="btn primary" onClick={() => save({ ...current.content, segments: { ...current.content.segments, [seg]: { ...entry, txt } } })}>保存本段</button>
          </div>
        </div>
        {sound && <div className="card amber" style={{ marginTop: 10 }}><b>全静音会与以下内容冲突</b>{sound.conflicts.map((c: any) => <div key={c.item} className="small">{c.item}：{Array.isArray(c.detail) ? c.detail.join("、") : c.detail}</div>)}
          <div className="row" style={{ marginTop: 6 }}><button className="btn danger" onClick={() => act.run(async () => { const r = await cmd("set_sound", { version_id: current.id, segment_id: seg, change: { silent: true }, confirm: true }); ctx.setFocus(r.versionId); setSound(null); ctx.reload(); })}>仍然全静音</button><button className="btn" onClick={() => setSound(null)}>保留</button></div></div>}
      </> : <p className="muted" style={{ marginTop: 14 }}>该段还没有提示词。让助手「为 {seg} 写提示词」。</p>}
      <ErrorLine error={act.error} />
      {current && <AdoptBar ctx={ctx} art={art} version={current} next="publish" hint="冻结实际送审 TXT 与引用文件后进入两遍独审" />}
    </div>
  );
}

// ---------------------------------------------------------------- 08 两遍独审 (J17, J21)
function ReviewView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "prompts");
  const st = useQuery<any>(current ? "prompt_review_status" : null, { version_id: current?.id });
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const act = useAction();
  if (!art || !current) return <div className="empty">提示词定稿后才能两遍独审。</div>;
  const rows = Object.entries(st.data ?? {});
  const cnt = (f: (x: any) => boolean) => rows.filter(([, x]) => f(x)).length;
  const label = (x: any) => x.ok ? (x.waived ? "用户放行" : "通过") : [x.coldRead.state, x.comparison.state].includes("blocked") || [x.coldRead.decision, x.comparison.decision].includes("return") && !x.waived ? "退回" : [x.coldRead.state, x.comparison.state].includes("needed") ? "待审" : "待判断";
  const submit = () => act.run(async () => {
    for (const [seg, x] of rows as any) {
      if (x.coldRead.state === "needed") await cmd("start_prompt_review", { version_id: current.id, segment: seg, pass_name: "cold_read" });
    }
    st.reload(); ctx.reload();
  });
  const second = () => act.run(async () => {
    for (const [seg, x] of rows as any) if (x.coldRead.state !== "needed" && x.comparison.state === "needed") await cmd("start_prompt_review", { version_id: current.id, segment: seg, pass_name: "comparison" });
    st.reload(); ctx.reload();
  });
  return (
    <div>
      <div className="stagehead"><div><h1>两遍独审</h1><div className="muted small">{PROVIDER_LABEL[ctx.view.kickoff.executor_plan.review.provider]} · 独立会话 · 第一遍只读取 TXT 和按顺序的原图 · 第二遍才开放故事稿和制作方案</div></div>
        <div className="row"><button className="btn" onClick={submit}>送审材料（第一遍）</button><button className="btn" onClick={second}>开始第二遍</button>
          <button className="btn primary" onClick={() => go(`#/e/${ctx.eid}/delivery`)}>登记 review.md</button></div></div>
      <div className="grid3">
        <div className="card"><div className="muted small">通过</div><div style={{ fontSize: 22, color: "var(--green)", fontWeight: 700 }}>{cnt((x) => x.ok)} 段</div></div>
        <div className="card"><div className="muted small">退回</div><div style={{ fontSize: 22, color: "var(--red)", fontWeight: 700 }}>{cnt((x) => label(x) === "退回")} 段</div></div>
        <div className="card"><div className="muted small">待判断 / 待审</div><div style={{ fontSize: 22, color: "var(--amber)", fontWeight: 700 }}>{cnt((x) => !x.ok && label(x) !== "退回")} 段</div></div>
      </div>
      <div className="muted small" style={{ margin: "10px 0" }}>通过：两遍都没有问题 · 退回：必须修改 · 待判断：改，或由你写理由放行（记为“用户放行”，不记为通过）</div>
      {rows.map(([seg, x]: any) => { const ref = current.content.segments[seg]?.refs?.[0]; const a = ctx.assets.find((y) => y.id === ref?.assetVersionId);
        const issues = [...(x.coldRead.issues ?? []), ...(x.comparison.issues ?? [])]; const l = label(x);
        return (
          <div key={seg} className={`card ${l === "退回" ? "red" : ""}`} style={{ marginBottom: 10 }}>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <img src={blob(a?.sha256)} style={{ width: 70, height: 40, objectFit: "cover", borderRadius: 6 }} />
              <div style={{ flex: 1 }}><b>{seg}</b><div className="muted tiny">第一遍 · 冷读</div><div className="small">{x.coldRead.state === "needed" ? "待审" : x.coldRead.observation ?? DEC[x.coldRead.decision]}</div></div>
              <div style={{ flex: 1 }}><div className="muted tiny">第二遍 · 对照</div><div className="small">{x.comparison.state === "needed" ? "待审（含相邻接点）" : x.comparison.observation ?? DEC[x.comparison.decision]}</div></div>
              <Tag tone={l === "通过" || l === "用户放行" ? "green" : l === "退回" ? "red" : "amber"}>{l}</Tag>
            </div>
            {issues.map((i: any) => (
              <div key={i.id} className="small" style={{ marginTop: 8 }}><Tag tone={i.blocking ? "red" : "amber"}>{i.blocking ? "必须修复" : "语义意见"}</Tag> {i.reason}
                {i.waiver_reason ? <span className="ok"> · 已放行：{i.waiver_reason}</span> : !i.blocking && <span className="row" style={{ marginTop: 4 }}>
                  <input placeholder="写理由放行（绑定本段输入，改动后失效）" value={reasons[i.id] ?? ""} onChange={(e) => setReasons({ ...reasons, [i.id]: e.target.value })} style={{ flex: 1 }} />
                  <button className="btn" onClick={() => act.run(async () => { await cmd("waive_segment_issue", { issue_id: i.id, reason: reasons[i.id] ?? "" }); st.reload(); })}>放行</button></span>}
              </div>))}
          </div>); })}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 09 发布包 (J42)
function PublishView({ ctx }: { ctx: Ctx }) {
  const { art, current } = useVersion(ctx, "publish");
  const [form, setForm] = useState<any>(null);
  const composed = useQuery<any>(current ? "compose_publish" : null, { episode_id: ctx.eid, version_id: current?.id }, [current?.id]);
  const act = useAction();
  useEffect(() => { setForm(current?.content ?? { title: "", body: "", tags: [], suno_title: "", suno_style: "", suno_exclude: "", cues: [] }); }, [current?.id]);
  if (!art) return <div className="empty">提示词采用后编写发布包。</div>;
  if (!form) return null;
  const copy = (t: string) => navigator.clipboard.writeText(t).catch(() => alert("复制失败，请手动选取原文"));
  const f = composed.data?.fields;
  return (
    <div>
      <div className="stagehead"><div><h1>发布包</h1><div className="muted small">六字段独立复制 · 音乐状态 · 对位表 · 前缀、链接与标签顺序读取本项目设置</div></div>
        {current && <VersionTabs ctx={ctx} art={art} current={current} />}</div>
      <div className="grid2">
        <div className="col">
          {[["title", "发布标题"], ["body", "正文第一行"], ["suno_title", "Suno Title"], ["suno_style", "Style of Music"], ["suno_exclude", "Exclude Styles"]].map(([k, l]) => (
            <label key={k} className="col" style={{ gap: 4 }}><span className="muted small">{l}</span><input value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></label>))}
          <label className="col" style={{ gap: 4 }}><span className="muted small">本集话题标签（逗号分隔）</span><input value={(form.tags ?? []).join(",")} onChange={(e) => setForm({ ...form, tags: e.target.value.split(/[,，]/).map((x) => x.trim()).filter(Boolean) })} /></label>
          <button className="btn primary" onClick={() => act.run(async () => { const r = await cmd("save_manual", { artifact_id: art.id, base_version_id: current?.id ?? null, content: form }); ctx.setFocus(r.versionId); ctx.reload(); })}>保存为新版本</button>
        </div>
        <div className="col">
          {f ? Object.entries({ 发布标题: f.title, 三行正文: f.body, 话题标签: f.tags, "Suno Title": f.suno_title, "Style of Music": f.suno_style, "Exclude Styles": f.suno_exclude }).map(([l, v]) => (
            <div key={l} className="card"><div className="spread"><span className="muted small">{l}</span><button className="btn ghost" onClick={() => copy(v as string)}>复制</button></div><div className="small" style={{ whiteSpace: "pre-wrap", userSelect: "all" }}>{v as string}</div></div>))
            : <div className="error small">{composed.error ?? "保存后预览拼接后的平台文案"}</div>}
          {composed.data && <div className="card"><div className="spread"><span className="muted small">音乐</span><Tag tone={composed.data.music === "启用" ? "green" : ""}>{composed.data.music}</Tag></div>
            <div className="muted small" style={{ marginTop: 6 }}>英文交付标题：{composed.data.englishTitle}</div>
            {composed.data.cues.map((c: any, i: number) => <div key={i} className="small">{c.at} · {c.action}</div>)}</div>}
        </div>
      </div>
      <ErrorLine error={act.error} />
      {current && <AdoptBar ctx={ctx} art={art} version={current} next="delivery" hint="采用后进入交付核验" />}
    </div>
  );
}

// ---------------------------------------------------------------- 10 交付核验 (J12, J19, J55)
function DeliveryView({ ctx }: { ctx: Ctx }) {
  const [scope, setScope] = useState<"full" | "trial">("full");
  const [segs, setSegs] = useState<string[]>([]);
  const problems = useQuery<any>("delivery_problems", { episode_id: ctx.eid, scope, segments: scope === "trial" ? segs : null }, [scope, segs.join()]);
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const [verify, setVerify] = useState<Record<string, any>>({});
  const act = useAction();
  const plan = artifactFor(ctx.view, "plan");
  const lay = layout(plan?.versions.find((v: any) => v.id === plan.adopted_version_id)?.content);
  const load = async () => {
    const recs = await q("task_records", { episode_id: ctx.eid }).catch(() => []);
    void recs;
    const ov = await q("project_overview", { project_id: ctx.view.episode.project_id });
    const ep = ov.episodes.find((e: any) => e.id === ctx.eid);
    setDeliveries(await Promise.all((ep?.deliveries ?? []).map((d: any) => q("delivery_view", { delivery_id: d.id }))));
  };
  useEffect(() => { load(); }, [ctx.eid]);
  const list = problems.data?.[0] ?? [];
  const byStage: Record<string, any[]> = {};
  list.forEach((p: any) => { (byStage[p.stage] ??= []).push(p); });
  return (
    <div>
      <div className="stagehead"><div><h1>交付核验</h1><div className="muted small">核验实际读取文件、指纹和内容；只有核验通过且文件完整落盘才登记交付号</div></div>
        <Pills value={scope} onChange={setScope} options={[["full", "整集完整交付"], ["trial", "限定试验范围"]]} /></div>
      {scope === "trial" && <div className="row">{lay.segments.map((s: any) => <button key={s.id} className={`pill ${segs.includes(s.id) ? "on" : ""}`} onClick={() => setSegs(segs.includes(s.id) ? segs.filter((x) => x !== s.id) : [...segs, s.id])}>{s.id}</button>)}<span className="muted small">试验范围从零计时，不更新整集完成状态</span></div>}
      {list.length > 0 ? (
        <div className="card red" style={{ marginTop: 14 }}>
          <b className="error">结构阻断 · {list.length} 项</b><span className="muted small"> · 不能放行，逐条定位到出错的阶段</span>
          {Object.entries(byStage).map(([stg, ps]) => (
            <div key={stg} style={{ marginTop: 10 }}><div className="row"><Tag tone="red">{stg}</Tag><a className="btn ghost" href={`#/e/${ctx.eid}/${stg === "recheck" || stg === "review" ? "review" : stg === "assets" ? "assets" : stg === "space" ? "space" : stg}`}>去修复</a></div>
              {ps.slice(0, 8).map((p: any, i: number) => <div key={i} className="small" style={{ margin: "3px 0" }}>{p.segment ?? ""} {p.file ? `· ${p.file}` : ""} · {p.problem}</div>)}</div>))}
        </div>) : problems.data && <div className="card" style={{ marginTop: 14, background: "#13261f" }}><Tag tone="green">核验通过</Tag> <span className="small">可以写入交付目录（先写临时目录，校验后改名）。</span></div>}
      <button className="btn primary" style={{ marginTop: 12 }} disabled={list.length > 0 || act.busy} onClick={() => act.run(async () => { await cmd("deliver", { episode_id: ctx.eid, scope, segments: scope === "trial" ? segs : null }); await load(); ctx.reload(); problems.reload(); })}>生成图文交付</button>
      <h2 style={{ marginTop: 24 }}>已登记的交付</h2>
      {deliveries.length === 0 && <div className="muted">尚无。</div>}
      {deliveries.map((d) => (
        <div key={d.id} className="card line" style={{ marginBottom: 10 }}>
          <div className="spread"><span><Tag tone={d.scope === "full" ? "green" : "blue"}>{d.scope === "full" ? "整集" : "试验"}</Tag> 交付号 <span className="mono">{d.id.slice(0, 8)}</span> · {d.segments.join("、")} · {Object.keys(d.files).length} 个文件</span>
            <div className="row"><a className="btn" target="_blank" href={deliveryFile(d.id, "00_图文对照.html")}>打开图文页</a>
              <button className="btn" onClick={() => navigator.clipboard.writeText(d.html)}>复制 HTML 绝对路径</button>
              <button className="btn" onClick={() => act.run(async () => setVerify({ ...verify, [d.id]: await q("verify_delivery", { delivery_id: d.id }) }))}>离线验包</button>
              {d.scope === "full" && <a className="btn primary" href={`#/p/${ctx.view.episode.project_id}/closure-start/${d.id}`}>整理本集设定</a>}</div></div>
          <div className="muted tiny mono" style={{ marginTop: 6 }}>{d.html}</div>
          {verify[d.id] && <div className={verify[d.id].ok ? "ok small" : "error small"}>{verify[d.id].ok ? "验包通过：文件与指纹一致" : verify[d.id].errors.map((e: any) => `${e.file} ${e.problem}`).join("；")}</div>}
        </div>))}
      <ErrorLine error={act.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 11 H3 出包 (J12)
function PackageView({ ctx }: { ctx: Ctx }) {
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const [did, setDid] = useState("");
  const [segs, setSegs] = useState<string[]>([]);
  const [params, setParams] = useState<any>({ resolution: "default", audio: true, candidates: 1, instanceType: "default", seed: "" });
  const [out, setOut] = useState<any>(null);
  const act = useAction();
  useEffect(() => { (async () => {
    const ov = await q("project_overview", { project_id: ctx.view.episode.project_id });
    const ds = await Promise.all((ov.episodes.find((e: any) => e.id === ctx.eid)?.deliveries ?? []).map((d: any) => q("delivery_view", { delivery_id: d.id })));
    setDeliveries(ds); if (ds[0]) { setDid(ds[ds.length - 1].id); setSegs(ds[ds.length - 1].segments); }
  })(); }, [ctx.eid]);
  const d = deliveries.find((x) => x.id === did);
  return (
    <div>
      <div className="stagehead"><div><h1>H3 出包</h1><div className="muted small">交给本地打包器离线导出 ZIP；更换清单、素材或范围后重新验包</div></div></div>
      {!d ? <div className="muted">先完成图文交付。</div> : <>
        <div className="row"><select value={did} onChange={(e) => setDid(e.target.value)}>{deliveries.map((x) => <option key={x.id} value={x.id}>{x.scope === "full" ? "整集" : "试验"} {x.id.slice(0, 8)}</option>)}</select>
          <span className="muted small mono">{d.html}</span></div>
        <div className="row" style={{ marginTop: 12 }}>{d.segments.map((s: string) => <button key={s} className={`pill ${segs.includes(s) ? "on" : ""}`} onClick={() => setSegs(segs.includes(s) ? segs.filter((x) => x !== s) : [...segs, s])}>{s}</button>)}</div>
        <div className="grid3" style={{ marginTop: 12 }}>
          <label className="col small">分辨率<select value={params.resolution} onChange={(e) => setParams({ ...params, resolution: e.target.value })}>{["default", "480p", "768p", "1080p"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="col small">候选<select value={params.candidates} onChange={(e) => setParams({ ...params, candidates: +e.target.value })}>{[1, 2, 4].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="col small">档位<select value={params.instanceType} onChange={(e) => setParams({ ...params, instanceType: e.target.value })}>{["default", "plus", "ultra"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="col small">声音<select value={params.audio ? "on" : "off"} onChange={(e) => setParams({ ...params, audio: e.target.value === "on" })}><option value="on">开</option><option value="off">关</option></select></label>
          <label className="col small">种子（空为随机，0–9007199254740987）<input value={params.seed} onChange={(e) => setParams({ ...params, seed: e.target.value })} /></label>
        </div>
        <button className="btn primary" style={{ marginTop: 12 }} disabled={!segs.length || act.busy} onClick={() => act.run(async () => {
          const overrides: any = { resolution: params.resolution, audio: params.audio, candidates: params.candidates, instanceType: params.instanceType };
          if (params.seed !== "") overrides.seed = parseInt(params.seed, 10);
          setOut(await cmd("package", { delivery_id: did, segments: segs, overrides }));
        })}>验包并导出 ZIP</button>
        {out && <div className="card" style={{ marginTop: 12, background: "#13261f" }}><b className="ok">已导出</b> <span className="mono small">{out.path}</span>
          <div className="small" style={{ marginTop: 6 }}>project.json schemaVersion {out.project.schemaVersion} · {out.project.scope} · {out.project.segments.length} 段 · {out.project.assets.length} 项素材</div>
          {out.project.segments.map((s: any) => <div key={s.id} className="muted tiny">{s.id} · {Object.entries(s.params).map(([k, v]) => `${k}=${v}`).join(" ")}</div>)}</div>}
      </>}
      <ErrorLine error={act.error} />
    </div>
  );
}

function charDiff(a: string, b: string): { op: string; text: string }[] {
  const n = a.length, m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { op: string; text: string }[] = [];
  const push = (op: string, ch: string) => { const last = out[out.length - 1]; if (last && last.op === op) last.text += ch; else out.push({ op, text: ch }); };
  let i = 0, j = 0;
  while (i < n && j < m) { if (a[i] === b[j]) { push("same", a[i]); i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) { push("del", a[i]); i++; } else { push("add", b[j]); j++; } }
  while (i < n) push("del", a[i++]);
  while (j < m) push("add", b[j++]);
  return out;
}
