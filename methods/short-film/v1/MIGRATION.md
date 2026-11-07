# 方法包迁移记录（short-film v1）

2026-11-07 从下列技能只读提取，原技能未修改：

- `~/.codex/skills/milo-leo-story`（SKILL.md、references/story-standard.md、references/story-draft.md、templates/story-draft.md、scripts/check_story.py）
- `~/.codex/skills/milo-leo-short-video`（SKILL.md、config.json、references/story-handoff.md 的承接规则）

## 拆分原则

| 内容 | 去向 |
|---|---|
| 故事因果、看得懂、规则先立后用、时间与连续、检验清单 A–D、F，作者自检与独立审查流程 | 本方法包 `story/`、`review/` |
| PUGGY 风格（家庭荒诞喜剧、无对白肢体喜剧）、MILO/LEO/女主人身份与行为、双后腿行走、猫无对白、单集完整＋轻连续、近期差异与机制库、9:16 与超过 60 秒、商品入景、品牌片尾、发布固定网址与标签、资产根目录 | `configs/projects/puggy.json`（项目设定条目带强度与出处） |
| 新项目 | `configs/projects/_template.json`：空设定、通用规格，首次沉淀后才有项目条目 |
| 检验清单 E 项（系列一致） | 方法包只保留“逐条对照项目设定、必守/可变分级”的通用做法；具体条目来自项目配置 |

## 第 1 阶段之后的参考文件

short-video 的镜头、分段、素材、提示词、两遍独审、发布包、交付参考（camera-rhythm、scene-assets、prompt-format、prompt-review、publish-pack、review-delivery 等）属于第 2 阶段，届时按同一原则拆分；本版方法包不包含它们，运行记录固定的方法版本仅覆盖故事阶段。

## 脚本处理清单

| 原脚本 | 处理 | 状态 |
|---|---|---|
| `check_story.py` | 移植通用部分：三栏交接（梗概、角色表、交接说明）非空、无占位；角色表表头、角色ID格式与唯一、独立身份基准取值。审核状态改由工作台 review/waiver 记录判断，不再读 review.md。 | 已移植：`server/workbench/storycheck.py` |
| `validate_episode.py` | 通用的结构、映射、版本、时长（生成段 >0 且 ≤15 秒、整数毫秒时基）和标识校验移植到第 2 阶段的方案校验；跨技能导入及角色专属检查（固定角色图哈希、商品入景）改为按项目启用。 | 第 2 阶段 |
| `check_publish_pack.py` | 继续使用现有 `--settings` 显式传项目文件（PUGGY 为 `发布设置.json`）；新项目无发布设置时报本项目缺项，不回退个人目录。 | 第 2 阶段 |
| `check_delivery.py`、`render_delivery.py` | 优先直接调用原有渲染/校验行为；未支持显式项目上下文的部分先抽成参数化函数，禁止回退到 asset_root 默认路径。 | 第 2 阶段 |
