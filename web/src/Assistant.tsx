/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { cmd, q } from "./api";
import { Avatars, ErrorLine, PROVIDER_LABEL, Tag, rno, useAction } from "./ui";

export type Selection = { label: string; data: Record<string, any> } | null;

const STAGE_ART: Record<string, string> = { story: "story", script: "script", plan: "plan", space: "plan", cast: "plan",
  assets: "plan", prompts: "prompts", review: "prompts", publish: "publish", delivery: "publish", package: "publish" };

export function artifactFor(view: any, stage: string) {
  return view.artifacts.find((a: any) => a.stage === STAGE_ART[stage]);
}

/** 导演助手: clarification, progress, draft, review and image-batch cards, plus the composer. */
export default function Assistant({ view, stage, sel, setSel, reload, focus, batches, onOpenBatch }: {
  view: any; stage: string; sel: Selection; setSel: (s: Selection) => void; reload: () => void; focus?: string | null;
  batches: any[]; onOpenBatch: (b: any) => void;
}) {
  const art = artifactFor(view, stage);
  const task = art?.tasks?.filter((t: any) => t.goal !== "两遍独审").slice(-1)[0];
  const [text, setText] = useState(() => sessionStorage.getItem(`draft:${view.episode.id}:${stage}`) ?? "");
  const [provider, setProvider] = useState<string>(view.kickoff?.executor_plan?.create?.provider ?? "claude");
  const [choice, setChoice] = useState<string | null>(null);
  const [impact, setImpact] = useState<any>(null);
  const act = useAction();
  useEffect(() => { try { sessionStorage.setItem(`draft:${view.episode.id}:${stage}`, text); } catch { /* ignore */ } }, [text]);
  const draftV = art?.versions?.filter((v: any) => v.id !== art.adopted_version_id).slice(-1)[0];
  const focusV = art?.versions?.find((v: any) => v.id === focus) ?? draftV;
  useEffect(() => {
    if (focusV && focusV.id !== art?.adopted_version_id) q("adoption_preview", { version_id: focusV.id }).then(setImpact).catch(() => setImpact(null));
    else setImpact(null);
  }, [focusV?.id, art?.adopted_version_id]);
  const open = task && !["closed", "abandoned"].includes(task.state);
  const send = () => act.run(async () => {
    if (!text.trim()) return;
    if (!art) throw new Error("本阶段还没有成果，请先采用上一步");
    if (open) {
      await cmd("post_message", { task_id: task.id, text, selection: sel?.data ?? null, base_version_id: focusV?.id ?? art.adopted_version_id ?? undefined });
    } else {
      await cmd("create_task", { episode_id: view.episode.id, stage: art.stage, goal: text, executor: provider !== view.kickoff?.executor_plan?.create?.provider ? { create: { provider } } : null, selection: sel?.data ?? null });
    }
    setText(""); setSel(null); reload();
  });
  const runs = task?.runs ?? [];
  const live = runs.filter((r: any) => ["queued", "running"].includes(r.status));
  return (
    <aside className="assistant">
      <div className="head">导演助手 <Avatars /><span className="muted tiny" style={{ marginLeft: "auto", textAlign: "right" }}>{task ? `任务：${task.goal.slice(0, 14)}` : "尚无任务"}{task ? <><br />自动修订 {task.used_rounds}/{task.max_rounds} 轮</> : null}</span></div>
      <div className="msgs">
        {(task?.messages ?? []).map((m: any) => <Message key={m.id} m={m} task={task} reload={reload} art={art} />)}
        {live.map((r: any) => (
          <div key={r.id} className="mcard">
            <div className="msg"><div className="who"><i className="prog" />程序 · 进度</div></div>
            <div className="small">{PROVIDER_LABEL[r.provider] ?? r.provider} 正在{r.purpose === "review" ? "独立审查（另开会话）" : r.purpose === "cold_read" ? "冷读" : r.purpose === "comparison" ? "对照审查" : "起草"}… <Tag tone="blue">{r.status}</Tag></div>
            <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => act.run(async () => { await cmd("stop_run", { run_id: r.id }); reload(); })}>停止</button><span className="muted tiny">停止后保留已保存的部分；未返回的请求按任务状态恢复</span></div>
          </div>))}
        {runs.filter((r: any) => r.status === "waiting_quota").map((r: any) => (
          <div key={r.id} className="mcard focus">
            <div className="small"><Tag tone="amber">等待额度</Tag> {PROVIDER_LABEL[r.provider]} 额度用尽{r.quota_resets_at ? `，预计 ${r.quota_resets_at} 恢复` : ""}。不会自动切换。</div>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id }); reload(); })}>等待后重试</button>
              {["codex", "claude", "antigravity"].filter((p) => p !== r.provider).map((p) => (
                <button key={p} className="btn" onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id, provider: p }); reload(); })}>换 {PROVIDER_LABEL[p]}</button>))}
            </div>
          </div>))}
        {runs.filter((r: any) => r.status === "failed").slice(-1).map((r: any) => (
          <div key={r.id} className="mcard"><div className="small error">{PROVIDER_LABEL[r.provider]} 运行失败：{r.reason}{r.diagnostic ? ` · ${r.diagnostic.slice(0, 120)}` : ""}</div>
            <button className="btn" style={{ marginTop: 8 }} onClick={() => act.run(async () => { await cmd("retry_run", { run_id: r.id }); reload(); })}>重试</button></div>))}
        {art?.stage === "story" && draftV && !draftV.gate.review && !live.length && (
          <div className="mcard">
            <div className="small muted">故事稿 {rno(draftV.display_no)} 已完成</div>
            <b>下一步？</b>
            {[["review", "发起独立审查（推荐）", `${PROVIDER_LABEL[view.kickoff?.executor_plan?.review?.provider] ?? ""} 另开会话，只拿故事稿和项目设定冷读`],
              ["self", "我先自己改几处", "改完再送审"], ["both", "让两个模型各写一版再对比", "会多消耗一次额度"]].map(([k, t, d]) => (
              <div key={k} className={`opt ${choice === k ? "on" : ""}`} onClick={() => setChoice(k)}><span className="radio" /><div><div>{t}</div><div className="muted tiny">{d}</div></div></div>))}
            <button className="btn primary" disabled={!choice} onClick={() => act.run(async () => {
              if (choice === "review") await cmd("submit_review", { version_id: draftV.id, task_id: task?.id });
              if (choice === "both") for (const p of ["codex", "claude"]) await cmd("create_task", { episode_id: view.episode.id, stage: "story", goal: task?.goal ?? "并行出稿", executor: { create: { provider: p } } });
              reload();
            })}>提交</button>
          </div>)}
        {impact && focusV && impact.items.length > 0 && (
          <div className="mcard focus">
            <div className="small" style={{ marginBottom: 6 }}>采用 {rno(focusV.display_no)} 会影响</div>
            {impact.items.map((it: any, i: number) => <div key={i} className="impact"><span>{it.target} · <span className="muted">{it.reason}</span></span><Tag tone={it.kind === "auto" ? "green" : "amber"}>{it.kind === "auto" ? "自动更新" : "需复核"}</Tag></div>)}
            {impact.unaffected?.length > 0 && <div className="muted tiny" style={{ marginTop: 6 }}>不受影响：{impact.unaffected.join("、")}。已采用的版本保留在历史里。</div>}
          </div>)}
        {(stage === "assets" ? batches : batches.filter((b) => ["awaiting_authorization", "pending_reconcile"].includes(b.state))).map((b) => (
          <div key={b.id} className="mcard">
            <div className="spread small"><span>图像批次 · {b.provider} · {b.operation === "edit" ? "局部重画" : "生成"}</span><Tag tone={b.state === "completed" ? "green" : b.state === "pending_reconcile" ? "amber" : "blue"}>{BATCH_STATE[b.state] ?? b.state}</Tag></div>
            <div className="muted tiny" style={{ margin: "6px 0" }}>{b.count} 张 · {b.size} · 预算 {b.budget_cents != null ? `¥${(b.budget_cents / 100).toFixed(2)}` : "未填"} · 费用 {b.cost_state === "unknown" ? "未知" : `估算 ¥${((b.estimate_cents ?? 0) / 100).toFixed(2)}`}</div>
            <button className="btn" onClick={() => onOpenBatch(b)}>{b.state === "awaiting_authorization" ? "确认、调整或取消" : "打开批次"}</button>
          </div>))}
      </div>
      <div className="composer">
        {sel && <div className="selchip">选区：{sel.label} <span style={{ cursor: "pointer" }} onClick={() => setSel(null)}>×</span></div>}
        <textarea placeholder="输入你的想法，继续完善这一集" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }} />
        <div className="row small">
          <span className="muted">＋</span><span className="muted" title="short-film@v1">⚒ 技能</span>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} style={{ padding: "2px 6px" }}>
            {["claude", "codex", "antigravity"].map((p) => <option key={p} value={p}>✦ {PROVIDER_LABEL[p]}</option>)}</select>
          {open && <button className="btn ghost" onClick={() => act.run(async () => { await cmd("abandon_task", { task_id: task.id }); reload(); })}>新任务</button>}
          <span className="muted tiny" style={{ marginLeft: "auto" }}>{text.length}/500字</span>
          <button className="btn primary" style={{ borderRadius: "50%", width: 28, height: 28, padding: 0 }} onClick={send} disabled={act.busy}>↑</button>
        </div>
        <ErrorLine error={act.error} />
      </div>
    </aside>
  );
}

