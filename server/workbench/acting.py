"""Dialogue timing uses the versioned shot list and an explicit reading or speaking rate."""
import unicodedata
import re

from .timeline import layout
from . import readable


def integer(value, label, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**53-1:
        raise ValueError(f"{label}须为不小于 {minimum} 的安全整数")
    return value


def text_field(cue, key):
    value = cue.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("须填写说话者和台词原文")
    return value.strip()


def duration(cue, words):
    mode = cue.get("mode")
    if mode == "reading":
        return integer(cue.get("read_ms"), "试读毫秒数", 1), None
    if mode not in ("words", "characters"):
        raise ValueError("请选择试读、字/分钟或词/分钟")
    rate = integer(cue.get("rate"), "每分钟字数或词数", 1)
    if cue.get("count") is not None:
        count = integer(cue["count"], "发音字词数", 1)
    elif mode == "words":
        # Automatic word counts require an explicitly space-delimited script.
        if any(c.isalpha() and not unicodedata.name(c, "").startswith(("LATIN ", "GREEK ", "CYRILLIC ")) for c in words):
            raise ValueError("此文字按词估算请填写发音词数，或改用按字估算、试读")
        count = len([token for token in words.split() if any(c.isalnum() for c in token)])
    else:
        count = sum(c.isalnum() for c in words)
    if not count:
        raise ValueError("没有可计数的字词，请填写发音字词数或改用试读")
    return (count * 60000 + rate - 1) // rate, count


def report(plan, selected=None):
    time = layout(plan)
    rows, errors, warnings = [], [], []
    by_id = {s["id"]: s for s in plan.get("shots", [])}
    for segment in time["segments"]:
        if selected is not None and segment["id"] not in selected:
            continue
        spoken = {}
        for sid in segment["shots"]:
            shot = by_id[sid]
            cues = shot.get("dialogueCues", [])
            if not isinstance(cues, list):
                errors.append(f"{sid} 的台词须为逐句数组")
                continue
            for number, cue in enumerate(cues, 1):
                label = f"{sid} 第{number}句"
                try:
                    if not isinstance(cue, dict):
                        raise ValueError("台词行须为对象")
                    speaker = text_field(cue, "speaker")
                    words = text_field(cue, "text")
                    direction = cue.get("direction", "")
                    if not isinstance(direction, str):
                        raise ValueError("配音语气须为文本")
                    offset = integer(cue.get("offset_ms", 0), "起声偏移")
                    pause = integer(cue.get("pause_ms", 0), "句后反应停顿")
                    end_shot = cue.get("throughShot") or sid
                    if end_shot not in segment["shots"] or segment["shots"].index(end_shot) < segment["shots"].index(sid):
                        raise ValueError("结束镜须为本段的当前镜或后续镜")
                    first, last = time["shots"][sid], time["shots"][end_shot]
                    if offset >= shot["duration_ms"]:
                        raise ValueError("起声点须落在本镜内")
                    start = first["seg_start_ms"] + offset
                    available = last["seg_end_ms"] - start
                    reading, count = duration(cue, words)
                except ValueError as exc:
                    errors.append(f"{label} · {exc}")
                    continue
                margin = available - reading - pause
                continuing = [other for other in segment["shots"]
                              if start < time["shots"][other]["seg_start_ms"] < start + min(reading, available)]
                if margin < 0:
                    warnings.append(f"{label} · 至 {end_shot} 的可用时窗少 {-margin / 1000:g} 秒")
                rows.append(dict(segment=segment["id"], shot=sid, number=number, throughShot=end_shot,
                                 speaker=speaker, text=words, direction=direction, mode=cue["mode"],
                                 count=count, rate=cue.get("rate") if count is not None else None, continuingShots=continuing,
                                 start_ms=start, speech_ms=reading, pause_ms=pause, available_ms=available, margin_ms=margin))
                spoken.setdefault(speaker.casefold(), []).append((start, start + reading, label))
        for intervals in spoken.values():
            previous_end = -1
            for start, end, label in sorted(intervals):
                if start < previous_end:
                    warnings.append(f"{label} · 与同一说话者的另一句发声重叠")
                previous_end = max(previous_end, end)
    return dict(rows=rows, errors=errors, warnings=warnings)


def prompt_errors(plan, entries):
    if not any(shot.get("dialogueCues") for shot in plan.get("shots", [])):
        return []
    time = layout(plan)
    timed = report(plan, set(entries))
    errors = list(timed["errors"])
    for segment in time["segments"]:
        sid = segment["id"]
        cues = [row for row in timed["rows"] if row["segment"] == sid]
        entry = entries.get(sid)
        if not cues or not isinstance(entry, dict) or not isinstance(entry.get("txt"), str):
            continue
        sound = entry.get("sound")
        if isinstance(sound, dict) and sound.get("silent"):
            errors.append(f"{sid} 采用方案含台词，提示词却设为全段静音")
        windows = None
        if entry.get("prompt_format") == "readable_v3":
            try:
                parts = readable.sections(entry["txt"], segment["strategy"])
                action = parts["镜头描述：" if segment["strategy"] == "grid" else "【镜头与动作】"]
                windows = readable.flat_windows(action, segment["duration_ms"], sid)
            except ValueError as exc:
                errors.append(str(exc))
                continue
        for cue in cues:
            body = entry["txt"] if windows is None else next(
                (w["body"] for w in windows if w["start_ms"] <= cue["start_ms"] < w["end_ms"]), "")
            spoken = ' '.join(re.findall(r'<speaker_?\d+>(.*?)</speaker_?\d+>', body, re.S))
            body = re.sub(r'</?speaker_?\d+>', '', body)
            words = ' '.join(cue["text"].split())
            if words not in ' '.join(body.split()) and words not in ' '.join(spoken.split()):
                errors.append(f"{sid}/{cue['shot']} 第{cue['number']}句的起声处缺少采用台词「{cue['text']}」")
    return errors
