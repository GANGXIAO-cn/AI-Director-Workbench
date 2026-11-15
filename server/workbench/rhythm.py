"""Shot-rhythm v2 for structured plans and segment-local prompt text.

Table checks and review warnings follow milo-leo-short-video at
ab0fa75e3b2254a9341a8501a48be7577ab023eb, scripts/shot_rhythm.py.
The workbench reads the adopted plan and versioned prompt, without filesystem lookups.
"""
from dataclasses import dataclass
from decimal import Decimal
from itertools import groupby
import hashlib
import json
import re

from . import readable, timeline

SPAN = re.compile(r"(\d+(?:\.\d{1,3})?)\s*[–—-]\s*(\d+(?:\.\d{1,3})?)")
COLUMNS = ("镜头S", "段ID", "段内区间", "观看任务", "保留理由", "TXT定位短句")
LONG_MS = 5000


def stamp(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def table(text, columns, required=False):
    rows = []
    header = None
    seen = False
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set(columns).issubset(cells):
            if seen or len(set(cells)) != len(cells):
                raise ValueError("重复的表头或列：" + columns[0])
            header, seen = cells, True
        elif header and not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            if len(cells) != len(header):
                raise ValueError("列数与表头不同：" + line)
            rows.append(dict(zip(header, cells)))
    if required and not rows:
        raise ValueError("缺少镜头充分性联合表或数据行")
    return rows


def warning(kind, key, scope, detail):
    return {"id": f"{kind}/{key}", "version": stamp([kind, scope]), "detail": detail}


FLAT_COLUMNS = (*COLUMNS, "接续方式", "构图/机位/运镜及完成时间")
CUT_CUE = re.compile(r"(?:硬切(?:到|至)?|切镜|切到|切至)")


def validate_passage(row, section, previous_id, used_ids, all_sections, tag):
    ident = row["镜头S"]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", ident):
        raise ValueError(f"{tag} 镜号不合法")
    if row["接续方式"] != section.marker:
        raise ValueError(f"{tag} 表文接续标记不一致")
    if section.marker == "同镜延续":
        if ident != previous_id:
            raise ValueError(f"{tag} 同镜延续须沿用上一镜号")
    elif ident in used_ids:
        raise ValueError(f"{tag} 起镜或切镜须使用新镜号")
    span = SPAN.fullmatch(row["段内区间"])
    if not span or [Decimal(v)*1000 for v in span.groups()] != [section.start, section.end]:
        raise ValueError(f"{tag} 联合表与正文时间不同")
    # 镜号、时间区间及接续标记已定位正文；短句只是阅读辅助，
    # 措辞或重复出现不应触发整份提示词返修。
    for field in ("观看任务", "构图/机位/运镜及完成时间"):
        if row[field] in {"", "-", "—"}:
            raise ValueError(f"{tag} 缺少{field}")


@dataclass(frozen=True)
class Passage:
    start: int
    end: int
    marker: str
    body: str

    @property
    def action(self):
        return self.body[len(self.marker)+1:]


def analyse(rows, windows):
    # 正文时间线已由 segment_windows 校验。辅助联合表的表达差异
    # 仅影响节奏统计，不再阻塞提示词采用或导出。
    try:
        return _analyse(rows, windows)
    except ValueError as exc:
        return dict(mode='flat-v2', segment_paragraph_counts={sid:len(parts) for sid,parts in windows.items()},
                    segment_shot_counts={}, segment_cut_counts={}, shot_seconds={}, warnings=[],
                    scope='辅助联合表与正文存在差异，未作逐镜节奏统计：'+str(exc),
                    unparsed_segments=list(windows))


def _analyse(rows, windows):
    warnings, scopes, metrics, counts, paragraph_counts, cuts = [], {}, {}, {}, {}, {}
    offset = 0
    for sid, sections in windows.items():
        group = rows[offset:offset+len(sections)]
        if len(group) != len(sections) or any(row["段ID"] != sid for row in group):
            raise ValueError(f"{sid} 联合表与TXT时间段缺行、多行或错序")
        offset += len(group)
        used_ids, previous_id = set(), None
        for row, section in zip(group, sections):
            ident = row["镜头S"]
            tag = f'{sid}/{ident}/{section.start/1000:g}–{section.end/1000:g}'
            validate_passage(row, section, previous_id, used_ids, sections, tag)
            scope = [row, vars(section)]
            tasks = re.split(r"<br\s*/?>", row["观看任务"], flags=re.I)
            tasks = [task.strip() for task in tasks]
            if any(task in {"", "-", "—"} for task in tasks) or len(set(tasks)) != len(tasks):
                raise ValueError(f"{tag} 观看任务为空或重复")
            if len(tasks) > 1:
                warnings.append(warning("多任务", tag, scope,
                    f'{len(tasks)}项观看任务在同一时间段：{row["观看任务"]}。复核是否要重分时间段'))
            if section.marker == "同镜延续":
                for cue in CUT_CUE.finditer(section.body):
                    if re.search(r"(?:不要|不能|不得|不应|不许|不|避免|禁止|勿)\s*$", section.body[:cue.start()]):
                        continue
                    warnings.append(warning("切镜疑点", tag, scope, section.body))
                    break
            previous_id = ident
            used_ids.add(ident)
        scopes[sid] = [group, [vars(w) for w in sections]]
        durations = []
        for ident, items in groupby(zip(group, sections), key=lambda pair: pair[0]["镜头S"]):
            items = list(items)
            duration = (items[-1][1].end - items[0][1].start)/1000
            durations.append(duration)
            if duration*1000 > LONG_MS and not any(row["保留理由"] not in {"", "-", "—"} for row, _ in items):
                warnings.append(warning("长镜", f"{sid}/{ident}", [[r, vars(w)] for r, w in items],
                                        "缺持续观看理由；复核各时间段的相机承接"))
        paragraph_counts[sid] = len(sections)
        metrics[sid] = durations
        counts[sid] = len(durations)
        cuts[sid] = counts[sid] - 1
        if counts[sid] <= 2:
            warnings.append(warning("少镜段", sid, scopes[sid], "核对各段相机承接与观看目的"))
    if offset != len(rows):
        raise ValueError(f'联合表包含TXT未覆盖的时间段：{rows[offset]["段ID"]}/{rows[offset]["段内区间"]}')
    if len(counts) > 1 and len(set(counts.values())) == 1:
        warnings.append(warning("全片同镜数", "全片", scopes, "复核各段实际观看需要"))
    return {"mode": "flat-v2", "segment_paragraph_counts": paragraph_counts,
            "segment_shot_counts": counts, "segment_cut_counts": cuts, "shot_seconds": metrics,
            "warnings": warnings, "scope": "节奏提示供内容审核参考，不要求逐条回应；结构错误仍须修正", "unparsed_segments": []}


def plan_rows(plan, selected=None):
    layout = timeline.layout(plan)
    shots = {s["id"]: s for s in plan["shots"]}
    rows = []
    scoped = any("visibleRoles" in s or "propStates" in s for s in plan["shots"])
    for segment in layout["segments"]:
        if selected is not None and segment["id"] not in selected:
            continue
        shot_number = 0
        previous = None
        for key in segment["shots"]:
            shot = shots[key]
            same = shot.get("continuation") == "same" and previous is not None
            marker = "同镜延续" if same else "起镜" if not previous else "切镜"
            if not same:
                shot_number += 1
            # The workbench's window ID is unique; consecutive windows may share a camera shot.
            default_id = key if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key) else f"S{shot_number:02}"
            ident = shot.get("rhythmShot") or (previous if same else default_id)
            timing = layout["shots"][key]
            action = shot.get("action") or ""
            if not isinstance(action, str):
                raise ValueError(f"{key} 动作须为文本")
            from .readable import strip_matching_window_prefix
            action = strip_matching_window_prefix(action,timing["seg_start_ms"],timing["seg_end_ms"])
            anchor = strip_matching_window_prefix(shot.get("txtAnchor") or action.rstrip("。，"),timing["seg_start_ms"],timing["seg_end_ms"])
            cells = (ident, segment["id"], f"{timing['seg_start_ms']/1000:g}–{timing['seg_end_ms']/1000:g}",
                     shot.get("attention") or action, shot.get("holdReason") or "",
                     anchor, marker, shot.get("camera") or "")
            if not all(isinstance(v,str) for v in cells):
                raise ValueError(f"{key} 的镜号、观看任务、机位和定位短句须为文本")
            row = dict(zip(FLAT_COLUMNS,cells))
            if scoped:
                roles, props = shot.get("visibleRoles"), shot.get("propStates")
                if (not isinstance(roles,list) or any(not isinstance(r,str) or not r.strip() or re.search(r"[、,，\s|]", r)
                        or r in ("无","待填") for r in roles) or len(set(roles))!=len(roles)):
                    raise ValueError(key+" 须填写不重复的实际可见角色 ID，空镜使用空数组")
                if not isinstance(props,dict) or any(not re.fullmatch(r"[a-z][a-z0-9_-]*",k) or
                        not isinstance(v,str) or not v.strip() or re.search(r"[;；|\n\r]",v) for k,v in props.items()):
                    raise ValueError(key+" 须填写关键道具 ID 与状态，没有道具使用空对象")
                row["实际可见角色ID"] = "、".join(roles) or "无"
                row["关键道具状态"] = "；".join(k+"="+v.strip() for k,v in props.items()) or "无"
            rows.append(row)
            previous = ident
    return rows


