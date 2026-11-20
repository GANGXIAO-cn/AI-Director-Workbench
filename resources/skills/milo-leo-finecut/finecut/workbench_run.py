"""Adapt adopted video segments and the editing expert's plan to the original skill."""
import json
import math
import sys
from pathlib import Path

from analyze import analyze
from assemble import assemble, sha256
from beats import beat_grid
from common import FPS, probe, set_frame
from qa import qa
from render import Timeline, fit_outro, mix, mux, outro_clip, render_video, save_timeline


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(work, request):
    rough = work / "source.mp4"
    bgm = request["bgm"]
    info = probe(request["clips"][0]["file"])
    width, height = (1920, 1080) if info["width"] > info["height"] else (1080, 1920)
    set_frame(width, height)
    write(work/"outro-duration.json", probe(fit_outro(outro_clip()))["duration"])
    signature = [sha256(c["file"]) for c in request["clips"]]
    if signature != [c["sha256"] for c in request["clips"]] or sha256(bgm) != request["bgmSha256"]:
        raise ValueError("实际素材和本集采用的版本不同")
    stamp = [signature, request["bgmSha256"]]
    receipt = work / "prepared.json"
    if receipt.exists() and read(receipt) == stamp and rough.exists():
        report = read(work / "体检/analysis.json")
        if report["source"] == str(rough) and sha256(rough) == report["source_fingerprint"]["sha256"]:
            return
    assemble([c["file"] for c in request["clips"]], str(rough))
    analyze(str(rough), str(work / "体检"))
    write(work / "bgm_beats.json", beat_grid(bgm))
    write(receipt, stamp)


def cutaway_frames(clip, source_map, edits):
    # Segment boundaries in the assembled rough are whole frames.
    first, stop = round(clip["start"]*FPS), round(clip["end"]*FPS)
    windows = []
    for edit in edits:
        if edit["segment"] != clip["segment"]:
            continue
        at, until = round(edit["start"]*FPS), round(edit["end"]*FPS)
        lo, hi = max(first, at), min(stop, until)
        if lo >= hi:
            continue
        source = source_map[edit["sourceSegment"]]
        src = round(source["rough_t0"]*FPS) + round(edit["sourceStart"]*FPS) + lo-at
        if src < round(source["rough_t0"]*FPS) or src+hi-lo > round(source["rough_t1"]*FPS):
            raise ValueError("替换镜头超出已采用原片的帧范围")
        windows.append(dict(first=lo-first, stop=hi-first, sourceFrame=src))
    return windows


