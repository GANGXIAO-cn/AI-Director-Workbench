import { useProjectRoute } from "./projectNavigation";
import { useEffect, useState } from "react";
import { cmd, deliveryFile } from "./api";
import { ErrorLine, Modal, PROVIDER_LABEL, STAGES, pickable, Tag, rno, useAction, usePoll, useQuery } from "./ui";

const stages = ["story", "script", "plan", "prompts", "publish", "delivery"];
const names = Object.fromEntries(STAGES);
const fixed = ["出图内容须批准（可批量）", "自动修订满两轮", "结构阻断", "两模型结论冲突", "请求结果未知",
  "违反必须守住的设定", "项目设定新增或修改", "审核隔离未验证或执行器额度不足"];
const optional = [["confirm_story", "故事稿给我看"], ["confirm_plan", "制作方案给我看"], ["confirm_delivery", "交付核验前给我看"]];
const states: Record<string, string> = { authorized: "待开始", running: "运行中", stopped: "等待导演处理", manual: "已切回手工", done: "已完成授权范围" };
type Episode = { id: string; display_code: string; kickoff_id?: string };
type Event = { episode: string; stage: string; completed?: boolean; number?: number; reused?: boolean; deliveryId?: string };
type Start = { code: string; versions: Record<string, { id: string; number: number }> };

function Authorize({ id, episodes, pre, close, started }: { id: string; episodes: Episode[]; pre?: string;
  close: () => void; started: (aid: string) => void }) {
  const [eps, setEps] = useState<string[]>(pre ? [pre] : episodes.length === 1 ? [episodes[0].id] : []);
  const [target, setTarget] = useState("delivery");
  const [stops, setStops] = useState(["confirm_story", "confirm_delivery"]);
  const [division, setDivision] = useState<Record<string, string>>({});
  const preview = useQuery<Record<string, Start>>("expert_preview", { project_id: id, episodes: eps });
  const act = useAction();
  const ready = eps.length > 0 && eps.every(e => preview.data?.[e]) && Object.keys(preview.data ?? {}).length === eps.length;
  const authorize = async () => {
    const overrides = Object.fromEntries(Object.entries(division).filter(([, p]) => p).map(([r, p]) => [r, { provider: p }]));
    const a = await cmd("authorize_expert", { project_id: id, episodes: eps, stop_stage: target, stops, division: overrides,
      expected: preview.data });
    started(a.authId);
  };
  return <Modal onClose={close} width={640}><div className="expert-form">
    <div className="spread"><h2>授权专家模式</h2><Tag tone="cyan">授权后开始执行</Tag></div>
    <section className="card"><b>范围</b><div className="formrow"><span>具体集</span><div className="row">{episodes.map(e =>
      <button key={e.id} className={`pill ${eps.includes(e.id) ? "on" : ""}`} onClick={() => setEps(eps.includes(e.id) ? eps.filter(x => x !== e.id) : [...eps,e.id])}>{e.display_code}</button>)}
      {!episodes.length && "请先为一集开工"}</div></div>
      <div className="formrow"><span>从</span><div>{eps.map(e => {
        const start = preview.data?.[e]; const stage = stages.filter(s => start?.versions[s]).pop();
        return <div key={e}>{start?.code} · {stage && start ? names[stage] + " " + rno(start.versions[stage].number) + "（已采用）" : "开工材料"}</div>;
      })}</div></div>
      <label className="formrow"><span>做到</span><select value={target} onChange={e => setTarget(e.target.value)}>
        {stages.map(s => <option key={s} value={s}>{s === "delivery" ? "图文可交付（含发布包、交付核验）" : names[s] + "采用"}</option>)}</select></label></section>
    <section className="card"><b>停点</b><div className="label">你可以加的确认点</div>
      {optional.map(([key,label]) => <label key={key}><input type="checkbox" checked={stops.includes(key)}
        onChange={e => setStops(e.target.checked ? [...stops,key] : stops.filter(k => k !== key))} /> {label}</label>)}
      <div className="label">程序守住的停点</div>{fixed.map(x => <div key={x} className="small muted">☑ {x}</div>)}</section>
    <section className="card"><b>执行与分工</b>{[["create","起草与修订"],["review","独立审核"]].map(([role,label]) =>
      <label key={role} className="formrow"><span>{label}</span><select value={division[role] ?? ""} onChange={e => setDivision({...division,[role]:e.target.value})}>
        <option value="">按本集开工设置</option>{Object.entries(PROVIDER_LABEL).filter(([p]) => pickable(["codex","claude","antigravity"]).includes(p)).map(([p,l]) => <option key={p} value={p}>{l}</option>)}</select></label>)}</section>
    <div className="row" style={{justifyContent:"flex-end"}}><button className="btn" onClick={close}>取消</button>
      <button className="btn primary" disabled={!ready || act.busy}
        onClick={() => act.run(authorize)}>授权并开始</button></div>
    <ErrorLine error={act.error} />{act.error && <button className="btn" onClick={preview.reload}>刷新范围</button>}{preview.error && <div className="error">{preview.error}</div>}
  </div></Modal>;
}

