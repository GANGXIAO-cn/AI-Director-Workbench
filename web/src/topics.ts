export function isKickoffTopic(topic:{status:string;intent?:string;publication?:string;episode_id?:string|null}) {
  return topic.status==="候选" && (topic.intent ?? (topic.status==="候选"?"想拍":"待整理"))==="想拍" && !topic.publication && !topic.episode_id;
}
