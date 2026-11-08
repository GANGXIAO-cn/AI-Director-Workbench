import {useState} from "react";
import {useQuery} from "./ui";

type Finding = {kind:string;note:string;segment?:string;segments?:string[];locations?:{segment:string;sections:string[]}[];
  sections?:string[];section?:string;occurrences?:number;opening_characters?:number;action_characters?:number;characters?:number};
export type SignalReport = {inputs:{segment:string;sha256:string;intervals?:number;timelineIssue?:string}[];
  skipped:{segment:string;reason:string}[];fixedBlocks:number;warnings:Finding[]};
const labels:Record<string,string> = {within_prompt_repeat:"段内重复",repeated_sentence:"跨段复述",asset_id:"素材编号",
  opening_balance:"开场与动作篇幅",length:"全文篇幅"};

export function SignalList({report,locate}:{report:SignalReport;locate?:(sid:string,phrase:string)=>void}) {
  const [filter,setFilter]=useState("");
  const selected = report.warnings.filter(w=>!filter || w.segment===filter || w.segments?.includes(filter));
  return <div className="prompt-signals">
    <p>扫描了已保存的 {report.inputs.length} 段 TXT，按 {report.fixedBlocks} 条登记原文排除固定内容。下列位置供导演通读，必要的连续性说明可保留。</p>
    {!!report.inputs.length && <label>查看段落 <select value={filter} onChange={e=>setFilter(e.target.value)}>
      <option value="">全部段落</option>{report.inputs.map(i=><option key={i.segment}>{i.segment}</option>)}
    </select></label>}
    {!!selected.length && <ol>{selected.map((w,i)=><li key={i}><b>{labels[w.kind]}</b> · {w.locations
      ? w.locations.map(l=>l.segment+" "+l.sections.join("、")).join(" / ")
      : [w.segment,w.section,...(w.sections??[])].filter(Boolean).join(" ")}
      <p>{w.note}</p>{w.occurrences && <small>本段共 {w.occurrences} 处</small>}
      {w.kind==="opening_balance" && <small>开场 {w.opening_characters} 字 · 动作 {w.action_characters} 字</small>}
      {w.kind==="length" && <small>{w.characters} 字</small>}
      {locate && (w.segments??[w.segment!]).map(s=><button key={s} className="btn" onClick={()=>locate(s, w.kind==="asset_id" ? w.note.replace(/^疑似内部资产代号：/,"") : w.kind==="opening_balance" ? w.section! : w.kind==="length" ? "" : w.note)}>查看 {s} 正文</button>)}
    </li>)}</ol>}
    {!selected.length && !!report.inputs.length && <p>所选范围未发现以上通读线索。</p>}
    {report.skipped.map(s=><p key={s.segment}>{s.segment} · {s.reason}</p>)}
    {!!report.inputs.length && <details><summary>核对的 TXT 指纹</summary>{report.inputs.map(s=>
      <p key={s.segment}>{s.segment} · <code>{s.sha256}</code>{s.intervals!==undefined && ` · ${s.intervals} 个时间窗`}{s.timelineIssue && ` · ${s.timelineIssue}`}</p>)}</details>}
  </div>;
}

export function VersionSignals({version,number,locate}:{version:string;number:number;locate?:(sid:string,phrase:string)=>void}) {
  const [open,setOpen]=useState(false);
  const result=useQuery<SignalReport>(open?"prompt_signals":null,{version_id:version});
  return <details className="card" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>提示词通读线索 · R{String(number).padStart(2,"0")}</summary>
    {result.error && <p className="error">{result.error}</p>}
    {result.data && <SignalList report={result.data} locate={locate}/>}
  </details>;
}
