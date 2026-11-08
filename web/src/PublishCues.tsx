import { fmtMs } from "./ui";

type Segment = {id:string; start:number; end:number};
type Cue = {segment?:string; scene?:string; at:string; action:string};
type Fields = {music?:string; cues?:Cue[]};
const time = (ms:number) => fmtMs(ms)+"."+String(ms % 1000).padStart(3,"0");

export function PublishCues({form,segments,bgm,change}:{form:Fields; segments:Segment[]; bgm:boolean;
  change:(patch:Fields)=>void}) {
  const cues = form.cues ?? [];
  const missing = segments.filter(s=>!cues.some(c=>c.segment===s.id));
  const enabled = form.music ? form.music==="enabled" : bgm;
  const update = (index:number, patch:Partial<Cue>) => change({cues:cues.map((c,i)=>i===index?{...c,...patch}:c)});
  return <fieldset className="card"><legend>剪辑音乐对位</legend>
    <label>配乐使用状态 <select value={form.music ?? ""} onChange={e=>change({music:e.target.value})}>
      <option value="">随采用提示词（{bgm?"启用":"备用"}）</option><option value="backup">备用，不启用</option>
      <option value="enabled">启用剪辑配乐</option></select></label>
    <p className="muted small">时间按全片设计预算填写。备用时，交付表逐段标记不放背景音乐，另列已写的备用对位。</p>
    {cues.map((c,i)=><div key={i} className="col card line">
      <div className="row"><label>对位段 <select value={c.segment ?? ""} aria-label={`第 ${i+1} 行对位段`} onChange={e=>update(i,{segment:e.target.value || undefined,scene:undefined})}>
        <option value="">{c.scene ?? "全片事件"}</option>{c.segment && !segments.some(s=>s.id===c.segment) && <option>{c.segment}</option>}
        {segments.map(s=><option key={s.id}>{s.id}</option>)}</select></label>
        <button className="btn ghost" onClick={()=>change({cues:cues.filter((_,j)=>j!==i)})}>移除此行</button></div>
      <label>全片设计时间<input value={c.at} onChange={e=>update(i,{at:e.target.value})}/></label>
      <label>动作与音乐处理<input value={c.action} onChange={e=>update(i,{action:e.target.value})}/></label>
    </div>)}
    <div className="row"><button className="btn" disabled={!missing.length} onClick={()=>change({cues:[...cues,...missing.map(s=>
      ({segment:s.id,at:time(s.start)+"–"+time(s.end),action:enabled?"":"不放背景音乐"}))]})}>补齐各段设计时间</button>
      <button className="btn" onClick={()=>change({cues:[...cues,{at:"",action:""}]})}>增加对位行</button></div>
  </fieldset>;
}
