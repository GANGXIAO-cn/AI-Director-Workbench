"""Apply stage-local review decisions to the actual H3 shot prose."""
from collections import Counter
import json
import re

from jsonschema import ValidationError, validate

from .executors.base import ExecRequest
from .officialh3 import QUOTED_LITERAL, has_unquoted_chinese
from .readable import WINDOW
from .storage import canonical, sha256_bytes, write_atomic


def shot_ranges(text):
    official = text.startswith("subject_definitions:")
    if official:
        start, end = "detailed_description:", "overall_soundscape:"
        marker = r"^\[Shot (\d+)\]"
    else:
        start, end = ("【镜头与动作】", "【声音与文字】") if "【镜头与动作】" in text else ("镜头描述：", "运镜边界：")
        marker = "^" + WINDOW.pattern
    left = text.index(start) + len(start)
    right = text.index(end, left)
    marks = list(re.finditer(marker, text[left:right], re.M))
    if not marks:
        raise ValueError("原稿没有可对应的镜头")
    ranges = {}
    for i, mark in enumerate(marks):
        number = int(mark[1]) if official else i + 1
        stop = left + marks[i + 1].start() if i + 1 < len(marks) else right
        ranges[number] = (left + mark.end(), stop)
    return ranges


def literal_text(content):
    return QUOTED_LITERAL.findall(content) + re.findall(r"<d>.*?</d>", content)


def apply_shot_edits(text, edits, notes=(), slots=()):
    ranges = shot_ranges(text)
    original = text
    replacements = []
    refs = set(re.findall(r"<(?:Picture|Subject|Video|Audio) \d+>", text))
    refs.update(f"<Picture {n}>" for n in slots)
    markers = []
    for edit in edits:
        labels = list(dict.fromkeys(re.findall(r"\[Shot \d+\]", edit["after"])))
        if labels:
            markers.append(f"第 {edit['shot']} 镜含 {'、'.join(labels)}")
    if markers:
        raise ValueError("镜头正文引用了镜号：" + "；".join(markers) + "。承接应写为本镜可见的动作或状态。")
    for edit in edits:
        if edit["shot"] not in ranges:
            raise ValueError("改写指定的镜头不存在")
        a, b = ranges[edit["shot"]]
        old, new = edit["before"], edit["after"]
        if (
            not old.strip()
            or not new.strip()
            or new.strip() == old.strip()
            or text[a:b].count(old) != 1
        ):
            raise ValueError("修改必须对应镜头内唯一的原句和新的画面描述")
        if re.search(r"[\r\n]", new):
            raise ValueError(f"第 {edit['shot']} 镜的新正文含换行，镜头局部替换需要单段文字")
        if text.startswith("subject_definitions:") and has_unquoted_chinese(new):
            raise ValueError(f"第 {edit['shot']} 镜的英文正文含引文以外的中文")
        if (
            any(n and n.strip() in new for n in notes)
            or re.search(
                r"\b(?:reviewer|review feedback|previous (?:attempt|version)|(?:replace|revise|rewrite|fix) (?:only |the |this )*(?:shot|prompt|sentence))\b",
                new,
                re.I,
            )
        ):
            raise ValueError(f"第 {edit['shot']} 镜的正文含审片意见或修改指令")
        if set(re.findall(r"<(?:Picture|Subject|Video|Audio) \d+>", new)) - refs:
            raise ValueError("修改引用了未绑定的素材")
        if re.findall(r"\b\d\d:\d\d(?:\.\d+)?\b", old) != re.findall(
            r"\b\d\d:\d\d(?:\.\d+)?\b", new
        ):
            raise ValueError("局部返工不能改变既有镜头时间码")
        pos = text.index(old, a, b)
        replacements.append((pos, pos + len(old), new))
    for slot in slots:
        if not any(f"<Picture {slot}>" in edit["after"] for edit in edits):
            raise ValueError(f"镜头没有落实第 {slot} 张承接图的画面")
    if not replacements:
        raise ValueError("没有落实修改的镜头")
    last = len(text)
    for a, b, new in sorted(replacements, reverse=True):
        if b > last:
            raise ValueError("同一处镜头修改重叠")
        text = text[:a] + new + text[b:]
        last = a
    missing = []
    revised_ranges = shot_ranges(text)
    for shot in sorted({edit["shot"] for edit in edits}):
        before = original[slice(*ranges[shot])]
        after = text[slice(*revised_ranges[shot])]
        lost = Counter(literal_text(before)) - Counter(literal_text(after))
        if lost:
            missing.append(f"第 {shot} 镜缺少 " + "、".join(lost.elements()))
    if missing:
        raise ValueError("原对白或画面文字未保留：" + "；".join(missing))
    return text


