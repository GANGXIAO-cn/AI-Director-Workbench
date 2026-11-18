"""Build run inputs (the frozen manifest) and the prompt derived from it.

Everything a model reads comes from manifest entries; the trailer only adds the fixed fields
(run id, fingerprint, target ids) that the service then checks on the way back.
"""
from .methodpack import method_entries
from .stages import WRITABLE, render
from .specs import creative_spec
from . import workflow, timeline


def render_settings(snapshot: list[dict]) -> str:
    if not snapshot:
        return "本项目尚无设定条目（首集可在设定为空时独立成集）。"
    label = {"required": "必守", "variable": "可变"}
    return "\n".join(f"[{s['key']}]（{label[s['strength']]}）{s['content']}" for s in snapshot)


def stage_settings(pack, stage, profile, settings_text):
    # Keep comparison fingerprints reproducible for episodes opened with older packs.
    if pack["id"] in {"short-film@v1", "short-film@v2"}:
        return settings_text
    keys = pack.get("stages", {}).get(stage, [])
    annex = pack.get("projectFiles", {})
    if any(key not in pack["files"] for key in keys) and not any(key in annex.get(profile, {}) for key in keys):
        return settings_text + "\n本项目未提供此阶段的方法包专属案例与制作规则。"
    return settings_text


def render_kickoff(kickoff: dict, source_text: str | None) -> str:
    lines = [f"来源类型：{ {'import': '导入材料', 'idea': '一个想法', 'topic': '选题卡'}[kickoff['source_type']] }"]
    if kickoff.get("goal"):
        lines.append(f"本集要求：{kickoff['goal']}")
    spec = creative_spec(kickoff["spec"])
    lines.append("本集规格：" + "；".join(f"{k}={v}" for k, v in sorted(spec.items())))
    if kickoff["relation"] == "continue":
        lines.append("前集关系：承接前集。前集遗留事实：")
        lines += [f"- {fact}" for fact in kickoff["carried_facts"]] or ["- （无）"]
    else:
        lines.append("前集关系：独立成集。")
    if source_text is not None:
        note = ("导演明确选定以下完整剧本为定稿来源。保留情节和准确对白，只整理当前阶段需要的结构；原文件只读，不回到01重写。"
                if kickoff.get("sourceIntent") == "locked_screenplay" else
                "以下导入材料作为改编来源，按导演目标开发，不当作已采用稿。"
                if kickoff["source_type"] == "import" else "以下是导演给出的原句。")
        lines += ["", note, source_text]
    return "\n".join(lines)


DECISION_CONTRACT = "若提供了导演决定清单，逐条在 decisionNotes 中回答。每条必须有 decision 键，值逐字复制该项的 code（如 D01）。已落实的条目 status 填 applied，where 写场号、镜号或段号，quote 逐字摘录本稿中体现该决定的一句原文。本阶段确实不涉及的条目 status 填 not_applicable 并写 reason。不得省略任何一条，不得改写决定的文字。决定之间或与开工资料冲突时，在 notes 中指出并停止在冲突处，不自行取舍。"

SCRIPT_LOCKED_CONTRACT = ("导演已锁定剧本原文。只切分和拆解。scenes 每场仅写 id、heading、action、dialogue、sound；heading、action 按原文顺序逐字照抄，"
                          "保留标点、对白原位，不重叠。dialogue 逐句摘自本场 action，sound 仅引用本场写明的声音。"
                          "另写 breakdown 数组，每项 sceneId、roles、props、durationEstimateSec、sourceQuote，sourceQuote 逐字摘自本场 action。原文未写的制作细节只报 supplements。")
SCRIPT_CONTRACT = ("把采用故事写成可见表演。scenes 是 JSON 数组，每场 id、heading、action、dialogue、sound；"
                   "按需填 performance、choice、response、change、payoff、budgetNote。按稳定场号写出可见的选择与结果。")
