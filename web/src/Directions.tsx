import { useState } from "react";
import { cmd } from "./api";
import { ChatMarkdown } from "./ChatMarkdown";
import { DELIVERY_STEPS, ErrorLine, PRODUCTION_STEPS, useAction, usePoll, useQuery } from "./ui";
import { savedWidths } from "./WorkspaceColumns";

export function DirectionChoice({run,onCompare,reload}:{run:any;onCompare:(route:string)=>void;reload:()=>void|Promise<void>}) {
  const act=useAction();
  const choose=(key:string)=>act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:key});await reload();});
  return <section className="mcard direction-choice" data-chat-kind="choice">
    <h3>选一个你想看的故事</h3>
    {run.choice.candidates.map((item:any)=><div className="direction-option" key={item.key}>
      <b>{item.key} {item.recommended && <span className="tag">推荐</span>} {item.title}</b>
      <p>{item.premise}</p><button className="btn" disabled={act.busy} onClick={()=>choose(item.key)}>用这个方向</button>
    </div>)}
    <button className="btn direction-compare-open" onClick={()=>onCompare(`#/e/${run.episode_id}/story?directions=${run.id}`)}>并排看三个方向</button>
    <p className="direction-hint">都不想要？直接在下方输入框说想法</p>
    {run.choice.contextNotice && <p className="direction-hint">{run.choice.contextNotice}</p>}<ErrorLine error={act.error}/>
  </section>;
}

export function DirectionCompare({eid,cid,reload}:{eid:string;cid:string;reload:()=>void|Promise<void>}) {
  const query=useQuery<any>("continuous_view",{episode_id:eid});const act=useAction();
  const run=query.data?.run;const choice=run?.id===cid && run.choice?.kind==="direction"?run.choice:null;
  usePoll(run?.state==="running",async()=>{await query.reload();await reload();});
  if(query.error)return <ErrorLine error={{message:query.error}}/>;
  if(!query.data)return <p>正在读取故事方向…</p>;
  if(!choice)return <p>方向已选定。<a href={`#/e/${eid}/story`}>返回故事稿</a></p>;
  return <section className="direction-compare" aria-label="三个故事方向比较"><div className="direction-grid">
    {choice.candidates.map((item:any)=><article className="story-result direction-card" key={item.key}>
      <div>{item.key} {item.recommended && <span className="tag">推荐</span>}</div><h2>{item.title}</h2>
      <p>{item.premise}</p><div className="story-reading"><ChatMarkdown text={item.story}/></div>
      <div className="direction-detail"><b>看点</b><p>{item.hook}</p><b>结尾</b><p>{item.ending}</p><b>与最近几集的不同</b><p>{item.difference}</p></div>
      <p className="direction-sources">参考来源：{item.sources.join("、") || "无"}</p>
      <button className="btn primary" disabled={act.busy || run.state!=="waiting_choice"} onClick={()=>act.run(async()=>{
        await cmd("choose_continuous",{continuous_id:run.id,choice_key:item.key});await query.reload();await reload();
      })}>用这个方向</button>
    </article>)}
  </div><ErrorLine error={act.error}/></section>;
}

export function KickoffChoice({run,pid,reload}:{run:any;pid:string;reload:()=>void|Promise<void>}) {
  const act=useAction();
  return <section className="mcard" data-chat-kind="choice"><h3>想法与必守设定有冲突</h3>
    {run.choice.conflicts.map((item:any)=><div key={item.key}><b>{item.key}</b><p>你的想法：{item.requestQuote}</p><p>必守设定：{item.settingQuote}</p><p>{item.reason}</p></div>)}
    <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:"adjust"});await reload();})}>按设定调整我的想法</button>
    <a className="btn" href={`#/p/${pid}/settings?reason_default=${encodeURIComponent("连续制作：想法与此设定冲突，导演选择修改设定")}`} onClick={event=>{event.preventDefault();void act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:"settings"});location.hash=`#/p/${pid}/settings?reason_default=${encodeURIComponent("连续制作：想法与此设定冲突，导演选择修改设定")}`;});}}>修改这条设定</a>
    <p className="muted small">也可以在下方说明想法。</p><ErrorLine error={act.error}/>
  </section>;
}

/** 实测 110：开工核对期间也按本集工作区布局显示，核对通过后由 Workspace 自动切换到正式工作区。 */
function readableReason(reason:string|null|undefined) {
  if(!reason)return "";
  let text=reason;
  try {const parsed=JSON.parse(reason);if(parsed && typeof parsed.result==="string")text=parsed.result;} catch { /* plain text */ }
  if(/authenticat|oauth|401|log ?in again|unauthorized/i.test(text))return "执行器登录已失效，请在终端重新登录后点“继续”。";
  return text;
}

export function PreKickoffWorkspace({eid,pid,episode,reload}:{eid:string;pid:string;episode?:any;reload:()=>void|Promise<void>}) {
  const query=useQuery<any>("continuous_view",{episode_id:eid});const act=useAction();const [text,setText]=useState("");
  usePoll(true,async()=>{await query.reload();await reload();});
  const run=query.data?.run;
  if(query.data && !run)return <div className="page">本集尚未开工。<a className="btn primary" href={`#/p/${pid}`}>去开工</a></div>;
  const [left,right]=savedWidths();
  const status=!run?"正在读取本集状态…":run.state==="running"?"正在核对想法与项目必守设定，通过后自动进入本集制作。":readableReason(run.reason);
  return <div className="app episode-workspace prekickoff-workspace">
    <div className="ws" style={{flex:1,gridTemplateColumns:`${left}px minmax(0,1fr) ${right}px`}}>
      <div className="catalog">
        <div className="spread" style={{padding:"0 8px 12px"}}><b>目录</b><a className="muted" href={`#/p/${pid}`}>⇤</a></div>
        {PRODUCTION_STEPS.map(([k,label])=><span key={k} className={"item"+(k==="story"?" on":"")} aria-disabled="true">{label}<span className={"st"+(k==="story"?" now":"")}/></span>)}
        <b>交付</b>{DELIVERY_STEPS.map(([k,label])=><span key={k} className="item" aria-disabled="true">{label}<span className="st"/></span>)}
      </div>
      <div className="center">
        <div className="topbar"><span className="title">{episode?.display_code ?? ""} · {episode?.title || "未命名"}</span><span className="chip">开工核对中</span></div>
        <h1>本集开工核对</h1>
        <p className="muted">开拍前先核对你的想法与项目必守设定。核对通过后，本页自动进入 01 故事方向；有冲突时请在右侧导演助手中选择处理方式。</p>
      </div>
      <aside className="assistant prekickoff-assistant" aria-label="导演助手">
        <div className="head"><strong>导演助手</strong></div>
        <div className="msgs"><div className="mcard" data-chat-kind="progress"><p>{status}</p>
          {run?.state==="blocked" && <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd("resume_continuous",{continuous_id:run.id});await query.reload();})}>继续</button>}</div>
          {run?.choice?.kind==="kickoff_conflict" && <KickoffChoice run={run} pid={pid} reload={query.reload}/>}
        </div>
        <div className="composer"><textarea aria-label="说明开工想法" value={text} onChange={e=>setText(e.target.value)} placeholder={run?.state==="waiting_choice"?"说明你的想法":"核对进行中"}/>
          <button className="btn primary" disabled={act.busy || !text.trim() || run?.state!=="waiting_choice"} onClick={()=>act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:"free",text});setText("");await query.reload();await reload();})}>发送消息</button>
          <ErrorLine error={act.error}/></div>
      </aside>
    </div>
  </div>;
}
