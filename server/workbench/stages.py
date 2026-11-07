"""Per-stage content rules: writable fields, normalisation of model strings, hard checks, rendering."""
import json
import re

from . import timeline
from .storycheck import check_story, render_story

WRITABLE = {
    "story": ("synopsis", "cast", "handoff", "notes", "adaptation"),
    "script": ("scenes", "notes"),
    "plan": ("shots", "segments", "notes"),
    "prompts": ("segments",),
    "publish": ("title", "body", "tags", "suno_title", "suno_style", "suno_exclude", "music", "cues"),
}
JSON_FIELDS = {("script", "scenes"), ("plan", "shots"), ("plan", "segments"), ("prompts", "segments"),
               ("story", "adaptation"), ("publish", "tags"), ("publish", "cues")}
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


def check_segment_prompt(seg_id: str, entry: dict, duration_ms: int, strategy: str) -> list[dict]:
    problems = []
    refs = entry.get("refs", [])
    txt = entry.get("txt", "")
    if not txt.strip():
        problems.append(dict(segment=seg_id, problem="TXT 为空"))
    if [r.get("label") for r in refs] != expected_labels(refs):
        problems.append(dict(segment=seg_id, problem="参考标签与上传顺序不符"))
    labels = {r.get("label") for r in refs}
    for label in sorted(set(LABEL_RE.findall(txt)) - labels):
        problems.append(dict(segment=seg_id, problem=f"正文引用了不存在的参考 {label}"))
    counts = {k: sum(1 for r in refs if r["kind"] == k) for k in KIND_LABEL}
    if not refs or len(refs) > 12 or counts["image"] > 9 or counts["video"] > 1 or counts["audio"] > 3:
        problems.append(dict(segment=seg_id, problem="参考数量超出限制（1–12 项，图片≤9、视频≤1、音频≤3）"))
    if strategy == "grid" and (len(refs) != 1 or not refs[0].get("grid")):
        problems.append(dict(segment=seg_id, problem="grid 段的交付输入只能是本段一张九宫格"))
    if len({r.get("assetVersionId") for r in refs}) != len(refs):
        problems.append(dict(segment=seg_id, problem="同段参考重复"))
    for a, b in time_gaps(txt, duration_ms):
        problems.append(dict(segment=seg_id, problem=f"时间句缺少 {a / 1000:g}–{b / 1000:g} 秒"))
    return problems


def check_content(stage: str, content: dict, plan: dict | None = None) -> list[str]:
    if stage == "story":
        return check_story(content)
    if stage == "script":
        scenes = content.get("scenes") or []
        return [] if scenes and all((s.get("action") or "").strip() for s in scenes) else ["分场剧本须有实际内容"]
    if stage == "plan":
        if not content.get("shots") or not content.get("segments"):
            return ["制作方案须有镜头和生成段"]
        # Over-long segments may be saved and adopted; delivery and packaging block them until split.
        return [p["problem"] if not p["segment"] else f"{p['segment']}：{p['problem']}" for p in timeline.check(content)
                if "超过 15 秒" not in p["problem"]]
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
                                                                                       seg["strategy"])]
        return errors
    if stage == "publish":
        return [f"{label}不能为空" for key, label in (("title", "发布标题"), ("body", "正文"), ("suno_title", "Suno Title"),
                                                    ("suno_style", "Style of Music")) if not str(content.get(key) or "").strip()]
    return []


def render(stage: str, content: dict) -> str:
    if stage == "story":
        return render_story(content)
    if stage == "script":
        return "\n\n".join(f"## {s.get('id')} {s.get('heading', '')}\n{s.get('action', '')}"
                           + (f"\n对白：{s['dialogue']}" if s.get("dialogue") else "")
                           + (f"\n声音：{s['sound']}" if s.get("sound") else "") for s in content.get("scenes", []))
    return json.dumps(content, ensure_ascii=False, indent=2)