export default function Expert({ id, episodes, arg }: { id: string; episodes: Episode[]; arg?: string }) {
  const { hash, go } = useProjectRoute();
  const pre = new URLSearchParams(hash.split("?")[1]).get("episode") ?? undefined;
  const history = useQuery<{id:string;state:string;stop_stage:string;created_at:number}[]>("expert_history", {project_id:id, episode_id:pre}, [arg]);
  const active = arg ?? history.data?.[0]?.id;
  const status = useQuery(active ? "expert_view" : null, {auth_id:active});
  const a = status.data && status.data.id === active && status.data.project_id === id ? status.data : null;
  usePoll(a?.state === "running" || a?.state === "authorized", status.reload);
  const [form, setForm] = useState<boolean | null>(null);
  const act = useAction();
  const showForm = form ?? (!arg && !!history.data && !active);
  const start = async (aid: string, token?: string) => {
    await cmd("expert_run", {auth_id:aid, resume_token:token});
    await status.reload();
  };
  const selected = a?.cursor?.episode ?? a?.episodes[0];
  const page = a?.page ?? a?.stage ?? a?.cursor?.stage ?? "story";
  const params = new URLSearchParams();
  if (active) params.set("expert", active);
  if (a?.batchId) params.set("batch", a.batchId);
  if (a?.versionId) params.set("v", a.versionId);
  const origin = page === "preset" ? `#/p/${id}/preset` : `#/e/${a?.episode ?? selected}/${page}?${params}`;
  const [handoff, setHandoff] = useState(false);
  useEffect(() => {
    if (handoff && a && ["manual", "done"].includes(a.state)) {
      setHandoff(false);
      go(origin);
    }
  }, [handoff, a, origin]);
  const completed = (a?.log ?? []).filter((x: Event) => x.completed);
  const range = stages.slice(0, stages.indexOf(a?.stop_stage) + 1);
  return <div className="expert-page"><div className="spread"><h1>专家模式{selected && a?.baseline ? " · " + a.baseline[selected].code : ""}</h1>
    <button className="btn" onClick={() => setForm(true)}>新授权</button></div>
    {!!history.data?.length && <label className="row small muted">授权记录 <select aria-label="授权记录" value={active}
      onChange={e => {setForm(false); go(`#/p/${id}/expert/${e.target.value}`);}}>{history.data.map(h =>
      <option key={h.id} value={h.id}>{new Date(h.created_at*1000).toLocaleString()} · 到{names[h.stop_stage]}</option>)}</select></label>}
    {a && <><div className="spread"><div><b>{a.baseline ? states[a.state] : "历史授权（未记录起始版本）"}</b>
      {a.baseline && <div className="small muted">{completed.length} / {a.episodes.length * range.length} 个阶段</div>}</div>
      <div className="row">{a.state === "running" && <button className="btn" disabled={act.busy || a.pause_requested}
        onClick={() => act.run(async () => {await cmd("pause_expert",{auth_id:active}); await status.reload();})}>{a.pause_requested ? "等待当前步骤结束" : "暂停"}</button>}
      {a.state === "authorized" && a.baseline && <button className="btn primary" onClick={() => act.run(() => start(a.id))}>开始本次授权</button>}
      {selected && ["running","stopped"].includes(a.state) && <button className="btn" disabled={act.busy || handoff}
        onClick={() => act.run(async () => {await cmd("manual_expert",{auth_id:a.id}); setHandoff(true); await status.reload();})}>{handoff ? "正在切换…" : "切回手工"}</button>}</div></div>
      {a.baseline && <progress className="expert-progress" aria-label="授权进度" value={completed.length} max={a.episodes.length * range.length} />}
      {["stopped","manual"].includes(a.state) && <div className="card line"><b>{a.detail ?? a.reason}</b>
        <div className="row" style={{marginTop:8}}>{selected && <a className="btn" href={origin}>去原位置处理</a>}
        {a.reason === "settings_change" || !a.baseline ? <button className="btn primary" onClick={() => setForm(true)}>重新授权</button> :
          <button className="btn primary" disabled={act.busy} onClick={() => act.run(() => start(a.id,a.token))}>{a.checkpoint ? "确认并继续" : "重新核对并恢复"}</button>}</div></div>}
      {a.baseline && <div className="expert-grid"><div className="col">{a.episodes.map((eid: string) => <section key={eid} className="expert-steps"><b>{a.baseline[eid].code}</b>
        {range.map(stage => {const done = completed.find((x: Event) => x.episode===eid && x.stage===stage);
          const current = !done && a.cursor?.episode===eid && a.cursor?.stage===stage;
          return <div className={`card spread ${current ? "cyan" : ""}`} key={stage}><span>{done ? "✓" : current ? "●" : "○"}　{names[stage]}</span>
            {done?.deliveryId ? <a href={deliveryFile(done.deliveryId,"00_图文对照.html")} target="_blank" rel="noreferrer">查看已核验图文 ↗</a> :
              <span className="muted small">{done ? (done.reused ? "沿用采用版 " : "已采用 ") + rno(done.number) : current ? states[a.state] : "待进行"}</span>}</div>;
        })}</section>)}</div><aside className="expert-aside"><h2>执行记录</h2>
          {a.log.filter((x: {resumed?: string}) => x.resumed).map((x: {checkpoint?:string;stage:string;episode:string},i:number) =>
            <div className="muted small" key={i}>{a.baseline[x.episode]?.code} · {names[x.stage]} · {x.checkpoint ? "导演已确认" : "已重新核对"}</div>)}
          <a className="btn ghost" href="#/sys/tasks">查看执行器任务记录</a>
          <div className="card"><b>不会越过的停点</b>{fixed.map(x => <div key={x} className="muted small">· {x}</div>)}</div>
        </aside></div>}</>}
    <ErrorLine error={act.error} />{(history.error || status.error) && <div className="error">{history.error || status.error}</div>}
    {showForm && <Authorize id={id} episodes={episodes.filter(e => e.kickoff_id)} pre={pre} close={() => setForm(false)}
      started={aid => {setForm(false); go(`#/p/${id}/expert/${aid}`); act.run(() => start(aid));}} />}
  </div>;
}
