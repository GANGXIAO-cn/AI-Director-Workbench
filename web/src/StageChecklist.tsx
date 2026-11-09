import { ErrorLine, Modal, STAGES, Tag, rno, useQuery } from "./ui";

type Method={name:string;text:string;methodSource:{path:string;sha256:string}};
type Report={version:null|{id:string;number:number};checks:{label:string;status:string;reason:string}[];
  methods:Method[];creationMethods:Method[];methodStage:string};
function MethodSources({label,items}:{label:string;items:Method[]}) {
  if (!items.length) return null;
  return <section><h3>{label}</h3>{items.map(m=><details className="card" key={m.name}>
    <summary>{m.name}</summary><p className="muted tiny">{m.methodSource.path} · SHA-256 {m.methodSource.sha256}</p>
    <pre className="method-excerpt">{m.text}</pre></details>)}</section>;
}

export function StageChecklist({eid,stage,version,delivery,scope,segments,close}:{eid:string;stage:string;version:string|null;delivery:string|null;scope:string;segments:string[];close:()=>void}) {
  const result=useQuery<Report>("stage_checklist",{episode_id:eid,stage,version_id:version,delivery_id:delivery,scope,segments});
  const name=STAGES.find(([key])=>key===stage)?.[1] ?? stage;
  const report=result.data;
  return <Modal width={780} onClose={close}><div className="spread"><h2>{name} · 过关检查</h2>
    <button className="btn" onClick={close}>关闭清单</button></div>
    <ErrorLine error={result.error?{message:result.error}:null}/>
    {!report&&!result.error&&<p>正在读取阶段记录…</p>}
    {report && <>{report.version&&<p>检查稿件 {rno(report.version.number)}</p>}
      <ul className="stage-check-list">{report.checks.map((r,i)=><li key={i}><Tag tone={r.status==="通过"?"green":r.status==="未通过"?"amber":""}>{r.status}</Tag>
        <div><strong>{r.label}</strong><p>{r.reason}</p></div></li>)}</ul>
      <MethodSources label="本稿或其来源稿起草时实际读取的增强方法" items={report.creationMethods}/>
      <MethodSources label={report.methodStage+"当前配置的增强方法 · 人工复核依据"} items={report.methods}/>
    </>}<button className="btn" onClick={result.reload}>刷新检查结果</button>
  </Modal>;
}
