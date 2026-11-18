import { Drawer } from "./Drawer";
import { DecisionNotes } from "./Decisions";
import { useEffect, useState } from "react";
import { blob, q } from "./api";
import { rno, useQuery } from "./ui";
import { FILM_STAGES } from "./workflow";

type Entry = {name:string; role:string; sha256:string; bytes:number; runId:string;
  upstreamVersion?: {display_no:number; stage:string}};
type Group = {key:string; label:string; items:Entry[]};

function groupLabel(group:Group) {
  if (group.key!=="decisions") return group.label;
  if (group.items.every(item=>item.role==="director_decisions")) return group.label;
  return group.items.some(item=>item.role==="director_decisions") ? "导演要求与决定" : "导演要求";
}

function upstream(entry: Entry) {
  const v = entry.upstreamVersion;
  return v ? (FILM_STAGES.find(([k])=>k===v.stage)?.[1].slice(3) ?? v.stage)+" "+rno(v.display_no) : "";
}

function InputDrawer({item, close}:{item:Entry; close:()=>void}) {
  const query = useQuery("input_text", {run_id:item.runId, sha256:item.sha256});
  return <Drawer label={item.name} close={close}>
    {query.error && <p role="alert">{query.error}</p>}
    {query.data?.image ? <img src={blob(item.sha256)} alt={item.name}/> : <pre>{query.data?.text}</pre>}
  </Drawer>;
}

export function InputGroups({groups}:{groups:Group[]}) {
  const [item, select] = useState<Entry|null>(null);
  return <>{groups.map(g=><section key={g.key}><h4>{groupLabel(g)}</h4><ul>{g.items.map((entry,i)=><li key={i}>
    <button className="link" onClick={()=>select(entry)}>{entry.name}</button> {upstream(entry)} · {entry.bytes} 字节
    {' '}<a href={blob(entry.sha256)} download={entry.name} title={entry.sha256}>下载</a>
  </li>)}</ul></section>)}
    {item && <InputDrawer item={item} close={()=>select(null)}/>}
  </>;
}

export function StageInputs({versionId,art,records,onSave}:{versionId:string;art:any;records:any[];onSave:(vid:string)=>void}) {
  const {data,error}=useQuery("version_inputs",{version_id:versionId});
  const groups:Group[]=data?.groups ?? [];
  const version=art.versions.find(v=>v.id===versionId);
  const [decisions,setDecisions]=useState<any[]>([]);
  const [readError,setReadError]=useState("");
  useEffect(()=>{
    let active=true;
    const entries=(data?.groups??[]).flatMap(g=>g.items).filter(i=>i.role==="director_decisions");
    Promise.all(entries.map(async i=>JSON.parse((await q("input_text",{run_id:i.runId,sha256:i.sha256})).text)))
      .then(lists=>{if(active)setDecisions(Array.from(new Map(lists.flat().map(d=>[d.code,d])).values()));})
      .catch(e=>{if(active)setReadError(String(e));});
    return ()=>{active=false;};
  },[data]);
  const frozen=new Set(decisions.map(d=>d.code));
  return <details className="card stage-inputs"><summary>本步用了什么
    {data && (groups.length ? " · 本稿读取了："+groups.map(g=>{
      const versions=g.items.map(upstream).filter(Boolean);
      return groupLabel(g)+" "+g.items.length+" 份"+(versions.length?"（"+versions.join("、")+"）":"");
    }).join(" · ") : " · 无创作任务输入")}</summary>
    {error && <p role="alert">{error}</p>}
    {data?.derivedFrom && <p>手工编辑，基于 {rno(data.derivedFrom.display_no)}；下列为其起草时读取的内容。</p>}
    <InputGroups groups={groups}/>
    {version?.content.supplements?.map((s,i)=><p key={i}>本稿新报制作补充 · {s.subject} · {s.statement} · {s.scenes?.join("、")}</p>)}
    {readError && <p role="alert">{readError}</p>}
    {decisions.map(d=>{
      const notes=version?.content.decisionNotes?.filter(n=>n.decision===d.code)??[];
      return <div key={d.code}><p>{d.code} · {d.text}</p>{notes.length
        ?notes.map((n,i)=><p key={n.segmentId??i}>{n.segmentId && n.segmentId+" · "}{n.quote||n.reason}</p>)
        :<p>待填写落点</p>}</div>;
    })}
    {version && data && <DecisionNotes records={records} art={art} version={version} onSave={onSave} frozen={frozen}/>}
  </details>;
}
