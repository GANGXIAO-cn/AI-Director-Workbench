"""Structured sections and checks for the readable_v3 segment-local prompt format."""
import re

FLEX = ("【参考素材】", "【基本设定】", "【开场】", "【镜头与动作】", "【声音与文字】")
GRID = ("【分镜参考】", "【主角】", "【环境】", "摘要与衔接：", "镜头描述：", "运镜边界：", "声音与音乐：", "画面文字：")
CLOCK = r"(\d{2}):([0-5]\d)(?:\.(\d{3}))?"
WINDOW = re.compile(CLOCK + "[-–—]" + CLOCK)
SECONDS = re.compile(r"第?\s*\d+(?:\.\d+)?\s*(?:秒\s*)?[–—~～至到-]\s*第?\s*\d+(?:\.\d+)?\s*秒")
STAGE = re.compile(r"阶段\s*[一二三四五六七八九十\d]+\s*[｜|：:]|^\s*阶段\s*[一二三四五六七八九十\d]+", re.M)


def regions(text, strategy):
    headings = GRID if strategy == "grid" else FLEX
    markers = sorted((text.index(title), title) for title in headings if title in text)
    return {title: text[start+len(title):markers[i+1][0] if i+1 < len(markers) else len(text)]
            for i, (start,title) in enumerate(markers)}


MUSIC_LINE = re.compile(r"^[ \t]*non_diegetic_music:[ \t]*([^\r\n]*)\r?$", re.M)
SILENT_LINE = re.compile(r"^[ \t]*overall_soundscape:\s*N/A[ \t]*\r?$", re.M)


def check_h3_sound(segment, text):
    sid = segment["id"]
    if segment.get("prompt_format") != "readable_v3":
        raise ValueError(f"{sid}：h3_rules 要求 readable_v3")
    music = segment.get("native_bgm", "none")
    silent = segment.get("full_silence", False)
    if music not in ("none", "generate"):
        raise ValueError(f"{sid}：native_bgm 须为 none 或 generate")
    if type(silent) is not bool:
        raise ValueError(f"{sid}：full_silence 须为布尔值")
    if silent and music != "none":
        raise ValueError(f"{sid}：全段静音与生成配乐冲突")

    count = text.count("non_diegetic_music:")
    if count != 1:
        problem = "漏写" if count == 0 else "重复"
        raise ValueError(f"{sid}：{problem} non_diegetic_music")
    match = MUSIC_LINE.search(text)
    if not match:
        raise ValueError(f"{sid}：non_diegetic_music 须独占一行")
    strategy = segment["reference_strategy"]
    heading = "声音与音乐：" if strategy == "grid" else "【声音与文字】"
    if not MUSIC_LINE.search(regions(text, strategy).get(heading, "")):
        raise ValueError(f"{sid}：non_diegetic_music 错栏，应在{heading}")
    value = match[1].strip()
    if not value:
        raise ValueError(f"{sid}：non_diegetic_music 取值须与字段同行，不能留空")
    if (value == "N/A") != (music == "none"):
        expected = "N/A" if music == "none" else "具体配乐描述"
        raise ValueError(f"{sid}：native_bgm={music} 要求 non_diegetic_music 填写{expected}")
    if not silent and SILENT_LINE.search(text):
        raise ValueError(f"{sid}：overall_soundscape 为 N/A 须声明 full_silence=true")


def check_audio_switch(sid, audio, silent, music):
    if type(audio) is not bool:
        raise ValueError(f"{sid}：生成声音开关须为布尔值")
    if silent and audio:
        raise ValueError(f"{sid}：全段静音与生成声音开关为开冲突")
    if music and not audio:
        raise ValueError(f"{sid}：生成配乐要求打开生成声音开关")


def sections(text, strategy):
    headings = GRID if strategy == "grid" else FLEX
    split = re.split("(" + "|".join(map(re.escape, headings)) + ")", text)
    if split[0].strip() or tuple(split[1::2]) != headings:
        raise ValueError("栏目缺失、重复或顺序有误：" + "、".join(headings))
    return {heading:body.strip() for heading,body in zip(split[1::2], split[2::2])}


def edit_sound_fields(text, strategy, music, retained_notes=None):
    """Preserve authored text on music edits. Full silence uses the reviewed replacement notes."""
    headings = GRID if strategy == "grid" else FLEX
    heading = "声音与音乐：" if strategy == "grid" else "【声音与文字】"
    sections(text, strategy)
    start = text.index(heading) + len(heading)
    end = text.index(headings[-1], start) if strategy == "grid" else len(text)
    body = text[start:end]
    newline = "\r\n" if "\r\n" in body else "\n"
    if retained_notes is not None:
        notes = retained_notes.strip()
        if "non_diegetic_music:" in notes or "overall_soundscape:" in notes:
            raise ValueError("保留说明中不能重复填写声音协议字段")
        replacement = newline + (notes + newline if notes else "") + "non_diegetic_music: N/A" + newline + "overall_soundscape: N/A"
        return text[:start] + replacement + body[len(body.rstrip("\r\n")):] + text[end:]
    # Some older drafts put N/A on the next line. Consume that value with its field.
    field = re.compile(r"(?m)^[ \t]*(non_diegetic_music|overall_soundscape):[ \t]*"
                       r"(?:\r?\n[ \t]*N/A[ \t]*(?=\r?$)|[^\r\n]*)")
    music_written = False
    def replace(match):
        nonlocal music_written
        if match[1] == "overall_soundscape":
            return "" if SILENT_LINE.fullmatch(match[0]) else match[0]
        if music_written:
            return ""
        music_written = True
        return "non_diegetic_music: " + music
    body = field.sub(replace, body)
    if not music_written:
        tail = body.rstrip("\r\n")
        body = tail + newline + "non_diegetic_music: " + music + body[len(tail):]
    return text[:start] + body + text[end:]