def markdown(rows):
    def line(values):
        if any("|" in v or "\n" in v or "\r" in v for v in values):
            raise ValueError("联合表单元格不能包含竖线或换行，多项观看任务请使用 <br>")
        return "|"+"|".join(values)+"|"
    columns=tuple(rows[0]) if rows else FLAT_COLUMNS
    return "\n".join([line(columns),line(["---"]*len(columns)),
                      *(line([r[c] for c in columns]) for r in rows)])+"\n"


def plan_table(plan, selected=None):
    custom = plan.get("rhythmTable", "")
    if not isinstance(custom, str):
        raise ValueError("镜头充分性联合表须为 Markdown 文本")
    if custom.strip():
        rows = table(custom, FLAT_COLUMNS, required=True)
        from .readable import strip_matching_window_prefix
        for row in rows:
            span=SPAN.fullmatch(row['段内区间'])
            if span:
                start,end=[int(Decimal(v)*1000) for v in span.groups()]
                row['TXT定位短句']=strip_matching_window_prefix(row['TXT定位短句'],start,end)
        if rows and "实际可见角色ID" not in rows[0] and "关键道具状态" not in rows[0] and any(
                "visibleRoles" in s or "propStates" in s for s in plan["shots"]):
            expected = plan_rows(plan)
            check_alignment(expected, rows)
            for row, shot in zip(rows,expected):
                row.update({k:shot[k] for k in ("实际可见角色ID","关键道具状态")})
        return rows
    return plan_rows(plan, selected)


