"""Build run inputs (the frozen manifest) and the prompt derived from it.

Everything a model reads comes from manifest entries; the trailer only adds the fixed fields
(run id, fingerprint, target ids) that the service then checks on the way back.
"""
from .methodpack import method_entries
from .stages import WRITABLE, render
from .specs import creative_spec


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
        note = ("以下导入材料只作为故事来源，按方法重写成故事稿，不当作已审稿。"
                if kickoff["source_type"] == "import" else "以下是导演给出的原句。")
        lines += ["", note, source_text]
    return "\n".join(lines)


STAGE_CONTRACT = {
    "script": "把采用故事写成可见表演。scenes 是 JSON 数组，每场有 id、heading、action、dialogue、sound。"
              "每行是一个剧情阶段或回合，id 保持稳定以便镜头引用。按需填 performance、choice、response、change、payoff，写清主角选择如何改变局面。"
              "为有分段需要的场次填写文本字段 proposedSegments（拟生成段）、watchPurpose（观看作用与要保留的过程）、omit（可省略的操作）、budgetNote（时长初估与依据）。最终秒数由采用方案中的镜头时长汇总。",
    "plan": "依据采用剧本按动作和观众需要看到的信息设计镜头。shots 是 JSON 数组，每镜写 id、segment、order、"
            "duration_ms（整数毫秒）、action、camera、continuity、origin（scene 填真实场号，line 填原句）。"
            "按需填 attention（注意对象）、gaze（视线）、reaction（可见反应）、sound（声源）、cut（剪点）和 device（设备视点）。需要借镜时只引用提供的 bookmarkId，refs 逐项写 mode A/B/C/D 和 conditions。"
            "每个时间窗给出 attention（观看任务）、camera（构图、机位或运镜）、txtAnchor（动作中适合在 TXT 原样保留且在本段唯一的定位短句）。"
            "同一机位继续拍下一动作时 continuation 填 same。长镜有持续观看理由时写 holdReason。常规起草直接填写镜头字段；rhythmTable 供导入已有镜头充分性联合表。"
            "逐镜填写 visibleRoles（本窗口实际可见的本集角色 ID 数组，空镜用 []）、propStates（道具ID到可见状态的对象，无则用 {}；道具 ID 用小写英文字母开头，可含数字和连字符）。同镜延续按窗口分别登记。身份图用途按可见镜头确定，看图观察留给实际查看原图的人填写。"
            "用 performance 写可见表演。剧本有对白时，将原句放进起声镜的 dialogueCues 数组，各行含 speaker、text、direction、镜内 offset_ms、句后 pause_ms。"
            "可以用 throughShot 写同段结束镜号。mode 填 words 或 characters 并写整数 rate（每分钟词或字），有必要时用 count 指定实际发音字词数。"
            "只在材料确有试读或指定秒数时使用 mode=reading 和 read_ms。无对白段保留动作、视线、反应和音效。"
            "segments 是 JSON 数组，每段写 id、order、strategy（flex 或 grid）。同段镜长合计 5–15 整秒。",
    "prompts": "segments 是以已采用段号为键的 JSON 对象。每段写 prompt_format=readable_v3、txt、refs 和 sound。若提供本集逐段参考与时间骨架，逐段保留栏目标题与顺序、原图绑定、refs 中已给出的 shots 镜号及其顺序和固定块。每个时窗时间码独占一行，紧跟以起镜、同镜延续或切镜及中文逗号起笔的正文。refs 使用素材清单里的"
               "assetVersionId、kind、role、label（图片1 等）与 grid 布尔值。grid 只取本段九宫格。"
               "有采用台词时在起声时间窗逐句保留原文、说话者、语气与跨切点接续。TXT 交代各图用途、起幅、按镜段表分时的表演与声音，以 MM:SS.mmm-MM:SS.mmm 连续覆盖本段；"
               "每段从零计时。按提供的镜头充分性联合表逐窗保留 TXT 定位短句、时间和接续方式。sound 为对象，bgm 与 silent 为布尔值，ambience 与 dialogue 为字符串数组。保持已采用的机位和接点。素材清单仅为登记资料，无法据此声称看过原图。",
    "publish": "根据本集内容与发布设置写 title、body、tags、"
               "music 和 cues。tags 为字符串数组，cues 为逐段含 segment、at、action 的数组。使用发布依据中的真实段号、全片设计时窗和可见动作，写明配乐进入、压低、硬停或不放的事件。"
               "无背景音乐时 music 填 backup。按 additional_fields 逐项填写，标准配乐字段以其 key 保存，自定义字段使用 extraFields 对象。不要虚构商品、品牌链接或平台数据。",
}


def creation_inputs(pack: dict, settings_text: str, kickoff_text: str, stage: str,
                    base_content: dict | None, request: str, adopted_story: str | None = None, profile=None) -> list[dict]:
    items = method_entries(pack, stage, profile)
    settings_text = stage_settings(pack, stage, profile, settings_text)
    if stage != "story":
        items.append(dict(role="stage_contract", name=f"{stage}.txt", text=STAGE_CONTRACT[stage]))
    items += [dict(role="project_settings", name="settings.txt", text=settings_text),
              dict(role="kickoff", name="kickoff.txt", text=kickoff_text)]
    if adopted_story is not None:
        upstream = {"script": "story", "plan": "script", "prompts": "plan", "publish": "prompts"}[stage]
        items.append(dict(role=f"adopted_{upstream}", name=f"{upstream}.md", text=adopted_story))
    if base_content:
        body = render(stage, base_content)
        items.append(dict(role="base_version", name="base.md", text=body))
    items.append(dict(role="request", name="request.txt", text=request))
    return items