def finish(work, request, plan):
    rough = str(work / "source.mp4")
    segments = read(work / "segments.json")["segments"]
    grid = read(work / "bgm_beats.json")
    limits, edl, fixes, by_id = [], [], [], {}
    source_map = {c["segment"]:s for c,s in zip(request["clips"], segments, strict=True)}
    for clip in plan["clips"]:
        source = source_map[clip["segment"]]
        start, end, offset = clip["start"], clip["end"], source["rough_t0"]
        if start < 0 or end - start < .08 or end > source["rough_t1"] - offset + .05:
            raise ValueError(f"{clip['segment']} 的剪点超出采用片长")
        a, b = offset + start, min(offset + end, source["rough_t1"])
        fx = {"speed":clip["speed"]} if clip["speed"] != 1 else None
        windows = []
        for win in plan.get("reframes", []):
            if win["segment"] != clip["segment"] or win["end"] <= start or win["start"] >= end:
                continue
            first = round((offset+max(start,win["start"]))*FPS) - round(a*FPS)
            stop = round((offset+min(end,win["end"]))*FPS) - round(a*FPS)
            if stop > first:
                windows.append(dict(first=first, stop=stop, zoom=win["zoom"], x=win["x"], y=win["y"]))
        if windows:
            fx = dict(fx or {}, reframes=windows)
        cutaways = cutaway_frames(clip, source_map, plan.get("cutaways", []))
        if cutaways:
            fx = dict(fx or {}, cutaways=cutaways)
        edl.append((a, b, fx, clip["note"]))
        limits.append(dict(t0=a-.001, t1=b+.001))
        by_id[clip["segment"]] = offset
        if clip["source_db"]:
            fixes.append((a, b, clip["source_db"]))
    tl = Timeline(edl, limits)
    # A director's explicitly revised cut retains its exact in/out times.
    skipped = [i for i,c in enumerate(plan["clips"]) if c["segment"] in plan["timingEdits"] or (tl.clips[i]["fx"] or {}).get("cutaways")]
    duration = grid["duration"]
    def beats_at(t):
        base = math.floor(t / duration) * duration
        return [base + b for b in grid["beats"]]
    tl.snap_cuts(beats_at, skip=skipped)
    if tl.total <= 0:
        raise ValueError("当前剪辑表没有可输出的画面")
    minimum = plan.get("minFinalSeconds", 60)
    if tl.total + read(work/"outro-duration.json") < minimum:
        tl = Timeline(edl, limits)  # 保留专家原剪点，避免卡拍把合格成片剪短。
    for sid, seconds in plan.get("dialogueDurations", {}).items():
        matches = [cut for clip,cut in zip(plan["clips"], tl.clips) if clip["segment"] == sid]
        source = source_map[sid]
        if (len(matches) != 1 or (matches[0]["fx"] or {}).get("speed",1) != 1 or abs(matches[0]["src0"] - source["rough_t0"]) > .5/FPS or
                abs(matches[0]["src1"] - source["rough_t0"] - seconds) > .5/FPS):
            raise ValueError(sid + " 的实际剪点会截断剧本对白")
        if not probe(source["file"]).get("has_audio"):
            raise ValueError(sid + " 的采用原片缺少剧本对白所需的音轨")
    gaps = []
    for gap in plan.get("musicBreaks", []):
        offset = by_id[gap["segment"]]
        for clip, cut in zip(plan["clips"], tl.clips):
            if clip["segment"] != gap["segment"]:
                continue
            lo, hi = max(cut["src0"], offset + gap["start"]), min(cut["src1"], offset + gap["end"])
            if hi > lo:
                gaps.append((cut["out0"] + (lo-cut["src0"])/clip["speed"],
                             cut["out0"] + (hi-cut["src0"])/clip["speed"]))
    music = []
    at = 0.
    for pause, resume in sorted(gaps) + [(tl.total, tl.total)]:
        while at < pause - .0001:
            bg_start = at % duration
            if duration-bg_start < .0001:
                bg_start = 0.
            end = min(at + duration-bg_start, pause)
            music.append(dict(out0=at, out1=end, bgm0=bg_start, fade_out=min(.15, (end-at)/2)))
            at = end
        at = max(at, resume)
    events = []
    for event in plan.get("events", []):
        src = by_id[event["segment"]] + event["second"]
        try:
            out = tl.out(src)
        except ValueError:
            continue  # the director may have trimmed out an earlier event
        events.append(dict(name=event["name"], kind="sound", out=out))
    for a,b in gaps:
        events.extend([dict(name="配乐让位", kind="music_stop", out=a), dict(name="配乐重进",kind="music_start",out=b)])
    render_video(tl, rough, str(work), fixes)
    mix(tl, str(work), bgm=request["bgm"], music=music, bgm_gain_db=plan["gain"])
    outro = outro_clip(plan.get("product"))
    output_dir = work/"待核验"
    output_dir.mkdir(exist_ok=True)
    outputs = mux(str(work), str(output_dir), "本集", outro=outro)
    save_timeline(tl, str(work), rough, bgm=request["bgm"], music=music, events=events,
                  beats=grid if grid["beats"] else None, bgm_gain_db=plan["gain"], outro=outro,
                  extra=dict(name="本集", outputs=outputs))
    report = qa(str(work))
    actual = probe(outputs[0])
    if (not actual.get("has_audio") or abs(report["outro_frames"] - round(
            probe(read(work/"timeline.json")["outro"])["duration"] * FPS)) > 1):
        raise ValueError("成片的音轨或片尾帧数与剪辑表不同")
    if actual["duration"] < minimum:
        raise ValueError(f"含片尾的实际成片为 {actual['duration']:.3f} 秒，低于 {minimum:g} 秒。")
    lines = [plan["summary"], "", f"正片 {tl.total:.2f} 秒，含品牌片尾 {actual['duration']:.2f} 秒。", ""]
    for clip, c in zip(plan["clips"], tl.clips):
        lines.append(f"- {clip['segment']}　{c['out0']:.2f}–{c['out1']:.2f} 秒　{clip['note']}")
    (work/"剪辑说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    mode, directory = sys.argv[1:]
    work = Path(directory).resolve()
    request = read(work / "request.json")
    if mode == "prepare":
        prepare(work, request)
    elif mode == "finish":
        finish(work, request, read(work / "edit.json"))
    else:
        raise ValueError(mode)
