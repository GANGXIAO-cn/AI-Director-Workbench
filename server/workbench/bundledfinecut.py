"""PUGGY ROOM's packaged editing skill and its episode-specific editing plan."""
import json
from dataclasses import replace
from math import fsum
import os
import subprocess
import sys
from pathlib import Path

from jsonschema import ValidationError, validate

from .executors.base import ExecRequest
from .storage import canonical, sha256_bytes, write_atomic
from .production_steps import step
from .filmconversation import keep_previous_reply
from .videosegment import episode_expert

MIN_FINAL_SECONDS = 60
SKILL = Path(__file__).resolve().parents[2] / "resources/skills/milo-leo-finecut"


def obj(**fields):
    return dict(type="object", properties=fields, required=list(fields), additionalProperties=False)


STRING, NUMBER = {"type":"string"}, {"type":"number"}
CUT = obj(segment=STRING, start=NUMBER, end=NUMBER, speed=NUMBER, note=STRING)
EVENT = obj(segment=STRING, second=NUMBER, name=STRING)
PLAN = obj(summary=STRING, cuts=dict(type="array", items=CUT), events=dict(type="array", items=EVENT),
           musicBreaks=dict(type="array", items=obj(segment=STRING, start=NUMBER, end=NUMBER)))


REFRAME = obj(segment=STRING, start=NUMBER, end=NUMBER, zoom=NUMBER, x=NUMBER, y=NUMBER)
CUTAWAY = obj(segment=STRING, start=NUMBER, end=NUMBER, sourceSegment=STRING, sourceStart=NUMBER)
WINDOW = obj(start=NUMBER, end=NUMBER)
REPLAN = obj(**PLAN["properties"], cutaways=dict(type="array", items=CUTAWAY), reframes=dict(type="array", items=REFRAME),
             visualChecks=dict(type="array", items=obj(before=WINDOW, after=WINDOW)))


def executable(plan):
    return dict(cuts=[[c["segment"], round(c["start"]*30), round(c["end"]*30), c["speed"]]
                      for c in plan["cuts"]], cutaways=plan.get("cutaways", []), reframes=plan.get("reframes", []), musicBreaks=plan["musicBreaks"])


def check_revision(plan, baseline, previous_timeline=None, source_frames=None):
    last_stop = {}
    for edit in plan.get("cutaways", []):
        sid, source = edit["segment"], edit["sourceSegment"]
        first, stop, source_first = [round(edit[k]*30) for k in ("start", "end", "sourceStart")]
        if not (0 <= first < stop and first >= last_stop.get(sid, 0) and any(
                c["segment"] == sid and round(c["start"]*30) <= first < stop <= round(c["end"]*30)
                for c in plan["cuts"])):
            raise ValueError("换画面的区间须按时间排列且位于本段保留剪点内")
        if source not in (source_frames or {}) or source_first < 0 or source_first+stop-first > source_frames[source]:
            raise ValueError("替换镜头超出本次采用的源素材范围")
        if sid == source and first == source_first:
            raise ValueError("替换镜头与原镜头使用了相同的帧")
        last_stop[sid] = stop
    last = {}
    for frame in plan["reframes"]:
        sid, a, b = frame["segment"], frame["start"], frame["end"]
        if not (round(b*30) > round(a*30) and a >= last.get(sid, 0) and 1 < frame["zoom"] <= 4
                and 0 <= frame["x"] <= 1 and 0 <= frame["y"] <= 1 and any(
                    c["segment"] == sid and c["start"] <= a < b <= c["end"] for c in plan["cuts"])):
            raise ValueError("局部构图须位于一个保留镜头内，按时间排列且互不重叠")
        last[sid] = b
    if baseline is not None and executable(plan) == executable(baseline):
        raise ValueError("这次只改了说明，没有可执行的剪点、画面或配乐调整。请落实导演反馈；素材做不到时说明具体原因。")
    if not plan["visualChecks"]:
        raise ValueError("重排画面必须提供与导演原话对应的旧成片和新版验收区间")
    for check in plan["visualChecks"]:
        for w in check.values():
            if not 0 <= w["start"] < w["end"]:
                raise ValueError("验收区间必须为成片中的有效起止时间")

    if baseline and previous_timeline and len(previous_timeline) == len(baseline["cuts"]):
        allowed = {cut["segment"] for cut,t in zip(baseline["cuts"], previous_timeline)
                   for check in plan["visualChecks"]
                   if t["out0"] < check["before"]["end"] and t["out1"] > check["before"]["start"]}
        old, new = executable(baseline), executable(plan)
        def by_segment(value, sid):
            return ([c for c in value["cuts"] if c[0] == sid],
                    [r for r in value["reframes"] if r["segment"] == sid],
                    [r for r in value["cutaways"] if r["segment"] == sid],
                    [m for m in value["musicBreaks"] if m["segment"] == sid])
        for sid in {c[0] for c in [*old["cuts"], *new["cuts"]]} - allowed:
            if by_segment(old, sid) != by_segment(new, sid):
                raise ValueError(sid + " 不在本次反馈对应的旧成片区间，请保留该段原剪法")


