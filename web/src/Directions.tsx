import { DELIVERY_STEPS, PRODUCTION_STEPS, usePoll, useQuery } from "./ui";
import { savedWidths } from "./WorkspaceColumns";

export function PreKickoffWorkspace({eid,pid,episode,reload}:{eid:string;pid:string;episode?:any;reload:()=>void|Promise<void>}) {
  const query=useQuery<any>("continuous_view",{episode_id:eid});
  usePoll(true,async()=>{await query.reload();await reload();});
  const run=query.data?.run;
  if(query.data && !run)return <div className="page">本集尚未开工。<a className="btn primary" href={`#/p/${pid}`}>去开工</a></div>;
  const [left,right]=savedWidths();
  const status=!run?"正在读取本集状态…":run.state==="running"?"正在核对想法与项目必守设定，通过后自动进入本集制作。":run.reason || "正在读取制作进度";
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
        <p className="muted">开拍前先核对你的想法与项目必守设定。核对通过后，本页自动进入 01 故事方向；有冲突时专家按必守设定调整想法。</p>
      </div>
      <aside className="assistant prekickoff-assistant" aria-label="导演助手">
        <div className="head"><strong>导演助手</strong></div>
        <div className="msgs"><div className="mcard" data-chat-kind="progress"><p>{status}</p>
        </div></div>
      </aside>
    </div>
  </div>;
}
