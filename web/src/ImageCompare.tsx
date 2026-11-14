import {useState} from "react";
import {blob,cmd} from "./api";
import {ErrorLine,useAction,useQuery} from "./ui";

function Picture({candidate,ratio}:{candidate:any;ratio?:string}) {
  const asset=useQuery<any>("asset_version",{version_id:candidate.versionId});
  return <><ErrorLine error={asset.error?{message:asset.error}:null}/>{asset.data?<a href={blob(asset.data.sha256)} target="_blank" rel="noreferrer" aria-label="查看原图"><img src={blob(asset.data.sha256)} alt={asset.data.name} style={ratio?{aspectRatio:ratio.replace(":","/")}:undefined}/></a>:<p>正在读取图片…</p>}</>;
}
function Issues({issues}:{issues:any[]}) {return <>{issues.length?issues.map((x,i)=><p key={i}>{x.type && `${x.type} · `}{x.where}：{x.text}</p>):<p>无</p>}</>;}
export function ImageChoice({run,eid,onCompare,reload}:{run:any;eid:string;onCompare:(route:string)=>void;reload:()=>void|Promise<void>}) {
  const act=useAction();const choice=run.choice;const failed=choice.kind==="image_failed";
  const choose=(key:string)=>act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:key});await reload();});
  return <section className="mcard image-choice" data-chat-kind="choice" data-choice-kind={choice.kind}><div className="choice-eyebrow">需要你选一下</div><h3>{choice.title}</h3>
    {choice.candidates.map((c:any)=><article key={c.key}><b>{c.key}{c.recommended?" · 推荐":""}</b><Picture candidate={c} ratio={choice.aspectRatio}/><p>{c.value}</p>{failed?<Issues issues={c.critical}/>:c.minor.length>0 && <Issues issues={c.minor}/>}
      <button className="btn primary" disabled={act.busy} onClick={()=>choose(c.key)}>{failed?"用这张，保留这个问题":"用这一张"}</button>
      {failed && <p className="small">交付页会标出这个问题；这张图按现状进入镜头和提示词，不会再自动返修</p>}
    </article>)}
    <button className="btn" onClick={()=>onCompare(`#/e/${eid}/assets?compare=${run.id}`)}>并排看大图</button>
    {choice.regenerate && <button className="btn" disabled={act.busy} onClick={()=>choose("regenerate")}>再出一组看看</button>}
    {choice.reply && <p>{choice.reply}</p>}<p>在对话里说明想法</p><ErrorLine error={act.error}/></section>;
}
export function ImageCompare({eid,continuousId,reload,onBack}:{eid:string;continuousId:string;reload:()=>void|Promise<void>;onBack:()=>void}) {
  const query=useQuery<any>("continuous_view",{episode_id:eid});const act=useAction();const [side,setSide]=useState(0);
  const run=query.data?.run;const choice=run?.id===continuousId?run.choice:null;
  if(query.error)return <ErrorLine error={{message:query.error}}/>;
  if(!choice)return <section><p>{query.data?"这次图片选择已结束。":"正在读取图片候选…"}</p><button className="btn" onClick={onBack}>返回素材</button></section>;
  const failed=choice.kind==="image_failed";
  const choose=(key:string)=>act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:key});await query.reload();await reload();});
  return <div className="image-compare" data-side={side}>
    <header><h2>{choice.title}</h2><button className="btn" onClick={onBack}>返回素材</button></header>
    {choice.candidates.length>1 && <nav className="image-compare-tabs" aria-label="A｜B">{choice.candidates.map((c:any,i:number)=><button className="btn" key={c.key} aria-pressed={side===i} onClick={()=>setSide(i)}>{c.key}</button>)}</nav>}
    <div className="image-compare-grid">{choice.candidates.map((c:any,i:number)=><article key={c.key} data-side={i}>
      <h3>{c.key}{c.recommended && <span className="tag">推荐</span>}</h3><div className="image-compare-picture"><Picture candidate={c}/></div>
      <div className="image-compare-assessment"><h4>画面价值</h4><p>{c.value}</p><h4>用途适配</h4><p>{c.fitness}</p><h4>严重问题</h4><Issues issues={c.critical}/><h4>轻微差异</h4><Issues issues={c.minor}/></div>
    </article>)}</div>
    <footer className="image-compare-footer">{choice.candidates.map((c:any)=><button key={c.key} className="btn primary" disabled={act.busy || run.state!=="waiting_choice"} onClick={()=>choose(c.key)}>{c.key} · {failed?"用这张，保留这个问题":"用这一张"}</button>)}<span>{failed?"交付页会标出这个问题；这张图按现状进入镜头和提示词，不会再自动返修":"在右侧对话里说明想法"}</span></footer><ErrorLine error={act.error}/>
  </div>;
}