def run_skill(action, work):
    result = subprocess.run([sys.executable, str(SKILL/"launch.py"), "finecut/workbench_run.py", action, str(work)],
                            env={**os.environ, "PYTHONUNBUFFERED":"1"}, capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise ValueError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "精剪工具未能完成")


def check_plan(plan, clips):
    try:
        validate(plan, PLAN)
    except ValidationError as error:
        raise ValueError("精剪专家返回的剪辑表格式不完整") from error
    order = list(clips)
    last_index, last_end = -1, 0
    for cut in plan["cuts"]:
        sid = cut["segment"]
        if sid not in clips:
            raise ValueError("剪辑表包含其他集的分段")
        i = order.index(sid)
        review = clips[sid]["review"]
        if (i < last_index or i == last_index and cut["start"] < last_end or
                not review["usableStart"] <= cut["start"] < cut["end"] <= review["usableEnd"] or
                cut["end"] - cut["start"] < .08 or not .5 <= cut["speed"] <= 2):
            raise ValueError(f"{sid} 的剪辑表超出采用区间或镜头次序冲突")
        last_index, last_end = i, cut["end"]
    if not plan["cuts"]:
        raise ValueError("剪辑表中没有可用画面")
    for item in [*plan["events"], *plan["musicBreaks"]]:
        times = [item["second"]] if "second" in item else [item["start"], item["end"]]
        if not any(c["segment"] == item["segment"] and c["start"] <= times[0] <= times[-1] < c["end"]
                   for c in plan["cuts"]):
            raise ValueError("声音事件超出保留画面")


