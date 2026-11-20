"""Apply director notes to adopted material and mix one local final movie."""
import json
import math
import os
import subprocess
import time
from pathlib import Path

from . import production_steps
from .cloudtask import PollInterrupted
from .db import connect
from .storage import canonical, file_sha256, sha256_bytes, write_atomic

ACTIONS = ("trim_head", "trim_tail", "set_start", "set_end", "speed", "bgm_db", "source_db", "restore_dialogue", "replan")
OP = {"type":"object","properties":{
    "action":{"type":"string","enum":list(ACTIONS)}, "segment":{"type":"string"},
    "value":{"type":"number"}}, "required":["action","segment","value"],"additionalProperties":False}
SCHEMA = {"type":"object","properties":{"reply":{"type":"string"},
          "edits":{"type":"array","items":OP}}, "required":["reply","edits"],"additionalProperties":False}
GUIDANCE = ("精剪咨询、询问进度、讨论可能性时 edits 返回空数组。导演要求改片时把本条消息转为 edits，"
            "不要重放历史意见。trim_head/trim_tail 是从当前段首/尾再缩短的秒数；set_start/set_end 是原片的绝对秒数；"
            "speed 是播放倍速(0.5到2)，source_db 是该段原声增益，bgm_db 是整集配乐增益，单位为分贝。"
            "bgm_db 和 replan 的 segment 为空。其余操作填真实段号。结合已采用剪点和此前意见理解节奏要求。"
            "导演指出成片缺陷就是修改意见。对白被截断时用 restore_dialogue，段号取自本集，value=0；保护整段连续原声，无需猜台词秒数。支持专家重排时，导演要求保留对白并切换已有镜头，或反馈涉及剧情、画面穿帮、物件状态、局部构图和镜头连续性，都用 replan，由成片画面复核导演原话；其他段内剪点问题也用 replan，segment 为空、value=0，系统会将原反馈和采用剧本交给精剪师。仅执行当前能力支持的改动，超出能力要明确说明。回复描述拟调整内容，执行状态由工作台显示。")


def operations(raw, segments):
    from jsonschema import validate
    validate(raw, SCHEMA["properties"]["edits"])
    for op in raw:
        action, value = op["action"], op["value"]
        if (op["segment"] != "" if action in ("bgm_db","replan") else op["segment"] not in segments):
            raise ValueError("修改意见没有对应本集素材")
        lo, hi = (0.5, 2) if action == "speed" else (-96, 12) if action.endswith("_db") else (0, math.inf)
        if action in ("replan","restore_dialogue") and value != 0:
            raise ValueError("重剪指令无需指定秒数")
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError("剪辑参数超出可用范围")
    return raw


def notes(db, delivery):
    return [dict(text=r[0], edits=json.loads(r[1])) for r in db.execute(
        "SELECT f.text,e.edits FROM finecut_edit e JOIN free_turn f ON f.id=e.turn "
        "WHERE e.delivery=? ORDER BY e.rowid", (delivery,))]


def undo_last_edit(wb, queue, delivery):
    from .db import transaction
    with transaction(wb.db_path) as db:
        last = db.execute("SELECT rowid FROM finecut_edit WHERE delivery=? ORDER BY rowid DESC LIMIT 1", (delivery,)).fetchone()
        if not last:
            raise ValueError("当前没有可撤销的精剪修改记录")
        db.execute("DELETE FROM finecut_edit WHERE rowid=?", (last[0],))
    info = queue.view(delivery)["music"]
    if info and info.get("handoff"):
        folder = (queue.data / info["handoff"]).parent
        receipt = status(folder)
        if receipt.get("state") == "修改受阻":
            write_atomic(folder / "render.json", canonical({k: v for k, v in receipt.items() if k not in ("state", "error", "blocked", "action")}))
    queue.wake.set()
    return queue.view(delivery)


def script_material(root):
    def read(name):
        path = root / "交接来源" / name
        return json.loads(path.read_bytes()) if path.is_file() else {}
    script, board = read("剧本.json"), read("制作方案.json")
    def spoken(value):
        return bool(value and str(value).strip().strip("。.!！").casefold() not in
                    ("", "无", "无对白", "无台词", "无对话", "无台詞", "none", "n/a", "no dialogue"))
    segments = script.get("shootingSegments", [])
    dialogue = {s["id"] for s in segments if spoken(s.get("dialogue"))}
    scenes = {s["id"] for s in script.get("scenes", []) if spoken(s.get("dialogue"))}
    for shot in board.get("shots", []):
        origin = shot.get("origin") or {}
        scene = origin.get("scene") if isinstance(origin, dict) else origin
        if shot.get("dialogueCues") or (not segments and scene in scenes):
            dialogue.add(shot["segment"])
    return script, dialogue


