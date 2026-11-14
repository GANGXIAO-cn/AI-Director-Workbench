export const FILM_STAGES: [string, string][] = [
  ["story", "01 故事方向"], ["characters", "02 角色"], ["world", "03 世界规则"],
  ["treatment", "04 分场"], ["script", "05 剧本"], ["stress", "06 故事审查"],
  ["asset_design", "07 素材"], ["plan", "08 分镜"],
  ["prompts", "09 提示词"],
];
export const OPTIONAL_FILM_STAGES = new Set(["world", "stress"]);
export const NEXT_FILM_STAGE: Record<string, string> = Object.fromEntries(FILM_STAGES.slice(0, -1).map(([key], i) => [key, FILM_STAGES[i + 1][0]]));
export const FILM_FIELDS: Record<string, [string, string][]> = {
  characters: [["characters", "角色小传"], ["notes", "来源与未定项"]],
  world: [["rules", "世界规则"], ["notes", "边界与依据"]],
  treatment: [["scenes", "分场事件"], ["durationBudget", "时长预算与依据"], ["notes", "说明"]],
  stress: [["retelling","冷读复述"],["scope", "审查范围"], ["diagnosis", "诊断结论"], ["issues", "问题与证据"], ["options", "返修选项"], ["notes", "作者决定"]],
  asset_design: [["strategy", "素材策略"], ["assets", "素材与状态设计"], ["notes", "实际素材说明"]],
  acting: [["scenes", "逐场表演"], ["notes", "说明"]],
};