def editing_plan(data, delivery, work, inputs, changes=(), baseline=None, previous_timeline=None, retry_finding=None):
    from .filmedit import script_material
    executor, slot, conn, root = episode_expert(data, delivery, "create", "finecut")
    script = (root/"03_精剪交接.md")
    brief = script.read_text() if script.is_file() else ""
    screenplay, dialogue = script_material(root)
    segments = json.loads((work/"segments.json").read_bytes())["segments"]
    script_order = [s["id"] for s in sorted(screenplay.get("shootingSegments", []), key=lambda row: row.get("order", 0))]
    required = set(script_order)
    source_ids = [c["segment"] for c in inputs["clips"]]
    if required.difference(source_ids):
        raise ValueError("采用剧本的分段缺少原片：" + "、".join(sorted(required.difference(source_ids))))
    if [sid for sid in source_ids if sid in required] != script_order:
        raise ValueError("采用原片的段序与剧本不符")
    dialogue.update(op["segment"] for change in changes for op in change["edits"] if op["action"] == "restore_dialogue")
    protected = {sid:round(s["rough_t1"]-s["rough_t0"], 4) for sid,s in zip(source_ids, segments) if sid in dialogue}
    if dialogue.difference(protected):
        raise ValueError("无法定位对白原片：" + "、".join(sorted(dialogue.difference(protected))))
    corrections = [change["text"] for change in changes if any(op["action"] == "replan" for op in change["edits"])]
    reports = json.loads((work/"体检/analysis.json").read_bytes())
    beats = json.loads((work/"bgm_beats.json").read_bytes())
    rules = (SKILL/"SKILL.md").read_text() + (SKILL/"references/intake-handoff.md").read_text()
    outro_seconds = json.loads((work/"outro-duration.json").read_bytes())
    context = dict(minFinalSeconds=MIN_FINAL_SECONDS, outroSeconds=outro_seconds, handoff=brief, clips=inputs["clips"], segments=segments,
                   adoptedScript=screenplay, dialogueDurations=protected, directorCorrections=corrections,
                   issues=reports["issues"], shots=reports["shots"], music={k:beats[k] for k in ("bpm","downbeats","duration")})
    timing = root / "分段与时长预算.json"
    if timing.is_file():
        context["scriptSeconds"] = json.loads(timing.read_bytes()).get("total_ms",0)/1000
    schema = REPLAN if corrections else PLAN
    stamp = sha256_bytes(canonical([schema, context, rules, slot, inputs["bgmSha256"]]))
    if retry_finding:
        context["未通过的成片验收"] = retry_finding
    path = work/"expert-plan.json"
    clips = {c["segment"]:c for c in inputs["clips"]}
    if path.exists():
        saved = json.loads(path.read_bytes())
        if saved["stamp"] == stamp and not retry_finding:
            return saved["plan"]
        if baseline is None:
            baseline = saved["plan"]
    if corrections and baseline:
        context["previousBaseCuts"] = baseline["cuts"]
        context["previousCutaways"] = baseline.get("cutaways", [])
        context["previousReframes"] = baseline.get("reframes", [])
        context["previousPublishedTimeline"] = previous_timeline or []
    revision_guidance = (
        "导演反馈按原话逐项落实，不得擅自换成别的目标。最新纠错针对上一版成片时间。"
        "可用 cutaways 单独换画面：segment、start/end 指目标原片段内区间；sourceSegment、sourceStart 指本集已采用原片中等长镜头的来源段号和起点秒数。可从同段或其他已采用段选镜。原对白及声音沿用目标区间。只选实际看得到且符合采用剧本和导演原话的画面，保留未涉及的已用换镜。无需换镜填空数组。"
        "局部重新构图必须写入 reframes：段内原片 start/end 秒、放大 zoom、水平和垂直裁切位置 x/y（0 左/上，1 右/下）。"
        "只改画面，不剪断对白。note 和 summary 只是说明，无法执行操作。保留仍需沿用的旧构图。"
        "visualChecks 列出反馈涉及的每处修改在旧成片 before 和候选成片 after 中的起止秒数，包含衔接前后。"
        "如果原话要求看见某个状态，不得用把它藏到画外来代替。素材无法达到要求时，在 summary 说明缺少什么，不伪造改动。"
        if corrections else ""
    )
    task = work/"expert"
    task.mkdir(exist_ok=True)
    req = ExecRequest("finecut-"+stamp[:16], "finecut", "draft",
                      "你是本集精剪师。按随附完整技能、采用版的逐段审片、实际镜头联系表和交接定剪。"
                      "全部分段都有真实视频。依据实际表演、剧情和接点优先选优秀镜头，采用剧本是最高剪辑依据。逐段保留剧情因果、关键动作、人物反应、全部对白及先后次序。不得自行删戏、改意或新增剧情。只可在不影响这些内容的前提下收紧停顿。视觉审片建议不能覆盖剧本。按 directorCorrections 基于 previousBaseCuts 修改，保留无关剪点。已记录的数值微调由渲染器单独应用。dialogueDurations 中的分段没有实测逐句时间，整段保留原速原声，从0到所列时长；不得依据嘴型猜剪点。cuts 按原段顺序，每段可含多个保留镜头，起止用各段原片秒数，其余分段落在 usableStart/End 内。"
                      "成片含实际品牌片尾达到 minFinalSeconds 即通过时长要求，无上限。交接和分段里的时长只用于前期规划。按剧情和节奏选镜，不为贴近规划时长拉长、缩短或报告差额。note 说明这一刀。"
                      "events 只写确有依据的画内声锚点，musicBreaks 可指定为动作让位的停乐区间，均用各段原片秒数。"
                      "按每刀 (end-start)/speed 核算正片时长，再加 outroSeconds 得到成片时长。speed 默认 1，只在动作或喜剧反应需要时适度变速。不要虚报时长。体检测得的疑点须结合图像判断。注明未验证的录音细节。\n" + rules + "\n" +
                      revision_guidance + "\n" + json.dumps(context, ensure_ascii=False), schema, "ephemeral", task, slot.get("model"),
                      slot.get("effort"), images=[Path(p) for p in reports["contact_sheets"]], connection=conn)
    for attempt in range(2):
        step("写稿 AI 正在拟剪辑表")
        result = executor.start(req)
        if not result.ok:
            from .videoreview import MediaCallError
            raise MediaCallError("写稿人未完成剪辑表：" + str(result.diagnostic)[:300])
        plan = result.structured
        try:
            validate(plan, schema)
            # Visual review cannot establish where the recorded line ends.
            # Build protected spans from the assembled sources, including an omitted dialogue segment.
            if any(c["segment"] not in clips for c in plan["cuts"]):
                raise ValueError("剪辑表包含其他集的分段")
            cut_order = [source_ids.index(c["segment"]) for c in plan["cuts"]]
            if cut_order != sorted(cut_order):
                raise ValueError("剪辑表与剧本段序不符")
            adjusted = []
            for sid in source_ids:
                if sid in protected:
                    adjusted.append(dict(segment=sid, start=0, end=protected[sid], speed=1, note="保留采用剧本对白及连续原声"))
                else:
                    adjusted.extend(c for c in plan["cuts"] if c["segment"] == sid)
            plan = dict(plan, cuts=adjusted, dialogueDurations=protected)
            check_plan({k:plan[k] for k in PLAN["properties"]}, {
                sid:dict(c, review=dict(usableStart=0, usableEnd=protected[sid])) if sid in protected else c
                for sid,c in clips.items()})
            if corrections:
                source_frames = {sid:round(s["rough_t1"]*30)-round(s["rough_t0"]*30) for sid,s in zip(source_ids,segments)}
                check_revision(plan, baseline, previous_timeline, source_frames)
            missing = (required or set(source_ids)).difference(c["segment"] for c in adjusted)
            if missing:
                raise ValueError("剪辑表漏掉采用剧本分段：" + "、".join(sorted(missing)))
            seconds = fsum((c["end"]-c["start"])/c["speed"] for c in plan["cuts"]) + outro_seconds
            if round(seconds, 9) < MIN_FINAL_SECONDS:
                raise ValueError(f"含片尾预计 {seconds:.3f} 秒，成片至少需要 {MIN_FINAL_SECONDS} 秒。请从已有素材中保留有价值的表演。")
            break
        except (ValueError, ValidationError) as exc:
            if attempt:
                raise ValueError(str(exc)[:500]) from exc
            req = replace(req, run_id=req.run_id+"-edit",
                          prompt=req.prompt+"\n上一版剪辑表："+json.dumps(plan,ensure_ascii=False)+"\n"+str(exc))
    keep_previous_reply(path)
    write_atomic(path, canonical(dict(stamp=stamp,plan=plan,selection=slot)))
    return plan