def review_inputs(pack: dict, settings_text: str, kickoff_text: str, story_text: str, profile=None) -> list[dict]:
    settings_text = stage_settings(pack, "story_review", profile, settings_text)
    return [*method_entries(pack, "story_review", profile),
            dict(role="reviewed_version", name="story.md", text=story_text),
            dict(role="project_settings", name="settings.txt", text=settings_text),
            dict(role="requirements", name="kickoff.txt", text=kickoff_text)]


def prompt_from(entries: list[dict], trailer: str, purpose: str) -> str:
    head = ("你是本次任务的独立审查者。只依据下列材料审查，不使用任何工具，不读取其他文件。"
            if purpose in ("review", "cold_read", "comparison") else
            "你是本次短片制作任务的作者。只依据下列材料写作，不使用任何工具，不读取其他文件。")
    body = "\n\n".join(f"===== {e['role']} · {e['name']} =====\n" + (e['text'] if e.get('text') is not None
                         else f"（图片，按顺序附在本次请求中，sha256={e['sha256']}）") for e in entries)
    return f"{head}\n\n{body}\n\n===== 固定字段（回包原样填写） =====\n{trailer}\n"


def draft_trailer(run_id, fingerprint, artifact_id, base_id, stage, entries=()) -> str:
    writable = WRITABLE[stage]
    if stage == "publish":
        import json
        from .publishing import LEGACY_KEYS, settings
        cfg = next((json.loads(e["text"]) for e in entries if e["role"] == "publish_settings"), None)
        enabled = {f["key"] for f in settings(cfg)["additional_fields"]}
        writable = [k for k in writable if k not in LEGACY_KEYS or k in enabled]
    fields = ", ".join(writable)
    guidance = ("spine、timeline、props、causes 是按复杂度选填的文本，分别保存故事脊、时间线、规则与道具表、因果核对表；无需为了填表重复正文。cast 字段必须是含「角色ID|身份与作用|独立身份基准」表头的 Markdown 表格。adaptation 只用于参考片节拍改编映射，填可解析的 JSON；普通故事省略此字段，文字说明放 notes。"
                if stage == "story" else "数组和对象字段的 value 使用完整、可解析的 JSON 字符串。")
    if stage == "story" and any(e["role"]=="adaptation_source" for e in entries):
        guidance += ("本次是参考包改编。adaptation 填 JSON 数组，至少写一行实际借鉴或改写的事件。"
                     "每行 pack 和 unit 取自本次 adaptation_source 的 pack 和 units[].code，"
                     "original 写原事件，adapted 写本集新事件，reason 写取舍理由。仅修订其他字段时可沿用基稿的对照。")
    return (f"运行号：{run_id}\n输入指纹：{fingerprint}\n目标对象：{artifact_id}\n基版：{base_id or 'null'}\n"
            f"可写字段：{fields}\n"
            "回包为 kind=draft：changes 中每项 targetId 为目标对象，field 只能是可写字段，value 为该字段完整新文本。"
            "baseVersionId 填基版（为 null 时填 null）。" + guidance)


def review_trailer(run_id, fingerprint, version_id, required_keys) -> str:
    keys = "、".join(required_keys) or "（无）"
    return (f"运行号：{run_id}\n输入指纹：{fingerprint}\n被审版本：{version_id}\n遍次：story\n"
            f"必守设定编号：{keys}\n"
            "回包为 kind=review，pass=story，reviewedVersionId 填被审版本。observations 第一条为冷读复述"
            "（objectId 与 versionId 均填被审版本）；每个必守设定编号各写一条 observation，objectId 填该编号、versionId 填被审版本。"
            "issues 中 location.objectId 与 versionId 填被审版本，anchor 写段落或原句位置；违反设定时 category=setting、"
            "settingId 填该设定编号，否则 settingId 为 null。没有问题时 decision=pass 且 issues 为空；"
            "有问题时 decision=return 或 uncertain。")


def segment_review_trailer(run_id, fingerprint, version_id, pass_name, segment, required_keys) -> str:
    extra = ("本遍为冷读：只描述实际看到的角色、方向、动作和不确定处，settingId 一律为 null。"
             if pass_name == "cold_read" else
             "本遍为对照：阅读第一遍观察、同一份 TXT/原图及故事、设定、方案与本集意图，检查叙事、连续性、相邻接点及遗漏。")
    return (f"运行号：{run_id}\n输入指纹：{fingerprint}\n被审版本：{version_id}\n遍次：{pass_name}\n被审段：{segment}\n"
            f"必守设定编号：{'、'.join(required_keys) or '（无）'}\n"
            f"回包为 kind=review，pass={pass_name}，reviewedVersionId 填被审版本。observations 至少一条，objectId 填被审段，"
            "versionId 填被审版本，unit 用 segment 或 grid_cell，anchor 写秒数、原句或格号。" + extra +
            "没有问题时 decision=pass 且 issues 为空；有问题时 decision=return 或 uncertain。")
