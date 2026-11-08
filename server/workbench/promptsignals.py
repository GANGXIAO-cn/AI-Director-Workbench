"""Locate passages for human review using the frozen readable_prompts.py rules."""
import json
import re

from . import readable
from .storage import sha256_bytes

SCOPE = "仅定位复核线索；次数、长度不判定语义质量，必要跨段约束可保留并说明依据"


def fixed_blocks(standards):
    cast = standards.get("characterStandard", {})
    rules = standards.get("promptStandard", {})
    blocks = [row.get("video_zh") for group in ("roles", "pair_rules") for row in cast.get(group, {}).values()]
    blocks.extend(row.get("zh") for row in rules.get("blocks", {}).values())
    return [block for block in blocks if block]


def size(text):
    return len(re.sub(r"\s", "", text))


def warnings(segments, blocks, asset_ids):
    result, across = [], {}
    patterns = [(aid, re.compile(r"(?<![A-Za-z0-9_-])"+re.escape(aid)+r"(?![A-Za-z0-9_-])")) for aid in asset_ids]
    for sid, strategy, original in segments:
        text = original
        for block in blocks:
            text = text.replace(block, "")
        parts = readable.regions(text,strategy)
        opening, action = ("【环境】","镜头描述：") if strategy == "grid" else ("【开场】","【镜头与动作】")
        total = size(text)
        opening_size, action_size = size(parts.get(opening,"")), size(parts.get(action,""))
        if total > 1200:
            result.append(dict(segment=sid,kind="length",characters=total,
                               note="去掉规定固定块后超过1200字，请结合栏目通读",scope=SCOPE))
        if opening_size > action_size:
            result.append(dict(segment=sid,kind="opening_balance",section=opening,opening_characters=opening_size,
                               action_characters=action_size,opening_share=round(opening_size/total,3),
                               note="局部环境/开场长于动作正文；核对是否均为本段必要条件",scope=SCOPE))
        sentences = {}
        for heading, body in parts.items():
            for sentence in re.split(r"[。！？\n]",body):
                sentence = sentence.strip()
                if len(sentence) >= 20:
                    sentences.setdefault(sentence,[]).append(heading)
        for sentence, occurrences in sentences.items():
            location = dict(segment=sid,sections=list(dict.fromkeys(occurrences)))
            across.setdefault(sentence,[]).append(location)
            if len(occurrences) > 1:
                result.append(dict(segment=sid,kind="within_prompt_repeat",sections=location["sections"],
                                   occurrences=len(occurrences),note=sentence,scope=SCOPE))
        for aid, pattern in patterns:
            if pattern.search(text):
                result.append(dict(segment=sid,kind="asset_id",note="疑似内部资产代号："+aid))
    for sentence, locations in across.items():
        if len(locations) >= 3:
            result.append(dict(segments=[loc["segment"] for loc in locations],kind="repeated_sentence",
                               locations=locations,note=sentence,scope=SCOPE))
    return result


def report(segments, standards, asset_ids):
    candidates, inputs, skipped = [], [], []
    blocks = fixed_blocks(standards)
    for seg in segments:
        sid, text = seg["id"], seg.get("txt")
        if seg.get("prompt_format") != "readable_v3":
            skipped.append(dict(segment=sid,reason="未声明 readable_v3"))
            continue
        if not isinstance(text,str) or not text.strip() or seg.get("strategy") not in ("flex","grid"):
            skipped.append(dict(segment=sid,reason="缺少可读 TXT 或参考策略"))
            continue
        strategy = seg["strategy"]
        candidates.append((sid,strategy,text.replace("\r\n","\n").replace("\r","\n")))
        record = dict(segment=sid,sha256=sha256_bytes(text.encode("utf-8")),strategy=strategy)
        if seg.get("duration_ms") is not None:
            try:
                timing = readable.check_timeline_text(text,strategy,seg["duration_ms"],sid,h3_rules=True,
                                                      flat=seg.get("flat",False))
                record["intervals"] = timing["timeline_intervals"]
            except ValueError as exc:
                record["timelineIssue"] = str(exc)
        inputs.append(record)
    return dict(inputs=inputs,skipped=skipped,fixedBlocks=len(blocks),warnings=warnings(candidates,blocks,asset_ids))


def archived(files,data):
    standards = {kind:json.loads(files.store.read_blob(item["sha256"]))
                 for kind,item in files.metadata["standards"].items() if kind in ("characterStandard","promptStandard")}
    segments = []
    for seg in data["segments"]:
        text = files.text(seg["prompt"]) if seg.get("prompt_format")=="readable_v3" else None
        duration = seg.get("duration_s")
        segments.append(dict(id=seg["id"],prompt_format=seg.get("prompt_format"),strategy=seg.get("reference_strategy"),
                             txt=text,duration_ms=round(duration*1000) if type(duration) in (int,float) else None,
                             flat=(data.get("shot_rhythm") or {}).get("version")==2))
    return report(segments,standards,[a["id"] for a in data.get("assets",[]) if not a.get("fixed_role")])
