export function isKickoffTopic(topic: {status: string; episode_id?: string | null}) {
  return topic.status === "候选" && !topic.episode_id;
}
