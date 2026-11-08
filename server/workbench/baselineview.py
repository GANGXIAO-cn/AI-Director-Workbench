"""Readable project rules and the source files frozen at episode kickoff."""
import json

from .errors import Rejected

PROMPT_LABELS = {
    "visual_style": "画面风格", "motion": "运动", "reference_use": "参考素材",
    "cats_silent": "猫的声音", "frame_text": "画面文字", "grid_reference": "九宫格参考",
}


def source_blocks(kind, data):
    text = data.decode("utf-8")
    if kind == "soundStandard":
        title = text.splitlines()[0].removeprefix("# ").strip() if text else ""
        return [dict(key="sound", label=title or "声音设定", text=text)]
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise Rejected("基准文件不是有效的 JSON") from exc
    sections = ("blocks",) if kind == "promptStandard" else ("roles", "pair_rules")
    if not isinstance(document, dict) or not any(s in document for s in sections):
        raise Rejected("基准文件缺少固定块或角色栏目")
    blocks = []
    for section in sections:
        values = document.get(section, {})
        if not isinstance(values, dict):
            raise Rejected(f"基准栏目格式错误：{section}")
        for key, value in values.items():
            prompt = section == "blocks"
            field = "zh" if prompt else "video_zh"
            if not isinstance(value, dict) or not isinstance(value.get(field), str) or not value[field].strip():
                raise Rejected(f"基准缺少正文：{section}.{key}.{field}")
            label = PROMPT_LABELS.get(key, key) if prompt else value.get("name", key)
            if section == "pair_rules":
                label = f"{label} 同框"
            blocks.append(dict(key=f"{section}.{key}", label=label, text=value[field],
                               version=value.get("version" if prompt else "video_zh_version"),
                               description=value.get("zh") if not prompt else None))
    return blocks


def block_by_key(kind, data, key):
    for block in source_blocks(kind, data):
        if block["key"] == key:
            return block
    raise Rejected("当前来源没有此固定块")


def replace_block(kind, data, key, text, version):
    if kind == "soundStandard":
        if key != "sound":
            raise Rejected("声音设定缺少指定固定块")
        return text.encode("utf-8")
    document = json.loads(data)
    section, name = key.split(".", 1)
    record = document.get(section, {}).get(name)
    if not isinstance(record, dict):
        raise Rejected("本集来源没有此固定块")
    field, ver = ("zh", "version") if section == "blocks" else ("video_zh", "video_zh_version")
    record[field], record[ver] = text, version
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def segment_use(key, previous, current, segment, entry, cast, names):
    txt = entry.get("txt", "")
    if any(phrase and phrase in txt for phrase in (previous, current)):
        return "提示词逐字引用"
    if key == "sound":
        return "分段对照审查读取声音设定"
    if key in {"blocks.visual_style", "blocks.motion", "blocks.reference_use", "blocks.frame_text"}:
        return "分段通用固定块"
    if key == "blocks.grid_reference" and segment.get("strategy") == "grid":
        return "九宫格分段"
    roles = {role for role, name in names.items() if role.casefold() in cast or name.casefold() in cast}
    if key == "blocks.cats_silent" and roles.intersection({"milo", "leo"}):
        return "已确认猫角色出镜"
    if key.startswith("roles.") and key[6:] in roles:
        return "已确认角色出镜"
    if key == "pair_rules.milo+leo" and {"milo", "leo"} <= roles:
        return "已确认同框"
    return None
