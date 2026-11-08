"""Edit the ordered inputs and the tag references in the same prompt version."""
import re

from . import readable, stages
from .errors import Rejected


def edited_entry(entry, draft):
    if draft is None:
        return dict(entry)
    if (not isinstance(draft, dict) or not isinstance(draft.get("txt"), str)
            or any(not isinstance(draft.get(k), list) or any(not isinstance(v, str) for v in draft[k])
                   for k in ("ambience", "dialogue"))):
        raise Rejected("本段编辑须包含提示词正文、环境声和对白列表")
    sound = dict(entry.get("sound") or {}, ambience=draft["ambience"], dialogue=draft["dialogue"])
    return dict(entry, txt=draft["txt"], sound=sound)


def renumber(refs, text):
    mapping = {}
    for ref, label in zip(refs, stages.expected_labels(refs)):
        mapping[ref["label"]] = label
        ref["label"] = label
    return stages.LABEL_RE.sub(lambda m: mapping.get(m[0], m[0]), text), mapping


def add_definition(entry, label, purpose):
    text = entry["txt"]
    definition = f"{label}：{purpose}"
    if re.search(r"(?m)^[ \t]*" + re.escape(label) + r"[ \t]*[:：]", text):
        raise Rejected(f"正文已有 {label} 的定义，请先核对并移去重复定义")
    newline = "\r\n" if "\r\n" in text else "\n"
    if entry.get("prompt_format") != "readable_v3":
        return text + ("" if text.endswith("\n") else newline) + definition
    first, next_heading = readable.FLEX[:2]
    if text.count(first) != 1 or text.count(next_heading) != 1 or text.index(first) > text.index(next_heading):
        raise Rejected("请先补齐提示词的【参考素材】和【基本设定】栏")
    offset = text.index(next_heading)
    prefix = text[:offset]
    return prefix + ("" if prefix.endswith("\n") else newline) + definition + newline + text[offset:]


def without_reference(entry, ref, refs):
    label = ref["label"]
    # Only the generated definition may be dropped automatically. Keep every other user sentence.
    definition = re.compile(r"(?m)^" + re.escape(f"{label}：{ref.get('role', '')}") + r"\r?(?:\n|$)")
    text = entry["txt"]
    if entry.get("prompt_format") == "readable_v3":
        body = readable.regions(text, "flex").get(readable.FLEX[0], "")
        if body:
            start = text.index(readable.FLEX[0]) + len(readable.FLEX[0])
            text = text[:start] + definition.sub("", body, count=1) + text[start+len(body):]
    else:
        text = definition.sub("", text, count=1)
    if label in stages.LABEL_RE.findall(text):
        raise Rejected(f"正文仍在使用 {label}，请先改写引用该标签的文字，再移出本段")
    return renumber(refs, text)[0]
