import { useEffect, useRef, useState } from "react";
import { cmd } from "./api";
import { ChatMarkdown } from "./ChatMarkdown";
import { ErrorLine, useAction, usePoll, useQuery } from "./ui";
import { FILM_STAGES } from "./workflow";

const classes:Record<string,string>={story:"story-result",characters:"character-profile",world:"world-rule",treatment:"treatment-scene",script:"script-scene"};
const names:Record<string,string>={decisionNotes:"导演决定落实",spine:"故事脊",timeline:"时间线",props:"规则与道具",causes:"因果核对",synopsis:"故事梗概",cast:"角色表",handoff:"交接说明",notes:"说明",name:"姓名",identity:"身份与作用",goal:"目标",obstacle:"阻碍",behavior:"行为",voice:"声音",identityRequired:"独立身份基准",identityReason:"身份基准说明",rule:"规则",choiceImpact:"影响选择",evidence:"依据",heading:"场景",characters:"出场角色",event:"事件",result:"结果",durationEstimateSec:"估计时长（秒）",timePoint:"发生时间",elapsedTime:"距上一场",rolePositions:"角色位置",stateChanges:"状态变化",action:"动作",dialogue:"对白",sound:"声音",performance:"关键表演",choice:"选择与办法",response:"阻碍与回应",change:"新局面",payoff:"预期与回收",durationBudget:"时长预算与依据"};
function Value({value,other}:{value:any;other?:any}) {
  if(value===null || value===undefined) return <span>—</span>;
  if(typeof value==="string") return <ChatMarkdown text={value}/>;
  if(typeof value!=="object") return <span>{typeof value==="boolean"?(value?"需要":"不需要"):String(value)}</span>;
  if(Array.isArray(value)) return <>{value.map((v,i)=><div key={i}><Value value={v} other={other?.[i]}/></div>)}</>;
  return <>{Object.entries(value).filter(([k])=>k!=="id").map(([k,v])=><div key={k} className={"compare-field"+(JSON.stringify(v)!==JSON.stringify(other?.[k])?" field-changed":"")}><b>{names[k]??k}</b><Value value={v}/></div>)}</>;
}
function CastTable({text}:{text:string}) {
  const rows=text.split("\n").filter(line=>line.trim().startsWith("|") && !/^[|:\s-]+$/.test(line)).map(line=>line.trim().replace(/^\||\|$/g,"").split(/(?<!\\)\|/).map(cell=>cell.trim().replace(/\\\|/g,"|")));
  if(rows.length<2)return <ChatMarkdown text={text}/>;
  return <table className="table"><thead><tr>{rows[0].map((cell,i)=><th key={i}>{cell}</th>)}</tr></thead><tbody>{rows.slice(1).map((cells,i)=><tr key={i}>{cells.map((cell,j)=><td key={j}>{cell}</td>)}</tr>)}</tbody></table>;
}
export function CandidateCompare({eid,stage,continuousId,view,reload,onBack}:{eid:string;stage:string;continuousId:string;view:any;reload:()=>void|Promise<void>;onBack:()=>void}) {
  const query=useQuery<any>("continuous_view",{episode_id:eid});
  const act=useAction();const root=useRef<HTMLDivElement>(null);
  const [side,setSide]=useState(0);const [different,setDifferent]=useState(false);const [expanded,setExpanded]=useState<string[]>([]);
  const run=query.data?.run;const choice=run?.id===continuousId?run.choice:null;
  usePoll(run?.state==="running",async()=>{await query.reload();await reload();});
  useEffect(()=>{
    const toolbar=root.current?.closest(".center")?.querySelector(".document-toolbar");
    if(!toolbar)return;
    const observer=new ResizeObserver(()=>root.current?.style.setProperty("--compare-top",`${toolbar.getBoundingClientRect().height}px`));
    observer.observe(toolbar);return()=>observer.disconnect();
  },[choice]);
  if(query.error)return <section><ErrorLine error={{message:query.error}}/><button className="btn" onClick={onBack}>返回本阶段</button></section>;
  if(!query.data)return <div className="card" aria-busy="true">正在读取候选稿件…</div>;
  if(!choice)return <section><p>这次比较的稿件暂不可用。</p><button className="btn" onClick={onBack}>返回本阶段</button></section>;
  const entries=choice.current?[{...choice.current,key:"current",summary:choice.current.disabledReason??"保留当前采用稿",check:choice.current.adoptable?"可保持现状":"仅供对照"},...choice.candidates]:choice.candidates;
  const versions=entries.map((e:any)=>view.artifacts.find((a:any)=>a.stage===e.stage)?.versions.find((v:any)=>v.id===e.versionId));
  if(versions.some((v:any)=>!v))return <section><p className="error">候选版本读取失败：当前分集没有对应稿件。</p><button className="btn" onClick={onBack}>返回本阶段</button></section>;
  const contents=versions.map((v:any)=>v.content);
  const field=({characters:"characters",world:"rules",treatment:"scenes",script:"scenes"} as Record<string,string>)[stage];
  const keys:string[]=stage==="story"?Array.from(new Set<string>(["synopsis","cast","handoff","notes",...contents.flatMap((c:any)=>Object.keys(c))])):Array.from(new Set<string>(contents.flatMap((c:any)=>(c[field]??[]).map((item:any)=>item.id))));
  const rows=keys.map(key=>({key,values:contents.map((c:any)=>stage==="story"?c[key]:(c[field]??[]).find((x:any)=>x.id===key))}));
  if(stage!=="story") for(const key of new Set<string>(contents.flatMap((c:any)=>Object.keys(c)).filter((k:string)=>k!==field))) rows.push({key,values:contents.map((c:any)=>c[key])});
  const chosen=!!choice.selected || run.state!=="waiting_choice";
  const choose=(key:string)=>act.run(async()=>{await cmd("choose_continuous",{continuous_id:run.id,choice_key:key});await query.reload();await reload();});
  return <div ref={root} className="candidate-compare" data-side={side}>
    <header className="compare-header"><h2>{choice.title}</h2><div className="compare-toolbar"><button className="btn" aria-pressed={different} onClick={()=>setDifferent(!different)}>{different?"全文":"只看不同"}</button><button className="btn" onClick={onBack}>返回本阶段</button></div><p>{FILM_STAGES.find(([s])=>s===stage)?.[1]} · 完整稿件比较</p></header>
    <div className="compare-grid compare-heads">{entries.map((e:any,i:number)=><section className="compare-candidate-head" data-side={i} key={e.key}><h3><span className="tag">{e.key==="current"?"当前版本":e.key}</span> {e.recommended && <span className="tag">推荐</span>} {e.label}</h3><p className={expanded.includes(e.key)?"":"summary-clamp"}>{e.summary}</p><button className="btn ghost" onClick={()=>setExpanded(expanded.includes(e.key)?expanded.filter(k=>k!==e.key):[...expanded,e.key])}>{expanded.includes(e.key)?"收起摘要":"展开摘要"}</button><p>✓ {e.check}</p></section>)}</div>
    {entries.length>1 && <nav className="compare-tabs" aria-label="A｜B"><button className="btn" aria-pressed={side===0} onClick={()=>setSide(0)}>A</button><span>｜</span><button className="btn" aria-pressed={side===1} onClick={()=>setSide(1)}>B</button></nav>}
    <div className="compare-body">{rows.map(row=>{
      const changed=JSON.stringify(row.values[0])!==JSON.stringify(row.values[1]);
      if(different && !changed && !(stage==="story" && row.key==="synopsis") && !expanded.includes(row.key))return <button className="btn same-row" key={row.key} onClick={()=>setExpanded([...expanded,row.key])}>{names[row.key]??row.key} 两版相同 · 展开</button>;
      return <section className={"compare-row"+(changed?" changed":"")} key={row.key}><div className="compare-row-title">{names[row.key]??row.key} {changed && <span className="tag">有改动</span>}</div><div className="compare-grid">{entries.map((e:any,i:number)=><article data-side={i} key={e.key} className={`${classes[stage]??"card"} compare-cell ${row.key==="synopsis"?"story-reading":""}`}>
        {row.values[i]===undefined?<p className="muted">此版没有这一场 / 这一条</p>:row.key==="cast" && typeof row.values[i]==="string"?<CastTable text={row.values[i]}/>:<Value value={row.values[i]} other={row.values[1-i]}/>}</article>)}</div></section>;
    })}</div>
    <footer className="compare-footer">{choice.selected?<b>已采用 {choice.selected==="current"?"当前版本":choice.selected}</b>:entries.map((e:any)=><div key={e.key}><button className={"btn "+(e.recommended?"primary":"")} disabled={act.busy || chosen || e.key==="current"&&!e.adoptable} onClick={()=>choose(e.key)}>{e.key==="current"?(e.adoptable?"保持现状，带着问题继续":"当前版本仅供对照"):`用 ${e.key} 这一版`}</button>{e.disabledReason && <small>{e.disabledReason}</small>}</div>)}<span>都不满意？在右侧对话里说想法</span></footer><ErrorLine error={act.error}/>
  </div>;
}
