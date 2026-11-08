"""Project-defined copy fields and deterministic platform assembly."""
import re

from .errors import Rejected

CORE = {"title": "发布标题", "body": "正文", "tags": "话题标签"}
MUSIC_FIELDS = (("suno_title", "Suno Title"), ("suno_style", "Style of Music"), ("suno_exclude", "Exclude Styles"))
LEGACY_KEYS = dict(MUSIC_FIELDS)
DEFAULTS = dict(title_prefix="", title_max_chars=100, body_fixed_lines=[], fixed_tags=[],
                max_extra_tags=10, forbidden_chars=[], additional_fields=[])


def settings(raw):
    if raw is None:
        return DEFAULTS.copy()
    if not isinstance(raw, dict):
        raise Rejected("发布设置须为对象")
    cfg = dict(DEFAULTS, **raw)
    if "additional_fields" not in raw:
        # Saved v1 settings supported the same six fixed fields in every project.
        cfg["additional_fields"] = [dict(key=k, label=v, required=k!="suno_exclude") for k,v in MUSIC_FIELDS]
    for key in ("max_extra_tags", "title_max_chars"):
        if type(cfg[key]) != int or cfg[key] < (1 if key=="title_max_chars" else 0):
            raise Rejected("标题和标签数量上限须为有效整数")
    for key in ("body_fixed_lines","fixed_tags","forbidden_chars"):
        if not isinstance(cfg[key],list) or not all(isinstance(v,str) and v.strip() for v in cfg[key]):
            raise Rejected("固定行、标签和禁用字符须为文本列表")
    if not isinstance(cfg["title_prefix"],str) or not isinstance(cfg.get("hook_emoji",""),str):
        raise Rejected("标题前缀与结尾标记须为文本")
    if not isinstance(cfg["additional_fields"],list):
        raise Rejected("扩展发布字段须为列表")
    if cfg.get("copy_format") not in (None, "instrumental_v1"):
        raise Rejected("未知发布包格式")
    if cfg.get("copy_format") == "instrumental_v1" and not cfg.get("hook_emoji"):
        raise Rejected("配乐发布包须配置正文结尾表情")
    seen = set(CORE) | {"music","cues","extraFields","__proto__","constructor","prototype"}
    for field in cfg["additional_fields"]:
        if not isinstance(field,dict):
            raise Rejected("发布字段须为对象")
        key=field.get("key")
        if not isinstance(key,str) or not re.fullmatch("[a-z][a-z0-9_]{0,50}",key) or key in seen:
            raise Rejected("发布字段编号须为唯一的小写英文标识")
        if not isinstance(field.get("label"),str) or not field["label"].strip() or type(field.get("required", False)) != bool:
            raise Rejected("发布字段须有名称及有效的必填标记")
        seen.add(key)
    if cfg.get("copy_format") == "instrumental_v1" and not LEGACY_KEYS.keys() <= seen:
        raise Rejected("配乐发布包须保留三个音乐字段")
    return cfg


def field_values(content, cfg):
    extra = content.get("extraFields") or {}
    if not isinstance(extra,dict):
        raise Rejected("扩展发布字段须为对象")
    values = {}
    for field in cfg["additional_fields"]:
        key = field["key"]
        value = content.get(key, "") if key in LEGACY_KEYS else extra.get(key, "")
        if not isinstance(value,str) or (field.get("required") and not value.strip()):
            raise Rejected(field["label"]+"须有实际文本")
        values[key]=value
    return values


COPY_HEADINGS = {"title": "标题", "body": "正文", "tags": "话题标签", "suno_title": "Title",
                 "suno_style": "Style of Music", "suno_exclude": "Exclude Styles"}


def read_copy_blocks(markdown):
    """Read the labelled, independently copyable text blocks of a published pack."""
    fields = {}
    heading = None
    block = None
    for line in markdown.splitlines():
        if block is not None:
            if line.strip() == "```":
                if heading in fields:
                    raise Rejected(f"同一标题下有多个代码块：{heading}")
                fields[heading] = "\n".join(block)
                block = None
            else:
                block.append(line)
        elif line.startswith("#"):
            heading = line.lstrip("#").strip()
        elif heading is not None and line.strip() == "```text":
            block = []
    if block is not None:
        raise Rejected(f"代码块未闭合：{heading}")
    return fields


