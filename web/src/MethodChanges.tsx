import { useState } from "react";
import { STAGES, useQuery } from "./ui";

const stageNames: Record<string,string> = {...Object.fromEntries(STAGES),topic:"选题",group:"连续故事",grid_examples:"九宫格用图参考",h3_guides:"H3 指南原文",
  story_review:"故事独审",cold_read:"提示词冷读",comparison:"提示词对照"};
const labels = {added:"新增",removed:"移除",edited:"正文修改",stages:"使用阶段变更",location:"来源位置变更"};
type Entry = {path:string;sha256:string;source?:{path?:string;lines?:string;commit?:string}};
type Change = {key:string;kind:keyof typeof labels;scope:string;before:Entry|null;after:Entry|null;patch:string;oldStages:string[];newStages:string[]};
type Comparison = {stages: Record<string,{before:string[];after:string[]}>; sections: Change[];
  beforeSources:Record<string,string>;afterSources:Record<string,string>};
const stageList = (stages:string[])=>stages.map(k=>stageNames[k] || k).join("、");

function origin(entry:Entry|null) {
  if (!entry) return "此版本未收录";
  return (entry.source?.path || entry.path)+(entry.source?.lines ? " · 行 "+entry.source.lines : "");
}

export function MethodChanges({candidate, versions, pid, current}:{candidate:string;versions:string[];pid:string;current?:string}) {
  const peers=versions.filter(v=>v!==candidate);
  const index=versions.indexOf(candidate);
  const [picked,setPicked] = useState("");
  const base=peers.includes(picked) ? picked : current && peers.includes(current) ? current : versions[index-1] || peers[0];
  const diff=useQuery<Comparison>(base?"compare_packs":null,{original:base,candidate,project_id:pid || null});
  if (!peers.length) return null;
  return <section className="card method-changes">
    <h2>版本差异</h2>
    <label>比较基准 <select aria-label="比较基准" value={base} onChange={e=>setPicked(e.target.value)}>{peers.map(v=><option key={v}>{v}</option>)}</select></label>
    <span> → {candidate}</span>
    {diff.error && <p className="error">{diff.error}</p>}
    {diff.data && <>
      <p>阶段输入变更 · {stageList(Object.keys(diff.data.stages)) || "无"}</p>
      {diff.data.sections.length===0 && <p>小节正文、使用阶段和来源位置均一致。</p>}
      {diff.data.sections.map(row=><details key={row.key}>
        <summary><strong>{row.key}</strong> · {labels[row.kind]} · {row.scope==="shared"?"通用":"项目专属"}</summary>
        <div className="small"><p>{base} · {origin(row.before)}</p><p>{candidate} · {origin(row.after)}</p>
          <p>使用阶段 · {stageList(row.oldStages) || "无"} → {stageList(row.newStages) || "无"}</p></div>
        {row.patch && <pre>{row.patch}</pre>}
      </details>)}
      {Object.keys(diff.data.stages).length>0 && <details><summary>阶段装入顺序</summary>
        {Object.entries(diff.data.stages).map(([stage,lists])=><div key={stage}><b>{stageNames[stage] || stage}</b>
          <p>{base} · {lists.before.join(" → ") || "无"}</p><p>{candidate} · {lists.after.join(" → ") || "无"}</p></div>)}</details>}
      <details><summary>源仓库提交</summary>
        {[...new Set([...Object.keys(diff.data.beforeSources),...Object.keys(diff.data.afterSources)])].map(repo=><p key={repo}>{repo}<br/>
          {base} · <code>{diff.data!.beforeSources[repo] || "未登记"}</code><br/>
          {candidate} · <code>{diff.data!.afterSources[repo] || "未登记"}</code></p>)}</details>
    </>}
  </section>;
}

type FrozenSection = {name:string;role:string;sha256:string;text:string;path?:string;source?:Entry["source"]};

export function FrozenMethods({runId}:{runId:string}) {
  const [open,setOpen] = useState(false);
  const saved = useQuery<{ref:string|null;inputHash:string;sections:FrozenSection[]}>(open?"run_methods":null,{run_id:runId});
  return <details onToggle={e=>setOpen(e.currentTarget.open)}><summary>查看本次冻结的方法原文</summary>
    {saved.error && <p className="error">{saved.error}</p>}
    {saved.data && <div className="method-changes"><p>{saved.data.ref ?? "历史记录未注明方法包版本"} · {saved.data.sections.length} 份方法文件</p>
      {saved.data.sections.map(row=><details key={row.role+row.name}><summary>{row.name} · {row.role==="method"?"通用":"项目专属"}</summary>
        <p>{row.source?.path || row.path || "历史记录未注明源位置"} {row.source?.lines}</p>
        <p>输入文件 SHA-256 · <code>{row.sha256}</code></p>
        {row.source?.commit && <p>源提交 · <code>{row.source.commit}</code></p>}
        <pre>{row.text}</pre></details>)}</div>}</details>;
}