def check_dialogue_changes(changes, protected):
    if not changes:
        return
    for op in changes[-1]["edits"]:
        if op["segment"] in protected and op["action"] in {"set_start", "set_end", "trim_head", "trim_tail", "speed"}:
            raise ValueError(op["segment"] + " 含剧本对白，当前没有实测的对白起止时间，无法安全执行指定剪点或变速")


def timeline(inputs, changes):
    clips = {c["segment"]:dict(segment=c["segment"], video=c["video"], start=c["review"]["usableStart"],
              end=c["review"]["usableEnd"], speed=1, source_db=0) for c in inputs["clips"]}
    gain = -16
    for change in changes:
        for op in operations(change["edits"], clips):
            kind, value = op["action"], op["value"]
            if kind == "bgm_db":
                gain = value
                continue
            if kind == "replan":
                continue
            c = clips[op["segment"]]
            if kind == "trim_head": c["start"] += value
            elif kind == "trim_tail": c["end"] -= value
            elif kind == "set_start": c["start"] = value
            elif kind == "set_end": c["end"] = value
            elif kind == "restore_dialogue": c.update(restore_dialogue=True, source_db=0)
            else: c[kind] = value
    return list(clips.values()), gain


def probe(path):
    return json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],timeout=30))


def render(data, inputs, changes, output, dialogue=()):
    clips, gain = timeline(inputs, changes)
    check_dialogue_changes(changes, set(dialogue) | {c["segment"] for c in clips if c.get("restore_dialogue")})
    args, filters, labels = [], [], []
    duration = 0
    for i,c in enumerate(clips):
        path = data/c["video"]
        info = probe(path)
        vid = next(s for s in info["streams"] if s["codec_type"]=="video")
        if i == 0:
            width, height = (int(vid[k])//2*2 for k in ("width","height"))
        if c["segment"] in dialogue or c.get("restore_dialogue"):
            if not any(s["codec_type"] == "audio" for s in info["streams"]):
                raise ValueError(c["segment"] + " 的采用原片缺少剧本对白所需的音轨")
            c.update(start=0, end=float(vid.get("duration") or info["format"]["duration"]), speed=1)
        start, end, speed = c["start"], c["end"], c["speed"]
        length = (end-start)/speed
        if length < .08 or end > float(vid.get("duration") or info["format"]["duration"])+.05:
            raise ValueError(c["segment"]+" 的剪点超出片长或可用区间太短")
        args.extend(["-ss",str(start),"-t",str(end-start),"-i",str(path)])
        filters.append(f"[{i}:v]setpts=(PTS-STARTPTS)/{speed},fps=30,scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]")
        source = f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS,atempo={speed},volume={c['source_db']}dB,apad" if any(
            s["codec_type"]=="audio" for s in info["streams"]) else "anullsrc=r=48000:cl=stereo"
        filters.append(f"{source},aformat=channel_layouts=stereo,atrim=duration={length}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
        duration += length
    n=len(clips)
    args.extend(["-stream_loop","-1","-i",str(data/inputs["bgm"])])
    filters.extend(["".join(labels)+f"concat=n={n}:v=1:a=1[v][original]",
                    f"[{n}:a]volume={gain}dB,atrim=duration={duration}[music]",
                    "[original][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97:latency=1[a]"])
    production_steps.step("工作台正在编码成片")
    result = subprocess.run(["ffmpeg","-nostdin","-loglevel","error","-y",*args,"-filter_complex",";".join(filters),
                 "-map","[v]","-map","[a]","-c:v","libx264","-preset","veryfast","-crf","18","-c:a","aac",
                 "-b:a","192k","-movflags","+faststart",str(output)],capture_output=True,timeout=1800)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace")[-450:])
    actual=probe(output)
    if {s["codec_type"] for s in actual["streams"]} != {"audio","video"} or abs(float(actual["format"]["duration"])-duration)>max(.25,n/30):
        raise ValueError("成片音视频轨或时长核验失败")


def status(folder):
    path=folder/"render.json"
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        saved=json.load(f)
        recorded_at=os.fstat(f.fileno()).st_mtime
    if saved.get("startedAt") is None:
        if saved.get("state")=="渲染中":
            saved["startedAt"]=recorded_at
        elif saved.get("state")=="已出片":
            request=folder/"精剪过程/request.json"
            if request.is_file() and request.stat().st_mtime<=recorded_at:
                saved.update(startedAt=request.stat().st_mtime, endedAt=recorded_at)
    return saved


def revision_for(source, changes):
    return sha256_bytes(canonical([file_sha256(source), changes]))


def progress(data, db, delivery):
    from contextlib import closing
    import sqlite3
    queue = data/"video-production/queue.sqlite"
    if not queue.is_file():
        return False, False
    with closing(sqlite3.connect(queue.as_uri()+"?mode=ro", uri=True)) as source_db:
        if not source_db.execute("SELECT 1 FROM sqlite_master WHERE name='soundtrack'").fetchone():
            return False, False
        row = source_db.execute("SELECT handoff FROM soundtrack WHERE delivery=?", (delivery,)).fetchone()
    if not row or not row[0] or not (data/row[0]).is_file():
        return False, False
    source = data/row[0]
    receipt = status(source.parent)
    done = receipt.get("state") == "已出片" and (source.parent/"final.mp4").is_file() and (
        receipt.get("published") == revision_for(source, notes(db, delivery)))
    completed_at = (receipt.get("endedAt") or (source.parent/"render.json").stat().st_mtime) if done else None
    return True, completed_at


def advance(wb, queue):
    from .videoreview import verify_movie, media_verdict, MediaCallError
    from .bundledfinecut import FeedbackMismatch
    with queue.db() as db:
        ready = db.execute("SELECT delivery,handoff FROM soundtrack s WHERE handoff IS NOT NULL AND NOT EXISTS (SELECT 1 FROM episode_control c WHERE c.delivery=s.delivery AND c.paused=1)").fetchall()
    for delivery, handoff in ready:
        source=queue.data/handoff
        folder=source.parent
        with connect(wb.db_path) as db:
            changes=notes(db, delivery)
            project=db.execute("SELECT p.config_id,d.relpath FROM delivery d JOIN episode e ON e.id=d.episode_id "
                               "JOIN project p ON p.id=e.project_id WHERE d.id=?", (delivery,)).fetchone()
        revision=revision_for(source,changes)
        previous=status(folder)
        if previous.get("revision")==revision and (previous["state"]=="修改受阻" or previous.get("retryAt",0)>time.time() or
                previous["state"]=="已出片" and (folder/"final.mp4").is_file()):
            continue
        started = previous.get("startedAt") if previous.get("revision")==revision and previous.get("state")=="渲染中" else None
        started = started or time.time()
        def record(state, **extra):
            write_atomic(folder/"render.json", canonical(dict(revision=revision,state=state,startedAt=started,
                endedAt=None if state=="渲染中" else time.time(),**extra)))
        last=previous.get("published")
        record("渲染中", published=last, action="工作台正在准备精剪素材")
        try:
            def report(label):
                queue.check_dispatch(delivery)
                record("渲染中", published=last, action=label)
            with production_steps.observe(report):
                inputs=json.loads(source.read_bytes())
                finding = None
                if project and project[0] == "puggy":
                    from . import bundledfinecut
                    finding = bundledfinecut.render(queue.data,delivery,inputs,changes,folder/"rendering.mp4")
                else:
                    script, dialogue = script_material(queue.data/project[1]) if project else ({}, set())
                    context=dict(script=script, clips=inputs["clips"], directorChanges=changes, dialogue=sorted(dialogue))
                    ids=[c["segment"] for c in inputs["clips"]]
                    for attempt in range(2):
                        answer=media_verdict(queue.data,delivery,"create","finecut_plan",folder/"剪辑表",
                            "为本集拟剪辑表。以逐段采用区间为基础，只给必要的段内剪点和速度，沿用原剪点时 edits 为空。"
                            "导演已有操作在你的剪辑表之后另行执行，不要重复。对白段完整保留。"+json.dumps(context,ensure_ascii=False),SCHEMA)["result"]
                        try:
                            edits=operations(answer["edits"],ids)
                            check_dialogue_changes([dict(edits=edits)],dialogue)
                            candidate = folder/"rendering.mp4"
                            stamp = sha256_bytes(canonical([revision, edits, sorted(dialogue)]))
                            receipt = folder/"encoded.json"
                            encoded = json.loads(receipt.read_bytes()) if receipt.is_file() else {}
                            if not (candidate.is_file() and encoded.get("plan")==stamp and
                                    encoded.get("sha256")==file_sha256(candidate)):
                                render(queue.data,inputs,[dict(text=answer["reply"],edits=edits),*changes],candidate,dialogue)
                                write_atomic(receipt, canonical(dict(plan=stamp, sha256=file_sha256(candidate))))
                            finding = verify_movie(queue.data, delivery, folder/"rendering.mp4", folder/"成片复核",
                                                   dict(context,editingPlan=answer))
                            break
                        except (FeedbackMismatch, ValueError) as exc:
                            if attempt:
                                raise
                            context["上版具体修改意见"]=str(exc)
                            context["上版剪辑表"]=answer
                (folder/"rendering.mp4").replace(folder/"final.mp4")
                record("已出片",published=revision, **({"finalReview":finding} if isinstance(finding,dict) else {"feedbackReview":finding} if finding else {}))
        except PollInterrupted:
            record("等待恢复精剪", published=last)
        except (MediaCallError, TimeoutError, ConnectionError, subprocess.TimeoutExpired) as exc:
            tries = previous.get("retries", 0) + 1 if previous.get("revision")==revision else 1
            record("等待自动重试", error=str(exc)[:500], retries=tries, retryAt=time.time()+min(300, 10*2**min(tries,5)), published=last)
        except (OSError,ValueError,subprocess.SubprocessError,StopIteration) as exc:
            record("修改受阻",error=str(exc)[:500],published=last)