def check_alignment(expected, rows):
    if len(expected) != len(rows):
        raise ValueError("联合表行数与采用方案的动作窗数不一致")
    for planned, row in zip(expected, rows):
        span = SPAN.fullmatch(row["段内区间"])
        planned_span = SPAN.fullmatch(planned["段内区间"])
        if (row["段ID"] != planned["段ID"] or row["接续方式"] != planned["接续方式"]
                or not span or tuple(map(Decimal, span.groups())) != tuple(map(Decimal, planned_span.groups()))):
            raise ValueError(f'{planned["段ID"]}/{planned["段内区间"]} 联合表与采用方案的动作窗或接续方式不一致')


def segment_windows(text, strategy, duration_ms, sid="本段"):
    local = readable.flat_windows(readable.motion_section(text,strategy,sid), duration_ms, sid)
    return [Passage(w["start_ms"], w["end_ms"], w["marker"], w["marker"] + "，" + w["body"]) for w in local]


def packaged_report(manifest, read_text):
    config = manifest.get("shot_rhythm")
    version = config.get("version") if isinstance(config, dict) else None
    if type(version) is not int or version != 2:
        raise ValueError("须声明 shot_rhythm.version=2")
    rows = table(read_text(config.get("plan", "场面与制作方案.md")), FLAT_COLUMNS, required=True)
    windows = {s["id"]: segment_windows(read_text(s["prompt"]), s["reference_strategy"],
                                        Decimal(str(s["duration_s"])) * 1000, s["id"]) for s in manifest["segments"]}
    return analyse(rows, windows)


def check(plan, entries, selected=None):
    segments = timeline.layout(plan)["segments"]
    selected = set(selected) if selected is not None else {s["id"] for s in segments}
    windows = {}
    legacy = []
    for s in segments:
        sid = s["id"]
        if sid not in selected:
            continue
        entry = entries.get(sid) or {}
        if entry.get("prompt_format") != "readable_v3":
            legacy.append(sid)
            continue
        windows[sid] = segment_windows(entry.get("txt", ""), s["strategy"], s["duration_ms"], sid)
    if not windows:
        return dict(rows=[], report=None, legacy=legacy)
    # Edited/imported tables use the same external format as historical production plans.
    full = plan_table(plan, windows)
    rows = [r for r in full if r["段ID"] in windows]
    if len(windows)==len(segments):
        rows = full  # include unexpected trailing rows so the validator can reject them
    report = analyse(rows, windows)
    if plan.get("rhythmTable"):
        expected = plan_rows(plan, windows)
        check_alignment(expected, rows)
    # Also verify exportability before a draft can be adopted.
    markdown(rows)
    return dict(rows=rows,report=report,legacy=legacy)
