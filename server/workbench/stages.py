"""Per-stage content rules: writable fields, normalisation of model strings, hard checks, rendering."""
import json
import re

from . import timeline, readable, rhythm, acting, shotrefs
from .storycheck import STORY_FIELDS, check_story, render_story

WRITABLE = {
    "story": (*STORY_FIELDS, "adaptation"),
    "script": ("scenes", "notes"),
    "plan": ("shots", "segments", "notes", "rhythmTable"),
    "prompts": ("segments",),
    "publish": ("title", "body", "tags", "suno_title", "suno_style", "suno_exclude", "music", "cues", "extraFields"),
}
JSON_FIELDS = {("script", "scenes"), ("plan", "shots"), ("plan", "segments"), ("prompts", "segments"),
               ("story", "adaptation"), ("publish", "tags"), ("publish","extraFields"), ("publish", "cues")}
NEXT = {"story": "script", "script": "plan", "plan": "prompts", "prompts": "publish"}
TIME_RE = re.compile(r"(\d{2}):(\d{2})(?:\.(\d{3}))?\s*[-–—~]\s*(\d{2}):(\d{2})(?:\.(\d{3}))?")
LABEL_RE = re.compile(r"(?:图片|视频|音频)\d+")
KIND_LABEL = {"image": "图片", "video": "视频", "audio": "音频"}


def normalize(stage, field, value):
    if (stage, field) in JSON_FIELDS and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            if (stage, field) == ("script", "scenes"):
                return [dict(id="全文", action=value)]
            raise
    return value


def to_ms(m, s, ms):
    return (int(m) * 60 + int(s)) * 1000 + int(ms or 0)


def time_gaps(txt: str, duration_ms: int) -> list[list[int]]:
    """Ranges of the segment not covered by any time sentence (e.g. a 13 s segment written to 9 s)."""
    spans = sorted((to_ms(*m.groups()[:3]), to_ms(*m.groups()[3:])) for m in TIME_RE.finditer(txt))
    gaps, cursor = [], 0
    for a, b in spans:
        if a > cursor:
            gaps.append([cursor, a])
        cursor = max(cursor, b)
    if cursor < duration_ms:
        gaps.append([cursor, duration_ms])
    return gaps


def expected_labels(refs: list[dict]) -> list[str]:
    counts, out = {}, []
    for ref in refs:
        counts[ref["kind"]] = counts.get(ref["kind"], 0) + 1
        out.append(KIND_LABEL[ref["kind"]] + str(counts[ref["kind"]]))
    return out


def check_segment_prompt(seg_id: str, entry: dict, duration_ms: int, strategy: str, shot_ids=()) -> list[dict]:
    problems = []
    refs = entry.get("refs", [])
    txt = entry.get("txt", "")
    if not isinstance(refs, list) or any(not isinstance(r, dict) or r.get("kind") not in KIND_LABEL for r in refs):
        return [dict(segment=seg_id, problem="refs 须为含有效素材类型的数组")]
    for ref in refs:
        if "shots" in ref:
            shots = ref["shots"]
            if (not isinstance(shots, list) or not shots or any(not isinstance(s, str) or s not in shot_ids for s in shots)
                    or len(set(shots)) != len(shots)):
                problems.append(dict(segment=seg_id, problem="作用镜头须为本段实际镜号且不得重复"))
    if not isinstance(entry.get("sound", {}), dict):
        problems.append(dict(segment=seg_id, problem="sound 须为声音对象，包含 bgm、ambience、dialogue"))
    if not isinstance(txt, str):
        return problems + [dict(segment=seg_id, problem="TXT 须为文本")]
    if not txt.strip():
        problems.append(dict(segment=seg_id, problem="TXT 为空"))
    if [r.get("label") for r in refs] != expected_labels(refs):
        problems.append(dict(segment=seg_id, problem="参考标签与上传顺序不符"))
    labels = {r.get("label") for r in refs}
    for label in sorted(set(LABEL_RE.findall(txt)) - labels):
        problems.append(dict(segment=seg_id, problem=f"正文引用了不存在的参考 {label}"))
    counts = {k: sum(1 for r in refs if r["kind"] == k) for k in KIND_LABEL}
    if not 1 <= counts["image"] <= 9 or len(refs) > 12 or counts["video"] > 1 or counts["audio"] > 3:
        problems.append(dict(segment=seg_id, problem="参考数量超出限制（1–12 项，图片1–9、视频≤1、音频≤3）"))
    if strategy == "grid" and (len(refs) != 1 or not refs[0].get("grid")):
        problems.append(dict(segment=seg_id, problem="grid 段的交付输入只能是本段一张九宫格"))
    if len({r.get("assetVersionId") for r in refs}) != len(refs):
        problems.append(dict(segment=seg_id, problem="同段参考重复"))
    if entry.get("prompt_format") == "readable_v3":
        try:
            readable.validate(entry, strategy, duration_ms)
        except (TypeError, ValueError) as exc:
            problems.append(dict(segment=seg_id, problem=str(exc)))
    for a, b in time_gaps(txt, duration_ms):
        problems.append(dict(segment=seg_id, problem=f"时间句缺少 {a / 1000:g}–{b / 1000:g} 秒"))
    return problems