STAGE_CONTRACT = {
    **workflow.CONTRACTS,
    "script": SCRIPT_CONTRACT,
    "plan": "依据采用剧本按动作和观众需要看到的信息设计镜头。shots 是 JSON 数组，每镜写 id、segment、order、"
            "duration_ms（整数毫秒）、action、camera、continuity、origin（scene 填真实场号，line 填原句）。"
            "按需填 attention（注意对象）、gaze（视线）、reaction（可见反应）、sound（声源）、cut（剪点）和 device（设备视点）。需要借镜时只引用提供的 bookmarkId，refs 逐项写 mode A/B/C/D 和 conditions。"
            "每个时间窗给出 attention（观看任务）、camera（构图、机位或运镜）、txtAnchor（动作中适合在 TXT 原样保留且在本段唯一的定位短句）。"
            "同一机位继续拍下一动作时 continuation 填 same。长镜有持续观看理由时写 holdReason。常规起草直接填写镜头字段；rhythmTable 供导入已有镜头充分性联合表，未导入时省略该字段。"
            "逐镜填写 visibleRoles（本窗口实际可见的本集角色 ID 数组，空镜用 []）、propStates（道具ID到可见状态的对象，无则用 {}；每个状态值写单行文本，使用逗号连接细节，禁止分号、竖线与换行。道具 ID 沿用素材编号，以小写英文字母开头，可含数字、下划线和连字符）。同镜延续按窗口分别登记。身份图用途按可见镜头确定，看图观察留给实际查看原图的人填写。"
            "用 performance 写可见表演。剧本有对白时，将原句放进起声镜的 dialogueCues 数组，各行含 speaker、text、direction、镜内 offset_ms、句后 pause_ms。"
            "可以用 throughShot 写同段结束镜号。mode 填 words 或 characters 并写整数 rate（每分钟词或字），有必要时用 count 指定实际发音字词数。"
            "只在材料确有试读或指定秒数时使用 mode=reading 和 read_ms。无对白段保留动作、视线、反应和音效。"
            "segments 是 JSON 数组，每段写 id、order、strategy（flex 或 grid）、可选 purpose（观看作用）、omit（可省略的过程）。同段镜长合计 5–15 整秒。",
    "prompts": "segments 按段号提交，每段写 txt（MiniMax 官方 H3 完整英文正文）和 sound（bgm、silent 布尔值，ambience、dialogue 字符串数组）。引用与作用窗口由程序从冻结骨架继承。沿用采用分镜、风格、原语言台词。每段独立编号，全参考用 Ref2VA 六栏，保持官方标签与 Shot 切点格式。",
    "publish": "根据本集内容与发布设置写 title、body、tags、"
               "music 和 cues。tags 为字符串数组，cues 为逐段含 segment、at、action 的数组。使用发布依据中的真实段号、全片设计时窗和可见动作，写明配乐进入、压低、硬停或不放的事件。"
               "无背景音乐时 music 填 backup。按 additional_fields 逐项填写，标准配乐字段以其 key 保存，自定义字段使用 extraFields 对象。不要虚构商品、品牌链接或平台数据。",
}


STAGE_CONTRACT["script"] += "\n" + timeline.shooting_contract()
SCRIPT_LOCKED_CONTRACT += "\n" + timeline.shooting_contract()
STAGE_CONTRACT["plan"] += ("\n若采用剧本有 shootingSegments，segments 沿用其 id、order 和时长预算，不能重新按整场自由分段。"
    "shots 每条表示段内一个观看窗口，每条 id 必须唯一，segment 填所属拍摄段号；同镜延续也使用不同窗口 id，用 continuation=same 连接，不重复 id。真实镜号由连续窗口关系推导，不需要另填一张镜号表。把动作、观看对象和摄影变化写清后再输出，不新增审核任务。"
    "每个段内观看窗口为1–3秒整数，duration_ms只能为1000、2000或3000；同镜延续也按此平铺，不能用小数时间或一条整段动作代替。"
    "提示词应能逐窗照表写，不留到提示词或交付再补镜头设计。")


def creation_inputs(pack: dict, settings_text: str, kickoff_text: str, stage: str,
                    base_content: dict | None, request: str, adopted_story: str | None = None, profile=None, source_intent=None) -> list[dict]:
    if stage == "prompts":
        from . import unifiedmethods
        pack = unifiedmethods.load()
    items = method_entries(pack, stage, profile)
    if stage == "script" and source_intent == "locked_screenplay":
        items = [item for item in items if item.get("methodSource") != pack["files"].get("script_contract")]
    settings_text = stage_settings(pack, stage, profile, settings_text)
    if stage != "story":
        items.append(dict(role="stage_contract", name=f"{stage}.txt", text=SCRIPT_LOCKED_CONTRACT if stage=="script" and source_intent=="locked_screenplay" else STAGE_CONTRACT[stage]))
    items += [dict(role="project_settings", name="settings.txt", text=settings_text),
              dict(role="kickoff", name="kickoff.txt", text=kickoff_text)]
    if adopted_story is not None:
        upstream = next((k for k, v in workflow.NEXT.items() if v == stage), "prompts")
        items.append(dict(role=f"adopted_{upstream}", name=f"{upstream}.md", text=adopted_story))
    if base_content:
        body = render(stage, base_content)
        items.append(dict(role="base_version", name="base.md", text=body))
    items.append(dict(role="request", name="request.txt", text=request))
    return items