FIELDS = dict(
    shot={"type": "integer"}, before={"type": "string"}, after={"type": "string"}
)
SCHEMA = dict(
    type="object",
    additionalProperties=False,
    required=["edits"],
    properties={
        "edits": dict(
            type="array",
            items=dict(
                type="object",
                properties=FIELDS,
                required=list(FIELDS),
                additionalProperties=False,
            ),
        )
    },
)


def revision_inputs(source, changes, bindings):
    return dict(
        source=source,
        reviews=list(changes),
        frames=[{k: b[k] for k in ("slot", "note", "imageHash")} for b in bindings],
    )


def compile_revision(data, delivery, source, changes, bindings, directory):
    from .videosegment import episode_expert

    materials = revision_inputs(source, changes, bindings)
    key = sha256_bytes(canonical(materials))
    folder = directory / "prompt-revisions" / key
    record = folder / "edits.json"
    notes = [*changes, *(b["note"] for b in bindings)]
    slots = [b["slot"] for b in bindings]
    if record.is_file():
        edits = json.loads(record.read_bytes())["edits"]
        return apply_shot_edits(source, edits, notes, slots)
    executor, slot, connection, _ = episode_expert(data, delivery, "create", "package")
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    protected = {number: words for number, (a, b) in shot_ranges(source).items()
                 if (words := literal_text(source[a:b]))}
    request = (
        "你是本集镜头提示词编辑。reviews 和 frames.note 是给你的内部意见。先确定意见指向的镜头，再把要求落实为人物、动作、道具位置和镜头结束时可见的状态。"
        "每处只返回 shot 镜号、before 原镜头内逐字引用的短句、after 可直接拍摄的新句，沿用原稿语言。英文稿写英文。"
        "镜号仅填在 shot 字段。after 为单段镜头正文，跨镜承接直接描述本镜可见的起始位置和动作，不在正文中引用 [Shot N]。"
        "不要翻译、附加或转述评语，不写修改过程。保留其余剧情、机位、时间码、对白与引用。新参考图要在相关动作中写清对应 Picture 和实际状态。"
        "每镜须逐字保留的对白和画面文字见下表。改到包含它们的句子时，在同镜保留原文、引号和出现次数。\n"
        + json.dumps(protected, ensure_ascii=False) + "\n以下为原稿及工作台记录。\n" + canonical(materials).decode()
    )
    req = ExecRequest(
            "h3-edit-" + key[:16],
            "revise",
            "draft",
            request,
            SCHEMA,
            "ephemeral",
            work,
            slot.get("model"),
            slot.get("effort"),
            connection=connection,
        )
    from .production_steps import step
    for attempt in range(2):
        step("写稿 AI 正在局部修改镜头提示词")
        result = executor.start(req)
        if not result.ok:
            from .videoreview import MediaCallError
            raise MediaCallError("本段提示词改写未完成：" + str(result.error_class))
        try:
            validate(result.structured, SCHEMA)
            edits = result.structured["edits"]
            if changes:
                targeted = {int(n) for change in changes for prefix in re.findall(r"^((?:\[Shot \d+\] ?)+)", change) for n in re.findall(r"\d+", prefix)}
                if targeted and all(change.startswith("[Shot ") for change in changes):
                    edited = {e["shot"] for e in edits}
                    if not bindings:
                        if edited != targeted:
                            raise ValueError("只改评审点名的镜号："+"、".join(map(str,sorted(targeted))))
                    else:
                        missing = targeted - edited
                        slot_targets = {}
                        for b in bindings:
                            s = b["slot"]
                            found = set()
                            if isinstance(b.get("shot"), int):
                                found.add(b["shot"])
                            note_text = str(b.get("note") or "")
                            found.update(int(n) for n in re.findall(r"(?:\[Shot\s*|Shot\s*|第\s*)(\d+)(?:\]|镜)?", note_text, re.I))
                            slot_targets[s] = found
                        extra = set()
                        for e in edits:
                            if e["shot"] in targeted:
                                continue
                            matched_slots = [s for s in slots if f"<Picture {s}>" in e["after"]]
                            if not matched_slots:
                                extra.add(e["shot"])
                            elif any(slot_targets[s] and e["shot"] not in slot_targets[s] for s in matched_slots):
                                extra.add(e["shot"])
                        if missing or extra:
                            raise ValueError("只改评审点名的镜号（及指定承接图落点镜）："+"、".join(map(str,sorted(targeted))))
            revised = apply_shot_edits(source, edits, notes, slots)
            break
        except (ValueError, ValidationError) as exc:
            if attempt:
                raise ValueError("写稿人补修后仍未通过：" + str(exc)) from exc
            from dataclasses import replace
            req = replace(req, prompt=request + "\n上次答复：" + json.dumps(result.structured, ensure_ascii=False)
                          + "\n仅纠正以下校验问题：" + str(exc))
    write_atomic(record, canonical(dict(edits=edits, writer=slot)))
    return revised