def check_copy_pack(markdown, cfg):
    blocks = read_copy_blocks(markdown)
    for heading in COPY_HEADINGS.values():
        if not blocks.get(heading, "").strip():
            raise Rejected(f"缺少或为空：{heading}")
    title, body, raw_tags = (blocks[COPY_HEADINGS[key]] for key in CORE)
    for key in CORE:
        if any(ch in blocks[COPY_HEADINGS[key]] for ch in cfg["forbidden_chars"]):
            raise Rejected(f"{COPY_HEADINGS[key]}含禁用字符")
    if "\n" in title or not title.startswith(cfg["title_prefix"]) or len(title) > cfg["title_max_chars"]:
        raise Rejected("发布标题须单行，符合项目标题前缀和长度")
    lines = body.split("\n")
    if lines[1:] != cfg["body_fixed_lines"]:
        raise Rejected("正文的行数或固定行与发布设置不符")
    marker = " " + cfg["hook_emoji"]
    if not lines[0].endswith(marker) or len(lines[0].strip()) <= len(marker):
        raise Rejected("正文第一行须有剧情并以空格和项目表情结尾")
    if title.casefold() in body.casefold():
        raise Rejected("正文重复了标题")
    if "link in bio" in body.casefold():
        raise Rejected("正文不能写 link in bio")
    tags = raw_tags.split(", ")
    fixed = cfg["fixed_tags"]
    if "#" in raw_tags or tags[:len(fixed)] != fixed:
        raise Rejected("话题标签须以固定标签开头，原始字段不加 #")
    extra = tags[len(fixed):]
    if len(extra) > cfg["max_extra_tags"] or len(tags) != len(set(tags)) or any(not re.fullmatch("[a-z0-9]+", t) for t in extra):
        raise Rejected("本集标签须符合数量、小写字母数字、去重和逗号加空格规则")
    if "vocals" not in blocks["Exclude Styles"].lower():
        raise Rejected("Exclude Styles 须排除 vocals")
    if not re.search(r"\b\d{2,3}\s*BPM\b", blocks["Style of Music"], re.I):
        raise Rejected("Style of Music 须写 BPM")
    if "Instrumental" not in markdown:
        raise Rejected("须注明 Instrumental")
    return dict(title=title, tags=tags, instagram_caption="\n\n".join([title, body, " ".join("#" + t for t in tags)]))


def pack_markdown(fields, labels, cfg):
    strict = cfg.get("copy_format") == "instrumental_v1"
    headings = {**labels, **COPY_HEADINGS} if strict else labels
    lines = []
    for key, value in fields.items():
        if strict and (any(line.strip() == "```" for line in value.splitlines()) or "\n" in headings[key]):
            raise Rejected("发布字段包含会打断复制块的换行标题或围栏")
        lines.append("## " + headings[key] + "\n" + ("```text\n" + value + "\n```" if strict else value))
    if strict:
        lines.append("Instrumental：开；Lyrics：留空。")
    return "\n\n".join(lines)


def cue_time_ms(value):
    clock = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):([0-5]\d)(?:[.．](\d{1,3}))?", value)
    if clock:
        h, m, sec, fraction = clock.groups()
        if h and int(m) >= 60:
            return None
        return ((int(h or 0)*60 + int(m))*60 + int(sec))*1000 + int((fraction or "").ljust(3,"0"))
    seconds = re.fullmatch(r"(\d+)(?:[.．](\d{1,3}))?\s*(?:s|秒)?", value, re.I)
    if seconds:
        return int(seconds[1])*1000 + int((seconds[2] or "").ljust(3,"0"))
    return None


def check_cue_time(cue, segment):
    values = [cue_time_ms(v.strip()) for v in re.split(r"[–—~～至到-]", cue["at"].strip())]
    if len(values) not in (1,2) or None in values:
        raise Rejected(f"{segment['segment']} 的对位请填写全片时间点或起止时间，例如 {segment['at']}")
    start, end = [cue_time_ms(v) for v in segment["at"].split("–")]
    if values[0] < start or values[-1] > end or (len(values)==2 and values[0] >= values[1]):
        raise Rejected(f"{segment['segment']} 的对位须落在全片设计时窗 {segment['at']} 内")


def cue_rows(cues, music, segments, strict, frozen=True):
    if music not in ("backup", "enabled"):
        raise Rejected("音乐使用状态须为启用或备用")
    if not isinstance(cues, list) or any(not isinstance(c, dict) or
            any(not isinstance(c.get(k), str) or not c[k].strip() for k in ("at", "action")) for c in cues):
        raise Rejected("音乐对位须逐项填写全片设计时间和音乐处理")
    if not frozen:
        return cues  # Older editions retain their authored rows; their original time budget was not recorded.
    known = {s["segment"]:s for s in segments}
    covered = set()
    for c in cues:
        sid = c.get("segment")
        if sid and (not isinstance(sid, str) or sid not in known):
            raise Rejected(f"音乐对位的段号 {sid} 不在本版制作依据中")
        if sid:
            check_cue_time(c, known[sid])
            covered.add(sid)
    if music == "enabled":
        if strict and known.keys() - covered:
            raise Rejected("启用配乐时须补齐分段对位："+"、".join(sorted(known.keys()-covered)))
        return cues
    # Preserve the author's optional cue instructions separately from the active no-music timeline.
    rows = [dict(segment=s["segment"], at=s["at"], action="不放背景音乐") for s in segments]
    for c in cues:
        if c.get("action") != "不放背景音乐" or not any(c == r for r in rows):
            rows.append(dict(c, action="备用对位（未启用） · "+c["action"]))
    return rows
