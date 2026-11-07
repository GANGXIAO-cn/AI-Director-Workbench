"""Build run inputs (the frozen manifest) and the prompt derived from it.

Everything a model reads comes from manifest entries; the trailer only adds the fixed fields
(run id, fingerprint, target ids) that the service then checks on the way back.
"""
from .storycheck import WRITABLE, render_story


def render_settings(snapshot: list[dict]) -> str:
    if not snapshot:
        return "本项目尚无设定条目（首集可在设定为空时独立成集）。"
    label = {"required": "必守", "variable": "可变"}
    return "\n".join(f"[{s['key']}]（{label[s['strength']]}）{s['content']}" for s in snapshot)


def render_kickoff(kickoff: dict, source_text: str | None) -> str:
    lines = [f"来源类型：{ {'import': '导入材料', 'idea': '一个想法', 'topic': '选题卡'}[kickoff['source_type']] }"]
    if kickoff.get("goal"):
        lines.append(f"本集要求：{kickoff['goal']}")
    spec = kickoff["spec"]
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


def creation_inputs(pack: dict, settings_text: str, kickoff_text: str, stage: str,
                    base_content: dict | None, request: str, adopted_story: str | None = None) -> list[dict]:
    items = [dict(role="method", name="story-standard.md", text=pack["texts"]["story_standard"]),
             dict(role="method", name="story-draft.md", text=pack["texts"]["story_draft"]),
             dict(role="project_settings", name="settings.txt", text=settings_text),
             dict(role="kickoff", name="kickoff.txt", text=kickoff_text)]
    if adopted_story is not None:
        items.append(dict(role="adopted_story", name="story.md", text=adopted_story))
    if base_content:
        body = render_story(base_content) if stage == "story" else base_content.get("scenes", "")
        items.append(dict(role="base_version", name="base.md", text=body))
    items.append(dict(role="request", name="request.txt", text=request))
    return items


def review_inputs(pack: dict, settings_text: str, kickoff_text: str, story_text: str) -> list[dict]:
    return [dict(role="method", name="story-review.md", text=pack["texts"]["story_review"]),
            dict(role="method", name="story-standard.md", text=pack["texts"]["story_standard"]),
            dict(role="reviewed_version", name="story.md", text=story_text),
            dict(role="project_settings", name="settings.txt", text=settings_text),
            dict(role="requirements", name="kickoff.txt", text=kickoff_text)]


def prompt_from(entries: list[dict], trailer: str, purpose: str) -> str:
    head = ("你是独立故事审查者。只依据下列材料审查，不使用任何工具，不读取其他文件。"
            if purpose == "review" else
            "你是短片故事作者。只依据下列材料写作，不使用任何工具，不读取其他文件。")
    body = "\n\n".join(f"===== {e['role']} · {e['name']} =====\n{e['text']}" for e in entries)
    return f"{head}\n\n{body}\n\n===== 固定字段（回包原样填写） =====\n{trailer}\n"


def draft_trailer(run_id, fingerprint, artifact_id, base_id, stage) -> str:
    fields = ", ".join(WRITABLE[stage])
    return (f"运行号：{run_id}\n输入指纹：{fingerprint}\n目标对象：{artifact_id}\n基版：{base_id or 'null'}\n"
            f"可写字段：{fields}\n"
            "回包为 kind=draft：changes 中每项 targetId 为目标对象，field 只能是可写字段，value 为该字段完整新文本。"
            "baseVersionId 填基版（为 null 时填 null）。cast 字段必须是含「角色ID|身份与作用|独立身份基准」表头的 Markdown 表格。")


def review_trailer(run_id, fingerprint, version_id, required_keys) -> str:
    keys = "、".join(required_keys) or "（无）"
    return (f"运行号：{run_id}\n输入指纹：{fingerprint}\n被审版本：{version_id}\n遍次：story\n"
            f"必守设定编号：{keys}\n"
            "回包为 kind=review，pass=story，reviewedVersionId 填被审版本。observations 第一条为冷读复述"
            "（objectId 与 versionId 均填被审版本）；每个必守设定编号各写一条 observation，objectId 填该编号、versionId 填被审版本。"
            "issues 中 location.objectId 与 versionId 填被审版本，anchor 写段落或原句位置；违反设定时 category=setting、"
            "settingId 填该设定编号，否则 settingId 为 null。没有问题时 decision=pass 且 issues 为空；"
            "有问题时 decision=return 或 uncertain。")
