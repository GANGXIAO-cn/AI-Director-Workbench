"""Publish and editorial handoff passages from the pinned short-film source."""
VIDEO = "milo-leo-short-video"
SHARED = [
    (VIDEO, "publish-pack", "1-25", "发布系统拼接与项目设置", "publish"),
    (VIDEO, "publish-pack", "31,33,35,41,44-50", "逐段配乐对位与发布页验收", "publish delivery"),
    (VIDEO, "templates/publish-pack.md", "1-24,49-52", "发布文案与剪辑对位模板", "publish"),
    (VIDEO, "finecut-handoff", "1-11,15-37", "精剪交接与实际粗剪核对", "delivery"),
]
PROJECT = [
    (VIDEO, "templates/publish-pack.md", "1-52", "系列发布六字段与音乐模板", "publish"),
    (VIDEO, "finecut-handoff", "13", "系列精剪商品必留边界", "delivery"),
]
EDITS = {
    "发布系统拼接与项目设置": {
        "[发布包模板](../templates/publish-pack.md)": "本阶段的发布模板",
        "`正式交付/01_发布包.md`": "`01_发布包.md`",
        "`<asset_root>/发布设置.json`": "随本次任务冻结的发布设置",
        "（MILO加空格）": "（如有设置）", "写MILO这一集做了什么，英文": "写本集主角做了什么，使用项目规定语言",
        "本集英文标题": "本集标题",
        "共三行。": "行数由项目固定行决定。",
        "MILO要做什么或做了什么": "主角要做什么或做了什么",
        "按本集实际人物写（LEO、女主人或其他对手），不硬套“LEO想要、MILO搞砸”，MILO可以真的做成": "按本集实际人物和对手来写，主角也可以真的做成",
        "空格加`hook_emoji`。第二、三行逐字使用`body_fixed_lines`": "按配置追加空格和`hook_emoji`。后续固定行逐字使用`body_fixed_lines`",
        "（`<`和`>`）": "",
    },
    "逐段配乐对位与发布页验收": {
        "；Suno生成长度": "；配乐生成长度",
        "脚本检查三个输入框的格式、固定行、固定标签、禁用字符、正文不含标题和Suno必填项；不判断文案是否好笑或吸引人。发布设置改动后（如/shop上线换最后一行），制作时按现行设置生成，":
            "按项目发布设置检查文案格式、固定行、固定标签、禁用字符和已配置的必填字段。发布设置改动后，新稿使用新设置，已冻结版本按当时的设置回读。",
        "先运行check_publish_pack.py，再按[交付命令](review-delivery.md#复用页面工具)生成和检查正式页面。页面完整呈现六个输入字段": "经工作台检查后生成和核对正式页面。页面完整呈现项目配置的输入字段",
        "Suno段": "音乐段",
    },
    "发布文案与剪辑对位模板": {
        "<本集英文标题>": "<本集标题>",
        "本文件六个输入字段": "项目配置的发布字段", "六字段各自独立复制": "各字段独立复制",
        "交付命令见references/review-delivery.md。": "在交付页执行核验。",
        "MILO <这一集做了什么>": "<项目标题前缀及本集动作>",
        "<一句剧情：谁想要什么，MILO要做什么> 🐱": "<一句剧情：谁想要什么，主角要做什么；按设置补结尾标记>",
        "<发布设置body_fixed_lines第1行>": "<有固定行时按发布设置原样追加>",
        "<发布设置body_fixed_lines第2行>": "<其余固定行按设置顺序追加>",
        "最多2个": "不超过项目设置上限",
    },
    "精剪交接与实际粗剪核对": {
        "`正式交付/03_精剪交接.md`": "`03_精剪交接.md`",
        "[模板](../templates/finecut-handoff.md)": "本阶段的完整精剪交接模板",
        "[连续集制作](continuous-production.md)": "故事组的连续集制作记录",
    },
}
