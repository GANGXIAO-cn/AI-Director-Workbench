"""Hard format checks for a story draft, ported from milo-leo-story/scripts/check_story.py.

Only structure is checked here (fields present, cast table shape). Review status lives in the
workbench review/waiver records instead of review.md.
"""
import json
import re

CAST_COLUMNS = ("角色ID", "身份与作用", "独立身份基准")
PLACEHOLDER = re.compile(r"<[^>]*>|待填|TODO", re.I)
STORY_AIDS = {"spine": "故事脊", "timeline": "时间线", "props": "规则与道具表", "causes": "因果核对表"}
STORY_FIELDS = ("synopsis", "cast", "handoff", "notes", *STORY_AIDS)
SCRIPT_FIELDS = ("scenes", "notes")
WRITABLE = {"story": STORY_FIELDS, "script": SCRIPT_FIELDS}


def cast_records(block: str) -> dict:
    if not isinstance(block,str):
        raise ValueError("本集角色表须为 Markdown 表格文本")
    header, roles = None, {}
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
        if not re.fullmatch(r"[a-z][a-z0-9-]*", rid) or rid in roles:
            raise ValueError("角色ID无效或重复：" + rid)
        if row["独立身份基准"] not in ("需要", "不需要"):
            raise ValueError("独立身份基准须写需要／不需要")
        roles[rid] = dict(description=row["身份与作用"], identityRequired=row["独立身份基准"] == "需要")
    if not roles:
        raise ValueError("本集角色表须含角色ID、身份与作用、独立身份基准及实际数据")
    return roles


def check_cast_table(block: str) -> list[str]:
    return list(cast_records(block))


def adaptation_errors(value, sources):
    if not sources:
        return []
    if not isinstance(value,list) or not value:
        return ["参考包改编故事须有原事件、新事件和取舍理由"]
    allowed = {(s["pack"],unit["code"]) for s in sources for unit in s["units"]}
    errors = []
    for n, row in enumerate(value,1):
        keys = ("pack","unit","original","adapted","reason")
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and row[k].strip() for k in keys):
            errors.append(f"改编对照第 {n} 行缺少来源、原事件、新事件或取舍理由")
        elif (row["pack"],row["unit"]) not in allowed:
            errors.append(f"改编对照第 {n} 行的来源编号未在本稿的参考包中登记")
    return errors


def check_story(content: dict) -> list[str]:
    """Return a list of hard errors (empty means structurally valid)."""
    errors = []
    for name, label in (("synopsis", "故事梗概"), ("handoff", "交接说明")):
        value = content.get(name)
        if not isinstance(value,str) or not value.strip() or PLACEHOLDER.search(value):
            errors.append(label + "须有实际内容，不得保留模板占位")
    for key, title in STORY_AIDS.items():
        value = content.get(key)
        if value is not None and (not isinstance(value, str) or PLACEHOLDER.search(value)):
            errors.append(title + "须为实际文本，可留空")
    if content.get("notes") is not None and not isinstance(content["notes"],str):
        errors.append("其他开发说明须为文本")
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
             *((title, key) for key, title in STORY_AIDS.items()), ("可选的开发辅助", "notes")]
    sections = [f"## {title}\n\n{content[key].strip()}" for title, key in parts
                if isinstance(content.get(key),str) and content[key].strip()]
    adaptation = content.get("adaptation")
    if adaptation is not None:
        sections.append("## 改编依据\n\n" + json.dumps(adaptation,ensure_ascii=False,indent=2))
    return "\n\n".join(sections) + "\n"
