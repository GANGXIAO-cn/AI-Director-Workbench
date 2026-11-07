"""Hard format checks for a story draft, ported from milo-leo-story/scripts/check_story.py.

Only structure is checked here (fields present, cast table shape). Review status lives in the
workbench review/waiver records instead of review.md.
"""
import re

CAST_COLUMNS = ("角色ID", "身份与作用", "独立身份基准")
PLACEHOLDER = re.compile(r"<[^>]*>|待填|TODO", re.I)
STORY_FIELDS = ("synopsis", "cast", "handoff", "notes")
SCRIPT_FIELDS = ("scenes", "notes")
WRITABLE = {"story": STORY_FIELDS, "script": SCRIPT_FIELDS}


def check_cast_table(block: str) -> list[str]:
    header, ids = None, []
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if set(CAST_COLUMNS) <= set(cells):
            if header or len(set(cells)) != len(cells):
                raise ValueError("本集角色表列名重复")
            header = cells
            continue
        if not header or all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if len(cells) != len(header):
            raise ValueError("本集角色表列数与表头不符")
        row = dict(zip(header, cells))
        if not all(row[k] and not PLACEHOLDER.search(row[k]) for k in CAST_COLUMNS):
            raise ValueError("本集角色表缺少实际角色信息")
        rid = row["角色ID"]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", rid) or rid in ids:
            raise ValueError("角色ID无效或重复：" + rid)
        if row["独立身份基准"] not in ("需要", "不需要"):
            raise ValueError("独立身份基准须写需要／不需要")
        ids.append(rid)
    if not ids:
        raise ValueError("本集角色表须含角色ID、身份与作用、独立身份基准及实际数据")
    return ids


def check_story(content: dict) -> list[str]:
    """Return a list of hard errors (empty means structurally valid)."""
    errors = []
    for name, label in (("synopsis", "故事梗概"), ("handoff", "交接说明")):
        value = (content.get(name) or "").strip()
        if not value or PLACEHOLDER.search(value):
            errors.append(label + "须有实际内容，不得保留模板占位")
    try:
        check_cast_table(content.get("cast") or "")
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def check_content(stage: str, content: dict) -> list[str]:
    if stage == "story":
        return check_story(content)
    if not (content.get("scenes") or "").strip():
        return ["分场剧本须有实际内容"]
    return []


def render_story(content: dict) -> str:
    parts = [("故事梗概", "synopsis"), ("本集角色表", "cast"), ("交接说明", "handoff"),
             ("可选的开发辅助", "notes")]
    return "\n\n".join(f"## {title}\n\n{content[key].strip()}" for title, key in parts
                       if (content.get(key) or "").strip()) + "\n"
