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


review_prefix = re.compile(r"^(?:\[Shot \d+\]\s*)+")


class WriterRevisionError(ValueError):
    """The writer must correct its reply before this round can be submitted."""


def writer_changes(source, windows, changes):
    # Review numbers follow the frozen board array. H3 numbers follow playback order.
    numbers = list(shot_ranges(source))
    ordered = sorted(enumerate(windows, 1), key=lambda pair: pair[1].get("order", 0))
    mapping = {}
    group = -1
    for index, window in ordered:
        if len(numbers) == len(windows) or group < 0 or window.get("continuation") != "same":
            group += 1
        if group >= len(numbers):
            raise ValueError("交付提示词的镜头与分镜窗口无法对应")
        mapping[index] = numbers[group]
    if group + 1 != len(numbers):
        raise ValueError("交付提示词的镜头与分镜窗口无法对应")
    result = []
    for change in changes:
        prefix = review_prefix.match(change)
        if not prefix:
            result.append(change)
            continue
        indices = [int(n) for n in re.findall(r"\d+", prefix[0])]
        if not all(i in mapping for i in indices):
            raise ValueError("审片点名的镜号不在本段采用分镜内")
        if all(i == mapping[i] and list(mapping.values()).count(i) == 1 for i in indices):
            result.append(change)
            continue
        targets = " ".join(f"[Shot {n}]" for n in dict.fromkeys(mapping[i] for i in indices))
        links = "、".join(f"{windows[i-1]['id']} 对应提示词第 {mapping[i]} 镜" for i in indices)
        result.append(f"{targets} 依照交付分镜对应关系：{links}。只修点名窗口的动作，保留同镜其余内容。原审片意见：" + change[prefix.end():])
    return result


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
    edited = {edit["shot"] for edit in edits}
    if ranges.keys() != revised_ranges.keys() or any(
        original[slice(*ranges[n])] != text[slice(*revised_ranges[n])] for n in ranges.keys() - edited
    ):
        raise ValueError("局部返工不能改变镜头边界或未点名镜头的正文")
    for shot in sorted(edited):
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