def render(parts):
    return "\n\n".join(title + "\n" + value for title,value in parts.items())


def _clock_ms(groups):
    minutes, seconds, millis = (int(value or 0) for value in groups)
    return (minutes*60+seconds)*1000+millis


def compact_window_headers(text):
    """Use whole seconds for integral windows, retaining exact legacy fractional timing."""
    def compact(match):
        if any(value not in (None, '000') for value in (match[3],match[6])):
            return match.group()
        return f"{match[1]}:{match[2]}-{match[4]}:{match[5]}"
    return re.sub("^" + WINDOW.pattern + r"(?=\r?$)", compact, text, flags=re.M)


def flat_windows(motion, duration_ms, segment_id="本段"):
    headers = []
    lines = motion.strip().splitlines()
    for i, line in enumerate(lines):
        header = WINDOW.fullmatch(line.strip())
        if header:
            headers.append((i, header))
        elif re.match(r'^\s*\d+:', line):
            raise ValueError(f'{segment_id} 时间码格式错误或没有独占一行：{line}')
    if not headers or headers[0][0] != 0:
        raise ValueError(f'{segment_id} 必须从独占一行的时间码开始')
    result = []
    previous = 0
    for i, (line, match) in enumerate(headers):
        start, end = _clock_ms(match.groups()[:3]), _clock_ms(match.groups()[3:])
        tag = f'{segment_id}/{match.group()}'
        if end <= start or start != previous:
            raise ValueError(f'{tag} 时间有缺口、重叠或非正时长')
        stop = headers[i+1][0] if i+1 < len(headers) else len(lines)
        body = '\n'.join(lines[line+1:stop]).strip()
        marker = re.match(r"(起镜|同镜延续|切镜)，",body)
        if not marker or not body[marker.end():].strip():
            raise ValueError(f'{tag} 正文首词须为起镜、同镜延续或切镜，紧跟中文逗号及动作正文')
        if (i == 0) != (marker[1] == '起镜'):
            raise ValueError(f'{tag} 首段必须起镜，后续只能同镜延续或切镜')
        ranges = [m.group().strip() for m in SECONDS.finditer(body)]
        if STAGE.search(body) or WINDOW.search(body) or len(ranges) >= 2:
            raise ValueError(f'{tag} 正文嵌套时间线：{body}')
        result.append(dict(start_ms=start,end_ms=end,marker=marker[1],body=body[marker.end():]))
        previous = end
    if previous != duration_ms:
        raise ValueError(f'{segment_id} 最后区间未覆盖生成时长 {duration_ms/1000:g} 秒')
    return result

def motion_section(text, strategy, sid):
    start, end = ("镜头描述：", "运镜边界：") if strategy == "grid" else ("【镜头与动作】", "【声音与文字】")
    _, found, tail = text.partition(start)
    motion, closed, _ = tail.partition(end)
    if not found or not closed:
        raise ValueError(f"{sid} 缺少时间轴栏目")
    return motion


def interval_count(motion, duration_ms):
    pattern = re.compile(r"^" + WINDOW.pattern + r"(?:[：:]|\s*$)", re.M)
    ranges = list(pattern.finditer(motion))
    if not ranges:
        raise ValueError("提示词缺少合法时间区间")
    previous = 0
    for match in ranges:
        start, end = _clock_ms(match.groups()[:3]), _clock_ms(match.groups()[3:])
        if start != previous or end <= start:
            raise ValueError("时间区间有缺口、重叠、倒序或零时长")
        previous = end
    if previous != duration_ms:
        raise ValueError("时间区间未连续覆盖本段实际时长")
    if sum(bool(re.match(r"^\d{2}:", line)) for line in motion.splitlines()) != len(ranges):
        raise ValueError("时间区间行格式不合法")
    return len(ranges)


