
import { useEffect } from "react";

import { usePoll, useQuery } from "./ui";
import { FILM_STAGES } from "./workflow";

/** 实测 117：连续制作停下或等待选择时，在对话流、最近剧集、侧栏和标签标题上提示导演。 */
export type AttentionItem = { episodeId:string; displayCode:string; title:string; projectId:string; projectName:string;
  state:"waiting_external"; reason:string; stage?:string|null; phase?:string|null; updatedAt:number };

export function useAttention() {
  const query = useQuery<AttentionItem[]>("attention_episodes");
  usePoll(true, query.reload, 5000);
  return query.data ?? [];
}

const BASE_TITLE = "AI 导演工作台";
export function useAttentionTitle(items:AttentionItem[]) {
  useEffect(() => {
    document.title = items.length ? items.length+" 集等候外部服务 · "+BASE_TITLE : BASE_TITLE;
  }, [items.map(i => i.episodeId + i.state).join(",")]);
}

export function AttentionTag({ item }:{ item?:AttentionItem }) {
  if (!item) return null;
  return <span className="attention-tag" title={item.reason}>等候外部服务</span>;
}

export function stageLabel(stage?:string|null, phase?:string|null) {
  if (phase === "kickoff") return "开工核对";
  if (phase === "direction") return "01 故事方向";
  return ({assets:"07 出图", delivery:"图文交付", package:"H3 出包"} as Record<string,string>)[stage ?? ""]
    ?? FILM_STAGES.find(([s]) => s === stage)?.[1] ?? "当前阶段";
}