def feedback_sheet(movie, window, path):
    from PIL import Image, ImageDraw
    from .filmedit import probe
    duration = float(probe(movie)["format"]["duration"])
    if window["start"] >= duration or window["end"] > duration + .05:
        raise ValueError("反馈验收区间超出成片，无法核对指定画面")
    start, end = max(0, window["start"]-.5), min(duration, window["end"]+.5)
    rate = min(8, 16/(end-start))
    w, h = 256, 320
    vf = (f"fps={rate},scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
    frames = subprocess.check_output(["ffmpeg","-nostdin","-v","error","-ss",str(start),"-i",str(movie),
        "-t",str(end-start),"-vf",vf,"-frames:v","16","-an","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],timeout=90)
    stride = w*h*3
    count = len(frames)//stride
    if not count:
        raise ValueError("反馈指定位置未取到实际画面")
    sheet = Image.new("RGB",(4*w, ((count+3)//4)*(h+20)))
    draw = ImageDraw.Draw(sheet)
    for i in range(count):
        x,y = i%4*w, i//4*(h+20)
        sheet.paste(Image.frombytes("RGB", (w,h), frames[i*stride:(i+1)*stride]), (x,y))
        draw.text((x+3,y+h), f"{start+i/rate:.2f}s", fill="white")
    sheet.save(path, quality=90)


class FeedbackMismatch(ValueError):
    pass


def verify_feedback(data, delivery, work, before, candidate, feedback, plan):
    from .filmedit import script_material
    executor, slot, connection, root = episode_expert(data, delivery, "review", "finecut")
    script, _ = script_material(root)
    review = work/"反馈验收"
    review.mkdir(exist_ok=True)
    images = []
    for i, check in enumerate(plan["visualChecks"]):
        for label, movie, window in (("旧版",before,check["before"]), ("新版",candidate,check["after"])):
            if label == "旧版" and not movie.is_file():
                continue
            path = review/f"{i+1}-{label}.jpg"
            feedback_sheet(movie, window, path)
            images.append(path)
    timeline = json.loads((work/"timeline.json").read_bytes())["clips"]
    targets = {c["segment"] for c,t in zip(plan["cuts"],timeline) for check in plan["visualChecks"]
               if t["out0"] <= check["after"]["end"]+.5 and t["out1"] >= check["after"]["start"]-.5}
    script_context = [s for s in script.get("shootingSegments", []) if s["id"] in targets] or script.get("scenes", [])
    schema = obj(passed={"type":"boolean"}, finding=STRING)
    prompt = ("独立验收成片。以下是导演原话及从 MP4 解码的前后连续画面。以导演原话为准，逐条判断每项要求。核对送审区间与原话是否一致，擅自扩大、漏改、改错位置均不通过。按图片顺序和标注秒数逐项检查原话所指的问题，"
              "核对给出的区间是否覆盖导演所指时间及前后衔接。以新版中实际可见的结果判断。"
              "换了构图不等于问题解决。避免露出错误状态可以解决跳变，但若原话要求展现某个动作或状态，须真能看到。"
              "未满足、画面不清、检查错位置、或缺少证据时 passed=false。finding 写明秒数、可见状态和仍待处理之处。"
              "采用剧本要求：" + json.dumps(script_context,ensure_ascii=False) + "\n导演原话：" + feedback + "。图片顺序：" + "、".join(p.name for p in images))
    (review/"expert").mkdir(exist_ok=True)
    step("独立评审正在核对修改前后的成片")
    result = executor.start(ExecRequest("cut-review-"+sha256_bytes(canonical([feedback,plan]))[:16], "finecut", "review",
        prompt, schema, "ephemeral", review/"expert", slot.get("model"), slot.get("effort"), images=images, connection=connection))
    if not result.ok:
        from .videoreview import MediaCallError
        raise MediaCallError("新版的导演反馈尚未验收：" + str(result.diagnostic)[:260])
    try:
        validate(result.structured, schema)
    except ValidationError as exc:
        raise ValueError("成片反馈验收未返回有效结论") from exc
    verdict = result.structured
    if not verdict["finding"].strip():
        raise ValueError("成片反馈验收缺少具体画面依据")
    keep_previous_reply(review/"result.json")
    write_atomic(review/"result.json", canonical(dict(feedback=feedback, selection=slot, **verdict)))
    if not verdict["passed"]:
        from .videoreview import decision_schema, media_verdict
        step("专家 AI 正在根据独立验收意见决定是否返修剪辑")
        expert_decision = media_verdict(
            data, delivery, "expert", "finecut_decision", review/"expert",
            "独立评审认为新版成片尚未完全满足导演反馈，请决定退回写稿人重拟剪辑表（revise）或按当前版继续进入成片复核（adopt），写清具体剪点或理由。"
            + json.dumps(dict(feedback=feedback, independentReview=verdict), ensure_ascii=False),
            decision_schema(["revise", "adopt"]),
        )["result"]
        write_atomic(review/"expert-decision.json", canonical(expert_decision))
        if expert_decision["action"] == "revise":
            raise FeedbackMismatch("专家决定按反馈返修成片：" + expert_decision["reason"] + "（独审依据：" + verdict["finding"] + "）")
    return verdict["finding"]


def render(data, delivery, inputs, changes, output):
    work = output.parent/"精剪过程"
    work.mkdir(exist_ok=True)
    request = dict(clips=[dict(c, file=str(data/c["video"])) for c in inputs["clips"]],
                   bgm=str(data/inputs["bgm"]), bgmSha256=inputs["bgmSha256"])
    write_atomic(work/"request.json", canonical(request))
    step("工作台正在检查原片与配乐")
    run_skill("prepare", work)
    # Keep the plan of the last published movie across blocked or interrupted attempts.
    from .filmedit import status
    from .storage import file_sha256
    published = status(output.parent).get("published")
    previous = work/"published-plan.json"
    saved = json.loads(previous.read_bytes()) if previous.is_file() else {}
    baseline = saved.get("plan")
    if published and saved.get("revision") != published and (work/"expert-plan.json").is_file():
        baseline = json.loads((work/"expert-plan.json").read_bytes())["plan"]
        timeline_path = work/"timeline.json"
        timeline = json.loads(timeline_path.read_bytes())["clips"] if timeline_path.is_file() else []
        saved = dict(revision=published, plan=baseline, timeline=timeline)
        write_atomic(previous, canonical(saved))
    pending = changes
    if published:
        input_hash = file_sha256(output.parent/"inputs.json")
        for n in range(len(changes), -1, -1):
            if sha256_bytes(canonical([input_hash,changes[:n]])) == published:
                pending = changes[n:]
                break
    feedback = "\n".join(change["text"] for change in pending if any(op["action"]=="replan" for op in change["edits"]))
    finding = None
    retry = None
    for attempt in range(2):
        plan = editing_plan(data, delivery, work, inputs, changes, baseline, saved.get("timeline"), retry)
        receipt = work/"待核验成片.json"
        stamp = sha256_bytes(canonical([inputs, changes, plan]))
        encoded = json.loads(receipt.read_bytes()) if receipt.is_file() else {}
        movie = work/encoded.get("file", "")
        if not (encoded.get("plan")==stamp and movie.resolve().is_relative_to(work.resolve())
                and movie.is_file() and file_sha256(movie)==encoded.get("sha256")):
            movie = render_plan(work, plan, changes)
            write_atomic(receipt, canonical(dict(plan=stamp, file=str(movie.relative_to(work)), sha256=file_sha256(movie))))
        try:
            feedback_result = verify_feedback(data, delivery, work, output.with_name("final.mp4"), movie, feedback, plan) if feedback else None
            from .videoreview import verify_movie
            from .filmedit import script_material
            _, _, _, root = episode_expert(data, delivery, "create", "finecut")
            script, _ = script_material(root)
            finding = verify_movie(data, delivery, movie, work/"成片复核", dict(script=script, cuts=plan["cuts"], feedback=feedback_result))

            break
        except FeedbackMismatch as exc:
            if attempt:
                raise
            retry = dict(验收意见=str(exc), 未通过的剪辑安排=executable(plan))

    os.replace(movie, output)
    timeline_path = work/"timeline.json"
    timeline = json.loads(timeline_path.read_bytes())
    timeline["outputs"] = [str(output.with_name("final.mp4"))]
    write_atomic(timeline_path, canonical(timeline))
    return finding


def render_plan(work, plan, changes):
    from .filmedit import check_dialogue_changes, operations
    cuts = [dict(c, source_db=0) for c in plan["cuts"]]
    protected = plan["dialogueDurations"]
    check_dialogue_changes(changes, protected)
    gain, timing = -7, set(protected)
    for change in changes:
        for op in operations(change["edits"], {c["segment"] for c in cuts}):
            action, val, sid = op["action"], op["value"], op["segment"]
            items = [c for c in cuts if c["segment"] == sid]
            if action == "restore_dialogue":
                for c in items:
                    c["source_db"] = 0
                continue
            if action == "replan":
                continue
            if sid in protected and action in ("set_start","set_end","trim_head","trim_tail","speed"):
                continue
            if action == "bgm_db":
                gain = val
            elif action in ("speed","source_db"):
                for c in items:
                    c[action] = val
            else:
                timing.add(sid)
                if action == "set_start": items[0]["start"] = val
                elif action == "trim_head": items[0]["start"] += val
                elif action == "set_end": items[-1]["end"] = val
                elif action == "trim_tail": items[-1]["end"] -= val
    write_atomic(work/"edit.json", canonical(dict(clips=cuts, gain=gain, timingEdits=sorted(timing), dialogueDurations=protected,
                                                 cutaways=plan.get("cutaways", []), reframes=plan.get("reframes", []), events=plan["events"], musicBreaks=plan["musicBreaks"], minFinalSeconds=MIN_FINAL_SECONDS, summary=plan["summary"])))
    step("工作台正在编码成片")
    run_skill("finish", work)
    return Path(json.loads((work/"timeline.json").read_bytes())["outputs"][0])