def review_inputs(pack: dict, settings_text: str, kickoff_text: str, story_text: str, profile=None, stage="story") -> list[dict]:
    if stage == "plan":
        return [*method_entries(pack, "plan_review", profile),
                dict(role="reviewed_version", name="分镜.json", text=story_text),
                dict(role="project_settings", name="settings.txt", text=settings_text),
                dict(role="requirements", name="kickoff.txt", text=kickoff_text)]
    settings_text = stage_settings(pack, "story_review", profile, settings_text)
    return [*method_entries(pack, "story_review", profile),
            dict(role="reviewed_version", name="story.md", text=story_text),
            dict(role="project_settings", name="settings.txt", text=settings_text),
            dict(role="requirements", name="kickoff.txt", text=kickoff_text)]


def document_entries(entries):
    """Adapt the legacy transport instruction without changing frozen inputs."""
    return [dict(e, text=e["text"].replace('必须在 changes 中提交 field=characters，其 value 为完整角色数组的 JSON 文本。', '必须提交 characters 字段，内容为完整角色数组。'))
            if e.get("role") == "stage_contract" and isinstance(e.get("text"), str) else e
            for e in entries]


def prompt_from(entries: list[dict], trailer: str, purpose: str, document=False) -> str:
    head = ("你是本次任务的独立审查者。只依据下列材料审查，不使用任何工具，不读取其他文件。"
            if purpose in ("review", "cold_read", "comparison") else
            "你是本次短片制作任务的作者。只依据下列材料写作，不使用任何工具，不读取其他文件。")
    if purpose == "bounded_judgment":
        head = "你负责本次限定范围的只读判断。只依据提供的材料，不使用工具，不读取其他文件。"
    body = "\n\n".join(f"===== {e['role']} · {e['name']} =====\n" + (e['text'] if e.get('text') is not None
                         else f"（图片，按顺序附在本次请求中，sha256={e['sha256']}）") for e in entries)
    heading = "本次回复要求"
    return f"{head}\n\n{body}\n\n===== {heading} =====\n{trailer}\n"


def draft_trailer(stage, entries=(), mode="changes", dialogue=False) -> str:
    writable = WRITABLE[stage]
    if stage == "publish":
        import json
        from .publishing import LEGACY_KEYS, settings
        cfg = next((json.loads(e["text"]) for e in entries if e["role"] == "publish_settings"), None)
        enabled = {f["key"] for f in settings(cfg)["additional_fields"]}
        writable = [k for k in writable if k not in LEGACY_KEYS or k in enabled]
    fields = ", ".join(writable)
    if stage == "publish":
        fields += "\nmusic 只填 backup（备用）或 enabled（启用）。配乐描述写入对应风格字段，分段处理写入 cues。"
        if settings(cfg).get("copy_format") == "instrumental_v1":
            fields += "\nsuno_style 写出具体速度，例如 112 BPM；suno_exclude 包含 vocals。tags 只填本集新增标签。"
    if mode == "document":
        return (f"可写字段：{fields}\n"
                + ('直接输出一个 JSON 对象，不加代码块，前后也不写任何说明文字：{"kind":"draft","fields":{字段名: 字段值}}。'
                   "fields 只放本次要修改的字段，字段名只能取自上述可写字段。" if dialogue else
                   # 实测 153：新稿按 Antigravity 的自然写法要求“裸稿件”，即顶层直接是要修改的字段。
                   '直接输出本阶段稿件的 JSON 对象，不加代码块，前后也不写任何说明文字：{字段名: 字段值, …}。'
                   "只放本次要修改的字段，字段名只能取自上述可写字段。")
                + "数组和对象字段直接写成 JSON 数组或对象，不要转成字符串；文本字段写字符串。"
                '字符串里的英文双引号必须写成 \\"。')
    guidance = ("本轮01只写 synopsis 完整梗概和 notes 开发说明；人物小传由02另存，不必填写旧版角色表或交接表。adaptation 只用于已提供参考包的改编映射，填可解析的JSON。"
                if stage == "story" else "数组和对象字段的 value 使用完整、可解析的 JSON 字符串。")
    if stage == "story" and any(e["role"]=="adaptation_source" for e in entries):
        guidance += ("本次是参考包改编。adaptation 填 JSON 数组，至少写一行实际借鉴或改写的事件。"
                     "每行 pack 和 unit 取自本次 adaptation_source 的 pack 和 units[].code，"
                     "original 写原事件，adapted 写本集新事件，reason 写取舍理由。仅修订其他字段时可沿用基稿的对照。")
    return (f"可写字段：{fields}\n"
            "提交 changes 数组，每项只写 field（可写字段名）和 value（完整新文本）。"
            + guidance)