def check_content(stage: str, content: dict, plan: dict | None = None) -> list[str]:
    if stage == "story":
        return check_story(content)
    if stage == "script":
        scenes = content.get("scenes") or []
        if not isinstance(scenes, list) or not scenes or not all(isinstance(s, dict) and isinstance(s.get("action"), str)
                                                                  and s["action"].strip() for s in scenes):
            return ["分场剧本须有实际内容"]
        ids = [s.get("id") for s in scenes]
        return [] if all(isinstance(i, str) and i.strip() for i in ids) and len(ids) == len(set(ids)) else ["剧情阶段须有唯一场号供镜头追溯"]
    if stage == "plan":
        if not content.get("shots") or not content.get("segments"):
            return ["制作方案须有镜头和生成段"]
        # Over-long segments may be saved and adopted; delivery and packaging block them until split.
        if content.get("rhythmTable") or shotrefs.plan_enabled(content):
            try:
                rhythm.check_alignment(rhythm.plan_rows(content), rhythm.plan_table(content))
                if shotrefs.plan_enabled(content):
                    shotrefs.board_rows(content)
            except (ValueError, KeyError, TypeError) as exc:
                return [str(exc)]
        problems = [p["problem"] if not p["segment"] else f"{p['segment']}：{p['problem']}" for p in timeline.check(content)
                    if "超过 15 秒" not in p["problem"]]
        if not problems and any("dialogueCues" in shot for shot in content["shots"]):
            problems.extend(acting.report(content)["errors"])
        return problems
    if stage == "prompts":
        if not plan:
            return ["没有已采用的制作方案"]
        lay = {s["id"]: s for s in timeline.layout(plan)["segments"]}
        errors = []
        for seg_id, seg in lay.items():
            entry = (content.get("segments") or {}).get(seg_id)
            if not entry:
                errors.append(f"{seg_id}：缺少提示词")
                continue
            errors += [f"{p['segment']}：{p['problem']}" for p in check_segment_prompt(seg_id, entry, seg["duration_ms"],
                                                                                       seg["strategy"], seg["shots"])]
        if not errors:
            try:
                rhythm.check(plan,content["segments"])
            except (KeyError,ValueError,TypeError) as exc:
                errors.append(str(exc))
        return errors
    if stage == "publish":
        errors = [f"{label}不能为空" for key,label in (("title","发布标题"),("body","正文")) if not isinstance(content.get(key),str) or not content[key].strip()]
        if not isinstance(content.get("tags",[]),list) or not all(isinstance(t,str) and t.strip() for t in content.get("tags",[])):
            errors.append("话题标签须为文本数组")
        cues = content.get("cues",[])
        if not isinstance(cues,list) or not all(isinstance(c,dict) and isinstance(c.get("at"),str) and isinstance(c.get("action"),str) for c in cues):
            errors.append("音乐对位须逐项填写时间和动作")
        return errors
    return []


def render(stage: str, content: dict) -> str:
    if stage == "story":
        return render_story(content)
    if stage == "script":
        labels = {"performance": "关键表演", "choice": "选择与办法", "response": "阻碍与回应",
                  "change": "新局面", "payoff": "预期与回收", "dialogue": "对白", "sound": "声音",
                  "proposedSegments": "拟生成段", "watchPurpose": "观看作用与保留过程", "omit": "可省略的操作",
                  "budgetNote": "时长初估与依据"}
        sections = []
        for scene in content.get("scenes", []):
            lines = [f"## {scene.get('id')} {scene.get('heading', '')}", scene.get("action", "")]
            lines.extend(f"{label}：{scene[key]}" for key, label in labels.items() if scene.get(key))
            sections.append("\n".join(lines))
        if content.get("notes"):
            sections.append(content["notes"])
        return "\n\n".join(sections)
    return json.dumps(content, ensure_ascii=False, indent=2)
