export function isKickoffTopic(topic:{status:string;intent?:string;publication?:string;episode_id?:string|null}) {
  return topic.status==="候选" && (topic.intent ?? (topic.status==="候选"?"想拍":"待整理"))==="想拍" && !topic.publication && !topic.episode_id;
}

export function directorText(text:string) {
  return text.replace(/\n来源：[^\n]* sha256=[a-f0-9]{64}\n原卡全文：\n\{[^\n]*?\}(?=。出处原话（不再作为约束）：|\n|$)/g,"");
}