def review_trailer(required_keys, stage="story") -> str:
    if stage == "plan":
        return (f"遍次：plan\n"
                "observations记录分镜实际观察，"
                "objectId填段号或窗口编号；issues定位具体原句和影响。"
                "仅违反已提供设定时填写settingId，其他为null。不要补造故事审核的逐条编号观察。"
                "无问题decision=pass、issues=[]；有问题decision=return或uncertain。")
    keys = "、".join(required_keys) or "（无）"
    return (f"遍次：story\n"
            f"必守设定编号：{keys}\n"
            "observations 第一条为冷读复述"
            "（objectId 填“全文”）；每个必守设定编号各写一条 observation，objectId 填该编号。"
            "issues 中 location.anchor 写段落或原句位置；违反设定时 category=setting、"
            "settingId 填该设定编号，否则 settingId 为 null。没有问题时 decision=pass 且 issues 为空；"
            "有问题时 decision=return 或 uncertain。")


def segment_review_trailer(pass_name, segment, required_keys) -> str:
    extra = ("本遍为冷读：检查最终输入的表达、标签和文字自洽；settingId 一律为 null。"
             if pass_name == "cold_read" else
             "本遍为对照：检查本段提示词是否忠实翻译已采用的剧本、表演、镜头及素材用途，定位遗漏、错引和表达偏离。"
             "已采用方案及制作补充作为依据继承，不重新裁决其制作可行性。项目设定只检查本段表达是否违背；不要求每段把全部设定各填一条观察，未涉及的设定无需补齐。"
             "适用导演决定仍逐条观察，objectId 填决定编号；未落实记 category=decision 并填写 decisionId。"
             "对设定或制作补充的可选观察，objectId 可填输入中实际提供的设定编号或补充 code，也可把编号写在本段观察的 anchor。"
             "真实表达矛盾引用本稿原句与对应来源，不要求重复抄写整套角色标准或重新证明上游方案。")
    return (f"遍次：{pass_name}\n被审段：{segment}\n"
            + f"项目必守设定参考（仅检查本段表达是否违背，非逐条填表要求）：{'、'.join(required_keys) or '（无）'}\n"
            + ("observations 至少包含一条本段观察。" if pass_name == "cold_read" else f"observations 至少包含一条 objectId={segment} 的本段观察。")
            + "unit 用 segment 或 grid_cell，anchor 写秒数、原句或格号。" + extra +
            "尚未生成的视频、实际触达、轴线、动态剪接及声画同步未验证，只在 observations 说明未测，不因缺少这些证据生成 issues 或 uncertain。"
            "不将静态参考图当作动作完成证明，也不声称动态已通过。没有本阶段实际表达问题时 decision=pass 且 issues 为空；真实表达问题用 return 或 uncertain 并定位原句。")


def judgment_trailer(document=False):
    import json
    example = dict(kind="discussion", reply='{"业务字段":"按本次判断任务约定填写"}', directorRequest="")
    if document:
        example["fields"] = {}
    else:
        example["changes"] = []
    return ("本次只做指定判断，不起草或修改稿件。只返回完整外层 JSON 对象；reply 是业务 JSON 对象编码后的字符串。"
            "kind 固定为 discussion，directorRequest 留空，修改字段保持空。唯一适用的外层示例：\n"
            + json.dumps(example, ensure_ascii=False))
