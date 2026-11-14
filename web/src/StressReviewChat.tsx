import { useState } from "react";
import { useProjectRoute } from "./projectNavigation";
import { useDisplay } from "./DisplayPreferences";
import { cmd, q } from "./api";
import { changeStageEdit } from "./Cooperation";
import { FILM_STAGES } from "./workflow";
import { ErrorLine, rno, useAction } from "./ui";

type Issue = {id:string; stage:string; problem:string; evidence:string; sceneIds?:string[]; severity?:string};
type Option = {issueId:string; proposal:string; repairScope:string};
export function stressGrade(report:any, issue:Issue) {
  return issue.severity || report.stressDisposition?.[issue.id]?.severity || "ungraded";
}
const gradeLabels:Record<string,string>={blocking:"必须修正",normal:"普通意见",ungraded:"待分级"};

export function StressReviewChat({report,view,reload}:{report:any;view:any;reload:()=>void}) {
  const action=useAction();
  const {go}=useProjectRoute();
  const {setFocusChat}=useDisplay();
  const [remarks,setRemarks]=useState<Record<string,string>>({});
  const [picks,setPicks]=useState<Record<string,number>>({});
  const issues:Issue[]=Array.isArray(report.content.issues)?report.content.issues.filter((i:any)=>i?.id && typeof i.problem==="string"):[];
  const options:Option[]=Array.isArray(report.content.options)?report.content.options:[];
  if (!issues.length) return null;
  const counts=(grade:string)=>issues.filter(i=>stressGrade(report,i)===grade).length;
  return <section className="stress-chat"><strong>故事审查 · {rno(report.display_no)}</strong>
    {Object.values(report.stressDisposition || {}).some((v:any)=>v.source==="补充分级") && <small>本版问题已补充分级</small>}
    <p>{Object.entries(gradeLabels).filter(([g])=>counts(g)).map(([g,label])=>label+" "+counts(g)+" 条").join("　")}</p>
    {issues.slice().sort((a,b)=>Number(stressGrade(report,b)==="blocking")-Number(stressGrade(report,a)==="blocking")).map(issue=>{
      const grade=stressGrade(report,issue);
      const stage=FILM_STAGES.find(([key,label])=>key===issue.stage || label.split(" ")[0]===issue.stage);
      const target=view.artifacts.find((a:any)=>a.stage===stage?.[0]);
      const base=target?.versions.find((v:any)=>v.id===target.adopted_version_id);
      const choices=options.filter(o=>o?.issueId===issue.id);
      const selected=picks[issue.id] ?? (choices.length===1?0:-1);
      const option=choices[selected];
      const waived=report.stressDisposition?.[issue.id]?.waived;
      const prior=target?.tasks?.findLast((task:any)=>(task.messageSelections ?? task.messages).some((m:any)=>{
        if (!m.selection) return false;
        const selection=typeof m.selection==="string"?JSON.parse(m.selection):m.selection;
        return selection.reportId===report.id && selection.issueId===issue.id && selection.versionId===base?.id;
      }));
      const run=prior?.runs?.at(-1);
      const produced=target?.versions.find((v:any)=>v.source_run_id===run?.id);
      const running=run && ["queued","running","waiting_quota"].includes(run.status);
      return <article key={issue.id} className="stress-chat-issue">
        <header><b className={"stress-grade "+grade}>{gradeLabels[grade] || gradeLabels.ungraded}</b> · {issue.id} · {stage?.[1] || issue.stage}</header>
        <p>{issue.problem}</p>
        {Array.isArray(issue.sceneIds) && issue.sceneIds.length>0 && <div className="stress-locations">在正文中定位　{issue.sceneIds.map(id=><button key={id} onClick={()=>action.run(async()=>{
          const sources=await q("version_sources",{version_id:report.id});
          const script=sources.inputs.find((entry:any)=>entry.role==="adopted_script" && entry.upstreamVersion)?.upstreamVersion;
          if (!script) throw new Error("此报告未记录所审剧本的版本");
          setFocusChat(false);go(`#/e/${view.episode.id}/script?v=${script.id}&scene=${encodeURIComponent(id)}`);
        })}>{id.replace(/^scene_0*(\d+)$/, "第 $1 场")}</button>)}</div>}
        <details><summary>审查依据</summary><p>{issue.evidence}</p></details>
        {waived ? <b className="ok">已按本版放行</b> : produced ? <a className="btn" href={`#/e/${view.episode.id}/${target.stage}?v=${produced.id}`}>查看返修稿 {rno(produced.display_no)}</a> : running ? <span>正在返修 · {stage?.[1]}</span> : <>
          {choices.length>1 && choices.map((o,i)=><label className="stress-option" key={i}><input type="radio" name={report.id+issue.id} checked={selected===i} onChange={()=>setPicks({...picks,[issue.id]:i})}/>{o.proposal}</label>)}
          {choices.length===1 && <p>{option.proposal}</p>}
          {option && <small>修改范围　{option.repairScope}</small>}
          <div className="review-actions"><button className="btn" disabled={action.busy || !option || !base} onClick={()=>action.run(async()=>{
            const revision=target.editState==="protected"?(await changeStageEdit(target,true)).revision:target.revision;
            const goal=[`根据故事审查 ${rno(report.display_no)} 的 ${issue.id} 修订${stage?.[1]} ${rno(base.display_no)}，提交本阶段完整新稿。`,"问题："+issue.problem,"依据："+issue.evidence,"选定方案："+option.proposal,"修改范围："+option.repairScope].join("\n");
            await cmd("create_task",{episode_id:view.episode.id,stage:target.stage,goal,base_version_id:base.id,workspace_revision:revision,editor_guard:[target.id,revision],
              selection:{reportId:report.id,issueId:issue.id,versionId:base.id,anchor:"故事审查 "+issue.id}});
            await reload();
          })}>修订这条{base?" · "+stage?.[1]+" "+rno(base.display_no):""}</button>
          {grade==="normal" && <><input aria-label={issue.id+" 放行备注（选填）"} placeholder="放行备注（选填）" value={remarks[issue.id] || ""} onChange={e=>setRemarks({...remarks,[issue.id]:e.target.value})}/><button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{await cmd("waive_stress_issue",{version_id:report.id,issue_id:issue.id,reason:remarks[issue.id] || ""});await reload();})}>按本版放行</button></>}</div></>}
      </article>;
    })}<ErrorLine error={action.error}/></section>;
}
