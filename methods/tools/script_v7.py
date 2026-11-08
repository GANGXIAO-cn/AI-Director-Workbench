"""Story-to-script handoff and scoped production examples from the pinned video skill."""
VIDEO = "milo-leo-short-video"
SHARED = [
    (VIDEO, "story-handoff", "1-15", "故事接收与版本依据", "script"),
    (VIDEO, "story-handoff", "17-44", "承接已审故事与跨段动作", "script"),
    (VIDEO, "story-ip", "1-23", "剧情阶段与可见表演", "script"),
    (VIDEO, "series-continuity", "1-7,23-25", "近期差异与已有故事组", "topic script"),
    (VIDEO, "series-continuity", "9-21", "连续集与单集兑现", "script"),
    (VIDEO, "continuous-production", "1-42", "跨集来源、依赖与复核", "group"),
    (VIDEO, "templates/production-plan.md", "68-73", "九格状态与镜头时刻", "grid_examples"),
]
PROJECT = [
    (VIDEO, "series-continuity", "9,19", "系列主角与回访规模", "script"),
    (VIDEO, "examples", "1-17", "系列既有作品", "script"),
    (VIDEO, "examples", "19-45", "系列人物、改招与局部压缩", "script"),
    (VIDEO, "examples", "47-67", "系列九格示意：四镜头的拒绝与反应", "grid_examples"),
    (VIDEO, "examples", "69-91", "系列九格示意：固定机位的接物与驶离", "grid_examples"),
    (VIDEO, "examples", "93-103", "系列动作设计与短触返修", "plan"),
    (VIDEO, "examples", "105-115", "系列审查记录与抽帧边界", "comparison"),
    (VIDEO, "examples", "117-135", "系列提示词局部写法与完整性", "prompts"),
]
EDITS = {
    "故事接收与版本依据": {
        "承接milo-leo-story产出的`故事稿.md`时读取": "承接故事阶段已采用的故事稿时读取",
        "由milo-leo-story维护": "由故事阶段的方法维护",
        "可用同级故事技能 `scripts/check_story.py 故事稿.md` 核对必要交接信息和明确审核状态；记录位于其他路径时传 `--review-record`。":
            "核对故事稿的交接信息、采用版本及该版本关联的独立审查记录。",
    },
    "剧情阶段与可见表演": {
        "由milo-leo-story负责": "由故事阶段负责",
        "不只靠胖猫衣服和名字": "不只靠角色的服装和名字",
    },
    "近期差异与已有故事组": {"asset_root/系列设定.md与系列台账.json": "项目提供的系列设定和制作台账"},
    "连续集与单集兑现": {
        "用户指定LEO重点集尊重安排，整体仍以MILO为核心。": "本集重点角色与系列主角依项目设定和用户安排。",
        "30—50集是发展规模": "系列规模由项目确定",
    },
}