def compile_revision(data, delivery, source, changes, bindings, directory, *, assignment=None):
    from .videosegment import episode_expert

    materials = revision_inputs(source, changes, bindings)
    if assignment is not None:
        materials["本轮返工任务"] = assignment
    key = sha256_bytes(canonical(materials))
    folder = directory / "prompt-revisions" / key
    record = folder / "edits.json"
    notes = [*changes, *(b["note"] for b in bindings)]
    # Continuity frames were already bound in the previous H3 request.
    slots = [] if changes else [b["slot"] for b in bindings]
    targets = {int(n) for change in changes if (prefix := review_prefix.match(change))
               for n in re.findall(r"\d+", prefix[0])}

    def checked(edits):
        edited = {e["shot"] for e in edits}
        if targets and edited != targets:
            raise ValueError(f"只改评审点名的镜号：{sorted(targets)}。本次漏改 {sorted(targets-edited)}，多改 {sorted(edited-targets)}")
        revised = apply_shot_edits(source, edits, notes, slots)
        for b in bindings:
            ref = f"<Picture {b['slot']}>"
            if ref in source and ref not in revised:
                raise ValueError("没有保留已绑定的承接图 " + ref)
        return revised

    if record.is_file():
        edits = json.loads(record.read_bytes())["edits"]
        return checked(edits)
    executor, slot, connection, _ = episode_expert(data, delivery, "create", "package")
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    protected = {number: words for number, (a, b) in shot_ranges(source).items()
                 if (words := literal_text(source[a:b]))}
    request = (
        "你是本集镜头提示词编辑。reviews 和 frames.note 是给你的内部意见。先确定意见指向的镜头，再把要求落实为人物、动作、道具位置和镜头结束时可见的状态。"
        "每处只返回 shot 镜号、before 原镜头内逐字引用的短句、after 可直接拍摄的新句，沿用原稿语言。英文稿写英文。"
        "镜号仅填在 shot 字段。after 为单段镜头正文，跨镜承接直接描述本镜可见的起始位置和动作，不在正文中引用 [Shot N]。"
        "不要翻译、附加或转述评语，不写修改过程。保留其余剧情、机位、时间码、对白与引用。首版新绑定的参考图需写清图号和实际状态。返工时沿用已有承接，只改本轮点名镜头，同镜未点名窗口保留。"
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
            revised = checked(edits)
            break
        except (ValueError, ValidationError) as exc:
            if attempt:
                raise WriterRevisionError("写稿人补修后仍未通过：" + str(exc)) from exc
            from dataclasses import replace
            req = replace(req, prompt=request + "\n上次答复：" + json.dumps(result.structured, ensure_ascii=False)
                          + "\n仅纠正以下校验问题：" + str(exc))
    write_atomic(record, canonical(dict(edits=edits, writer=slot)))
    return revised


def submitted_prompt(receipt):
    texts = [n.get("fieldValue") for n in receipt.get("nodeInfoList", [])
             if str(n.get("nodeId")) == "263" and n.get("fieldName") == "text"]
    if len(texts) != 1 or not isinstance(texts[0], str) or not texts[0].strip():
        raise ValueError("H3 提交记录缺少唯一的提示词正文")
    return texts[0]


def confirmed_rework(data, delivery, segment, out, change, bindings):
    from .videosegment import frozen_delivery
    from .videoreview import decision_schema, media_verdict, MediaCallError
    from .storage import file_sha256

    number = int(out.name.removeprefix("round-"))
    previous = out.parent / f"round-{number-1}"
    path = previous / "trial.json"
    receipt = json.loads(path.read_bytes()) if path.is_file() else {}
    if not receipt.get("task"):
        raise ValueError("找不到上一版 H3 已接单的提交记录，无法确定返工底稿")
    source = submitted_prompt(receipt)
    review_path = previous / "expert/visual-review.json"
    review = json.loads(review_path.read_bytes()) if review_path.is_file() else {}
    decision = review.get("visualReview") or {}
    change = change or decision.get("localChange")
    if not change:
        raise ValueError("上一版缺少本轮返工意见，无法确定要改的镜头")
    root, manifest = frozen_delivery(data, delivery)
    board_path = "交接来源/制作方案.json"
    if file_sha256(root / board_path) != manifest["files"][board_path]:
        raise ValueError("返修所用分镜与交付版本不一致")
    windows = [s for s in json.loads((root / board_path).read_bytes())["shots"] if s["segment"] == segment]
    work = dict(segment=segment, round=number, board=windows, expertReason=decision.get("reason",""))
    if not review_prefix.match(change):
        # Older decisions can contain a local instruction without a numbered prefix.
        indices = (review.get("independentReview") or decision).get("affectedShots")
        if not indices:
            shot = dict(type="integer", enum=list(range(1, len(windows)+1)))
            schema = dict(type="object", additionalProperties=False, required=["shots"],
                          properties=dict(shots=dict(type="array", minItems=1, items=shot)))
            try:
                scope = media_verdict(data, delivery, "expert", "prompt_scope", out,
                    "从本轮返工意见定位需修的分镜窗口，按 board 数组从1编号，只返回涉及的窗口。"
                    + json.dumps([work, change], ensure_ascii=False), schema)["result"]
            except ValueError as exc:
                raise MediaCallError("专家定位镜头回包无效，将自动重试：" + str(exc)) from exc
            indices = scope["shots"]
        change = " ".join(f"[Shot {i}]" for i in indices) + " " + change
    instructions = writer_changes(source, windows, [change])
    basis = sha256_bytes(canonical([work, source, instructions, bindings]))
    feedback_path = out / "prompt-feedback.json"
    last = json.loads(feedback_path.read_bytes()) if feedback_path.is_file() else {}
    attempt = 1
    if last.get("basis") == basis:
        attempt = last["attempt"] + 1
        work.update(previousCandidate=last["candidate"], expertFeedback=last["reason"])
    work["attempt"] = attempt
    candidate = compile_revision(data, delivery, source, instructions, bindings, out.parent, assignment=work)
    prompt = ("你是本集专家。提交 H3 前，对照上一版实际提交稿、本轮点名窗口和你的返工决定，"
              "核对新稿是否落实具体动作、保留未点名镜头和同镜内其余窗口。仅决定确认提交或退回写稿人；"
              "退回时给出本轮范围内具体补修意见。\n" +
              json.dumps(dict(segment=segment, round=number, board=windows, instruction=instructions[0],
                              expertReason=decision.get("reason",""), lastFeedback=work.get("expertFeedback"),
                              source=source, candidate=candidate), ensure_ascii=False))
    try:
        verdict = media_verdict(data, delivery, "expert", "prompt_confirmation", out/"prompt-checks"/str(attempt),
                                prompt, decision_schema(["确认提交","退回补修"]))["result"]
    except ValueError as exc:
        raise MediaCallError("专家确认回包无效，将自动重试：" + str(exc)) from exc
    if verdict["action"] == "退回补修":
        write_atomic(feedback_path, canonical(dict(basis=basis, attempt=attempt, reason=verdict["reason"], candidate=candidate)))
        raise WriterRevisionError("专家退回本轮提示词，写稿 AI 将自动补修：" + verdict["reason"])
    return candidate
