import { useState } from "react";
import { Tag } from "./ui";

export type TopicRuntime = {form:"短篇"|"长篇";minSeconds:number|null;maxSeconds:number|null};

export function runtimeLabel(target?:TopicRuntime|null) {
  if (!target) return "";
  const {form,minSeconds,maxSeconds}=target;
  const minutes=[minSeconds,maxSeconds].every(value=>value===null || value%60===0);
  const unit=minutes ? 60 : 1;
  let span:string;
  if (minSeconds===null && maxSeconds!==null) span=`最多 ${maxSeconds/unit}`;
  else if (maxSeconds===null && minSeconds!==null) span=`至少 ${minSeconds/unit}`;
  else if (maxSeconds!==null && minSeconds!==null) span=minSeconds===maxSeconds ? String(minSeconds/unit) : `${minSeconds/unit}–${maxSeconds/unit}`;
  else return "";
  return `${form} · ${span} ${minutes ? "分钟" : "秒"}`;
}

export function TopicRuntimeTag({target}:{target?:TopicRuntime|null}) {
  return target ? <Tag tone="cyan">{runtimeLabel(target)}</Tag> : null;
}

export function useManualRuntime() {
  const [form,setForm]=useState<TopicRuntime["form"]|"">("");
  const [duration,setDuration]=useState("");
  const [unit,setUnit]=useState("秒");
  const fields=<div className="row">
    <label>选题篇幅 <select value={form} onChange={e=>setForm(e.target.value as typeof form)}>
      <option value="">选择篇幅</option><option>短篇</option><option>长篇</option>
    </select></label>
    <label>建议成片时长 <input value={duration} style={{width:"8em"}} placeholder="如 60 或 60–90" onChange={e=>setDuration(e.target.value)}/></label>
    <select aria-label="成片时长单位" value={unit} onChange={e=>setUnit(e.target.value)}><option>秒</option><option>分钟</option></select>
  </div>;
  function read():TopicRuntime {
    if (!form) throw new Error("请选择长篇或短篇");
    const span=duration.trim().match(/^(\d+(?:\.\d+)?)(?:\s*[-–—~～至到]\s*(\d+(?:\.\d+)?))?$/);
    if (!span) throw new Error("请填写成片时长，如 4 分钟或 60–90 秒，并选择对应单位");
    const scale=unit==="分钟" ? 60 : 1;
    const minSeconds=Number(span[1])*scale, maxSeconds=Number(span[2] ?? span[1])*scale;
    if (!Number.isSafeInteger(minSeconds)||!Number.isSafeInteger(maxSeconds)||minSeconds<1||maxSeconds<minSeconds)
      throw new Error("成片时长须为正整数秒，范围从短到长");
    return {form,minSeconds,maxSeconds};
  }
  return {fields,read};
}
