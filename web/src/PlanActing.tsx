import { useState } from "react";
import { blob, cmd } from "./api";
import { ErrorLine, useAction, useQuery } from "./ui";

type Props = {
  version: any; episodeId: string; assets: any[];
  onSave: (shots: any[]) => Promise<void>;
};
const seconds = (ms: number) => (ms / 1000).toLocaleString("zh-CN", {maximumFractionDigits: 3});

function SecondsInput({title, value, onChange}: {title: string; value?: number; onChange: (v?: number) => void}) {
  return <label className="col small">{title}<input type="number" min="0" step="0.001" value={value == null ? "" : value / 1000}
    onChange={e => onChange(e.target.value === "" ? undefined : Math.round(Number(e.target.value) * 1000))} /></label>;
}

export function PlanActing({version, episodeId, assets, onSave}: Props) {
  const shots: any[] = version.content.shots ?? [];
  const [id, setId] = useState(shots[0]?.id ?? "");
  const [changes, setChanges] = useState<Record<string, any>>({});
  const [preview, setPreview] = useState<{changes: object; report: any} | null>(null);
  const saved = useQuery<any>("acting_review", {version_id:version.id});
  const cast = useQuery<any>("cast_matrix", {episode_id:episodeId});
  const act = useAction();
  if (!shots.length) return <p>本版尚无镜头。</p>;
  const shot = {...shots.find(s => s.id === id), ...changes[id]};
  const update = (edit: any) => setChanges({...changes, [id]: {...changes[id], ...edit}});
  const cues: any[] = Array.isArray(shot.dialogueCues) ? shot.dialogueCues : [];
  const editCue = (i: number, edit: any) => update({dialogueCues:cues.map((c, n) => n === i ? {...c, ...edit} : c)});
  const dirty = Object.keys(changes).length > 0;
  const report = dirty ? (preview?.changes === changes ? preview.report : null) : saved.data;
  const followers = shots.filter(s => s.segment === shot.segment).sort((a,b) => a.order - b.order);
  const endShots = followers.slice(followers.findIndex(s => s.id === id));
  const castRow = cast.data?.rows.find((row: any) => row.segment === shot.segment);
  return <section className="card line">
    <label className="small">表演镜头 <select value={id} onChange={e => setId(e.target.value)}>{shots.map(s =>
      <option key={s.id} value={s.id}>{s.segment} · {s.id} · {s.action}</option>)}</select></label>
    <p>{shot.origin?.scene && <b>{shot.origin.scene} · </b>}{shot.origin?.line || shot.action}</p>
    <p className="muted small">{shot.segment} · 镜长 {seconds(shot.duration_ms)} 秒</p>
    {Object.entries(castRow?.cells ?? {}).map(([role, binding]: any) => {
      if (binding.status !== "ok") return null;
      const asset = assets.find(a => a.id === binding.assetVersionId);
      return <span className="small" key={role}>{role} · 本集当前绑定 {asset ? <a href={blob(asset.sha256)} target="_blank" rel="noreferrer">{asset.name} v{asset.version_no}</a> : binding.assetVersionId || "已确认"}</span>;
    })}
    <div className="grid2">{Object.entries({performance:"表情、姿势与动作",reaction:"对手反应",sound:"环境与动作音效"}).map(([key,title]) =>
      <label className="col small" key={key}>{title}<input value={shot[key] ?? ""} onChange={e => update({[key]:e.target.value})}/></label>)}</div>
    {cues.map((cue, i) => <fieldset key={i} style={{marginTop:12}}><legend>第 {i + 1} 句</legend>
      <div className="grid2">{Object.entries({speaker:"说话者",text:"台词原文",direction:"语气、重音与呼吸"}).map(([key,title]) =>
        <label className="col small" key={key}>{title}<input value={cue?.[key] ?? ""} onChange={e => editCue(i,{[key]:e.target.value})}/></label>)}
        <label className="col small">读秒方式<select value={cue?.mode ?? ""} onChange={e => editCue(i,{mode:e.target.value,rate:undefined,count:undefined,read_ms:undefined})}>
          <option value="" disabled>选择计时依据</option><option value="reading">填写试读或指定时长</option><option value="characters">按字/分钟估算</option><option value="words">按词/分钟估算</option></select></label>
        {cue?.mode === "reading" ? <SecondsInput title="发声时长（秒）" value={cue.read_ms} onChange={v => editCue(i,{read_ms:v})}/> :
          <><label className="col small">每分钟{cue?.mode === "words" ? "词" : "字"}数<input type="number" min="1" step="1" value={cue?.rate ?? ""} onChange={e => editCue(i,{rate:e.target.value ? Number(e.target.value) : undefined})}/></label>
          <label className="col small">发音字词数（留空自动计数）<input type="number" min="1" step="1" value={cue?.count ?? ""} onChange={e => editCue(i,{count:e.target.value ? Number(e.target.value) : undefined})}/></label></>}
        <SecondsInput title="镜内起声点（秒）" value={cue?.offset_ms} onChange={v => editCue(i,{offset_ms:v})}/>
        <SecondsInput title="句后反应停顿（秒）" value={cue?.pause_ms} onChange={v => editCue(i,{pause_ms:v})}/>
        <label className="col small">最迟结束镜<select value={cue?.throughShot || id} onChange={e => editCue(i,{throughShot:e.target.value})}>
          {endShots.map(s => <option key={s.id}>{s.id}</option>)}</select></label></div>
      <button className="btn ghost" onClick={() => update({dialogueCues:cues.filter((_,n)=>n!==i)})}>移除本句</button>
    </fieldset>)}
    <div className="row" style={{marginTop:12}}>
      <button className="btn" onClick={() => update({dialogueCues:[...cues,{speaker:"",text:"",mode:"reading",offset_ms:0,pause_ms:0}]})}>添加一句台词</button>
      <button className="btn" disabled={!dirty || act.busy} onClick={() => act.run(async () => {
        setPreview({changes, report:await cmd("preview_acting",{version_id:version.id,changes})});
      })}>核算本次调整</button>
      <button className="btn primary" disabled={!dirty || act.busy} onClick={() => act.run(() => onSave(shots.map(s => ({...s,...changes[s.id]}))))}>保存表演方案新版</button>
    </div>
    {dirty && !report && <p className="muted small">调整后可重新核算各句时长。</p>}
    {report && <><h3>台词时长表</h3>{report.rows.length > 0 ? <div style={{overflowX:"auto"}}><table className="table"><thead><tr><th>生成段 / 镜头</th><th>台词</th><th>计时依据</th><th>段内起声</th><th>发声 + 停顿</th><th>可用</th><th>余量</th></tr></thead>
      <tbody>{report.rows.map((r: any) => <tr key={r.shot+"-"+r.number}><td>{r.segment} · {r.shot}{r.throughShot !== r.shot && " → "+r.throughShot}</td>
        <td>{r.speaker}「{r.text}」{r.direction && " · "+r.direction}</td><td>{r.mode === "reading" ? "填写时长" : r.count+(r.mode === "words" ? " 词" : " 字")+" · "+r.rate+"/分"}</td>
        <td>{seconds(r.start_ms)} 秒</td><td>{seconds(r.speech_ms)} + {seconds(r.pause_ms)} 秒</td><td>{seconds(r.available_ms)} 秒</td>
        <td className={r.margin_ms < 0 ? "error" : ""}>{seconds(r.margin_ms)} 秒</td></tr>)}</tbody></table></div> : <p className="muted small">本版尚无可核算的台词。</p>}
      {[...report.errors,...report.warnings].map((m:string)=><p key={m} className="error">{m}</p>)}</>}
    {saved.error && <p className="error">{saved.error}</p>}<ErrorLine error={act.error}/>
  </section>;
}