const BATCH_STATE: Record<string, string> = { awaiting_authorization: "待确认", authorized: "已授权", completed: "完成", partial: "部分待发",
  pending_reconcile: "待核对", partially_succeeded: "部分成功", failed: "失败", cancelled: "已取消" };

function Message({ m, task, reload, art }: { m: any; task: any; reload: () => void; art: any }) {
  const act = useAction();
  const payload = m.payload ? JSON.parse(m.payload) : null;
  if (m.role === "user") return <div className="msg user">{m.text}{m.selection && <div className="muted tiny">选区 {Object.values(JSON.parse(m.selection)).join(" · ")}</div>}</div>;
  if (m.kind === "clarify") return (
    <div className="mcard focus">
      <div className="msg"><div className="who"><i className="codex" />澄清</div></div>
      <div>{m.text}</div>
      {task.state === "clarifying" && <div className="row" style={{ marginTop: 8 }}>{payload.options.map((o: string) => (
        <button key={o} className="btn" onClick={() => act.run(async () => { await cmd("answer_clarification", { task_id: task.id, answer: o }); reload(); })}>{o}</button>))}</div>}
      {payload.relatedIds?.length > 0 && <div className="muted tiny">关联：{payload.relatedIds.join("、")}</div>}
      <ErrorLine error={act.error} />
    </div>);
  if (m.kind === "draft") {
    const v = art?.versions.find((x: any) => x.id === payload.versionId);
    return (
      <div className="mcard">
        <div className="spread small"><span>{art?.stage === "story" ? "故事稿" : art?.stage === "script" ? "剧本" : "成果"} {v ? rno(v.display_no) : ""} 草稿 · {PROVIDER_LABEL[payload.provider] ?? payload.provider}</span>
          <Tag tone={art?.adopted_version_id === payload.versionId ? "green" : "amber"}>{art?.adopted_version_id === payload.versionId ? "已采用" : "待采用"}</Tag></div>
        <div className="small" style={{ margin: "6px 0" }}>{m.text}{payload.baseVersionId ? `（基于 ${rno(art?.versions.find((x: any) => x.id === payload.baseVersionId)?.display_no)}）` : ""}</div>
        <div className="row"><a className="btn" href={`#${location.hash.split("#")[1].split("?")[0]}?v=${payload.versionId}`}>定位</a></div>
      </div>);
  }
  if (m.role === "system") return (
    <div className="mcard"><div className="small">{m.text.startsWith("独立审查") ? <>独立审查 · 另开会话 <Tag tone={m.text.includes("pass") ? "green" : "red"}>{m.text.includes("pass") ? "通过" : "退回"}</Tag></> : m.text}</div>
      <div className="muted tiny" style={{ marginTop: 4 }}>{m.text}</div></div>);
  return <div className="msg"><div className="who"><i className="codex" />助手</div><div>{m.text}</div></div>;
}
