import { ContinuousRecoveryNote } from "./ContinuousRecovery";
import { useEffect } from "react";
import { cmd } from "./api";
import { ErrorLine, useAction, usePoll, useQuery } from "./ui";
import { FILM_STAGES } from "./workflow";

/** 实测 117：连续制作停下或等待选择时，在对话流、最近剧集、侧栏和标签标题上提示导演。 */
export type AttentionItem = { episodeId:string; displayCode:string; title:string; projectId:string; projectName:string;
  state:"blocked"|"waiting_choice"; reason:string; stage?:string|null; phase?:string|null; updatedAt:number };

export function useAttention() {
  const query = useQuery<AttentionItem[]>("attention_episodes");
  usePoll(true, query.reload, 5000);
  return query.data ?? [];
}

const BASE_TITLE = "AI 导演工作台";
export function useAttentionTitle(items:AttentionItem[]) {
  useEffect(() => {
    const stopped = items.filter(i => i.state === "blocked").length;
    const waiting = items.length - stopped;
    const parts = [stopped ? `⏸ ${stopped} 集已停下` : "", waiting ? `${waiting} 集待你选择` : ""].filter(Boolean);
    document.title = parts.length ? parts.join(" · ") + " · " + BASE_TITLE : BASE_TITLE;
  }, [items.map(i => i.episodeId + i.state).join(",")]);
}

export function AttentionTag({ item }:{ item?:AttentionItem }) {
  if (!item) return null;
  return <span className={"attention-tag " + item.state} title={item.state === "blocked" ? explainStop(item.reason, stageLabel(item.stage, item.phase)).summary : "AI 已备好选项，等你选择"}>
    {item.state === "blocked" ? "已停下" : "待你选择"}</span>;
}

export function stageLabel(stage?:string|null, phase?:string|null) {
  if (phase === "kickoff") return "开工核对";
  if (phase === "direction") return "01 故事方向";
  return ({assets:"07 出图", delivery:"图文交付", package:"H3 出包"} as Record<string,string>)[stage ?? ""]
    ?? FILM_STAGES.find(([s]) => s === stage)?.[1] ?? "当前阶段";
}

function rawText(reason:string) {
  try { const parsed = JSON.parse(reason); if (parsed && typeof parsed.result === "string") return parsed.result; } catch { /* plain text */ }
  return reason;
}

/** 按停下原因给出固定白话说明；推进判断角色接入前只做确定性归类。 */
export function explainStop(reason:string, stage:string) {
  const text = rawText(reason || "");
  if (/登录已失效|authenticat|oauth|401|unauthorized|log ?in again/i.test(text))
    return { summary:`${stage}：执行器登录已失效，AI 无法继续。`, todo:"请在终端重新登录该执行器，然后点“继续”。" };
  if (/额度|quota|rate limit|usage limit/i.test(text))
    return { summary:`${stage}：执行器额度用完或达到频率限制。`, todo:"等额度恢复后点“继续”，或在输入框下方换一个执行器再继续。" };
  if (/方向格式重试后仍不合格/.test(text))
    return { summary:`${stage}：AI 给出的故事方向格式两次都不合格，所以停下。`, todo:"点“继续”会让 AI 重新出方向。" };
  if (/没有出图提示词/.test(text))
    return { summary:`${stage}：${text.split("。")[0]}。`, todo:"到 07 素材的备图工作单补写这一项的出图提示词，或改为“只用文字描述”、选一张可复用图，然后点“继续”。" };
  if (/开工核对/.test(text))
    return { summary:`${stage}：开工核对没有完成。`, todo:"点“继续”重新核对。" };
  if (/仍有必须修正的问题|阻断/.test(text))
    return { summary:`${stage}：独审仍有必须修正的问题，AI 返修后没有解决。`, todo:"请先看本阶段的独审意见，再决定点“继续”或手动修改。" };
  if (/本轮已换过一次执行器|本轮已重试过一次|没有其他可用执行器/.test(text))
    return { summary:`${stage}：AI 写的稿件格式或内容校验未通过，自动切换/重试后仍未成功，所以停下。`, todo:"点“继续”会用当前执行器再试一次；也可以在上方失败卡片中换一个执行器重试，或修改本阶段的执行器配置。" };
  if (/本集角色表|交接说明|须提供|须有|格式|表头|字段/.test(text))
    return { summary:`${stage}：AI 写的稿件格式不合格，已自动修正过一次仍未通过，所以停下。`, todo:"点“继续”会让 AI 按格式要求再修正一次；如果反复失败，可在输入框下方换一个起草执行器再继续。" };
  return { summary:`${stage}：连续制作停下了。`, todo:"点“继续”重试；具体原因见下方详情。" };
}

export function ContinuousStopped({ run, reload }:{ run:any; reload:()=>void|Promise<void> }) {
  const act = useAction();
  if (!run || run.state !== "blocked") return null;
  const { summary, todo } = explainStop(run.reason ?? "", stageLabel(run.cursor?.stage, run.cursor?.phase));
  return <section className="mcard continuous-stopped" data-chat-kind="stopped" role="alert">
    <h3>AI 已停下，需要你处理</h3>
    {run.cursor?.stoppedNeeds?.length
      ? <ul className="stopped-needs">{run.cursor.stoppedNeeds.map((n:any)=><li key={n.key}><b>{n.title}</b><span>{n.reason}</span><ContinuousRecoveryNote run={run} needKey={n.key}/>
          <a className="btn ghost" href={`#/e/${run.episode_id}/worksheet?need=${encodeURIComponent(n.key)}`}>查看这一项</a></li>)}</ul>
      : <><p>{run.cursor?.stage==="assets" && run.reason ? rawText(run.reason) : summary}</p><p>{todo}</p></>}
    <ContinuousRecoveryNote run={run}/>
    <button className="btn primary" disabled={act.busy} onClick={() => act.run(async () => { await cmd("resume_continuous", { continuous_id: run.id }); await reload(); })}>继续</button>
    {run.reason && <details><summary>查看原始原因</summary><pre className="chat-prose">{rawText(run.reason)}</pre></details>}
    <ErrorLine error={act.error}/>
  </section>;
}