def check_timeline_text(text, strategy, duration_ms, sid, h3_rules=False, flat=True):
    if strategy == "flex" and tuple(re.findall(r"【[^】\n]+】", text)) != FLEX:
        raise ValueError("flex须按顺序使用约定标题：" + "、".join(FLEX))
    motion = motion_section(text, strategy, sid)
    count = len(flat_windows(motion, duration_ms, sid)) if flat else interval_count(motion, duration_ms)
    for phrase in ("尚未试听","不能假定模型记得","无实际共享音频","没有实际共享音频","待后期"):
        if phrase in text:
            raise ValueError(f"TXT混入制作备注：{phrase}")
    numbering = re.sub(r'\(S[1-9]\d*(?:,S[1-9]\d*)*\)', '', motion) if h3_rules else motion
    if re.search(r'(?<![A-Za-z0-9_])S\d+[A-Z]?(?![A-Za-z0-9_])', numbering):
        raise ValueError("时间轴混入内部镜头编号")
    warnings = ["疑似制作理由/字段前缀，请通读处理"] if "作用：" in text or "剪点/省略：" in text else []
    return dict(timeline_intervals=count, warnings=warnings)


def check_flat_text(text, strategy, duration_ms, sid, h3_rules=False):
    return check_timeline_text(text, strategy, duration_ms, sid, h3_rules)


def validate(entry, strategy, duration_ms):
    text = entry["txt"]
    parts = sections(text, strategy)
    motion, sound = (("镜头描述：","声音与音乐：") if strategy=="grid" else ("【镜头与动作】","【声音与文字】"))
    flat_windows(parts[motion], duration_ms)
    forbidden = ("尚未试听","不能假定模型记得","无实际共享音频","没有实际共享音频","待后期")
    if any(note in text for note in forbidden):
        raise ValueError("提示词混入制作备注")
    actions = re.sub(r"\(S[1-9]\d*(?:,S[1-9]\d*)*\)", "", parts[motion])
    if re.search(r"(?<![A-Za-z0-9_])S\d+[A-Z]?(?![A-Za-z0-9_])", actions):
        raise ValueError("时间轴混入内部镜号")
    state = entry.get("sound", {})
    if not isinstance(state, dict) or any(type(state[k]) is not bool for k in ("silent","bgm") if k in state):
        raise ValueError("静音与配乐状态须为布尔值")
    check_h3_sound(dict(id=entry.get("id", "本段"), prompt_format=entry.get("prompt_format"),
                        reference_strategy=strategy, native_bgm="generate" if state.get("bgm") else "none",
                        full_silence=state.get("silent", False)), text)
    settings = entry.get("generation_settings")
    if isinstance(settings, dict) and "audio" in settings:
        check_audio_switch(entry.get("id", "本段"), settings["audio"], state.get("silent"), state.get("bgm"))
    if state.get("silent") and not re.search(r"^[ \t]*overall_soundscape:[ \t]*N/A[ \t]*\r?$", parts[sound], re.M):
        raise ValueError("全段静音须在声音栏填写 overall_soundscape: N/A")
    if state.get("silent") and re.search(r"<d>[^<]+</d>", text, re.I):
        raise ValueError("全段静音与正文中的发声台词冲突")
    return parts


def check_project_blocks(entry, strategy, blocks, cast_standard, on_screen, labels_by_hash):
    text = entry["txt"]
    parts = sections(text, strategy)
    grid = strategy == "grid"
    refs = parts["【分镜参考】" if grid else "【参考素材】"]
    settings = parts["【环境】" if grid else "【基本设定】"]
    audio = parts["声音与音乐：" if grid else "【声音与文字】"]
    placement = dict(reference_use=refs, visual_style=settings, motion=settings,
                     frame_text=parts["画面文字："] if grid else audio)
    if grid:
        placement["grid_reference"] = refs
    roles = {key:record for key,record in cast_standard.get("roles",{}).items()
             if key.casefold() in on_screen or str(record.get("name","")).casefold() in on_screen}
    if {"milo","leo"} & roles.keys():
        placement["cats_silent"] = audio
    for key, section in placement.items():
        if key in blocks:
            phrase = blocks[key].get("zh")
            if not phrase or section.count(phrase) != 1 or text.count(phrase) != 1:
                raise ValueError(f"项目固定块须在对应栏目逐字出现一次：{key}")
    definitions = parts["【主角】"] if grid else refs
    for key,role in roles.items():
        phrase = role.get("video_zh", "")
        if not phrase or definitions.count(phrase) != 1 or text.count(phrase) != 1:
            raise ValueError(f"{key} 的角色短标准缺失、重复或错栏")
        if not grid:
            label = labels_by_hash.get(role.get("sha256"))
            if not isinstance(label, str) or not label or label+"："+phrase not in definitions.splitlines():
                raise ValueError(f"{key} 角色短标准须绑定实际身份原图标签")
    if {"milo","leo"} <= roles.keys():
        pair = cast_standard.get("pair_rules",{}).get("milo+leo",{}).get("video_zh")
        if not pair or definitions.count(pair) != 1 or text.count(pair) != 1:
            raise ValueError("同框短标准须在角色定义区出现一次")


def strip_matching_window_prefix(text, start_ms, end_ms):
    """Only remove an exact leading duplicate; never rewrite semantic timing."""
    if not isinstance(text, str):return text
    match = re.match(r"^\s*(?:段内\s*)?" + CLOCK + r"[-–—]" + CLOCK + r"[。．，,:：\s]*", text)
    if match and (_clock_ms(match.groups()[:3]), _clock_ms(match.groups()[3:])) == (start_ms,end_ms):
        return text[match.end():]
    return text
