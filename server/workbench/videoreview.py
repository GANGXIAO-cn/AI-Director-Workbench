"""Review actual timestamped frames with the episode's existing image-capable expert."""
import io
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw
from jsonschema import validate

from .production_steps import step
from .executors.base import ExecRequest
from .storage import canonical, file_sha256, write_atomic


def state_frame(video, directory, seconds, usable_start=0, usable_end=None):
    """Export one original frame with its source time; it is a candidate for later binding."""
    video = Path(video)
    data = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time:stream=start_time,duration",
            "-of",
            "json",
            str(video),
        ]
    )
    info = json.loads(data)
    times = [float(f["best_effort_timestamp_time"]) for f in info["frames"]]
    stream = info["streams"][0]
    end = (float(stream.get("start_time", 0)) + float(stream["duration"])
           if stream.get("duration") not in (None, "N/A") else (times[-1] if times else 0))
    if not times or not times[0] <= seconds <= end:
        raise ValueError("状态图时间必须位于实际画面范围内")
    indices = [i for i,t in enumerate(times) if usable_start <= t and (usable_end is None or t <= usable_end)]
    if not indices:
        return None
    index = min(indices, key=lambda i: abs(times[i] - seconds))
    name = f"state-frame-{index}.png"
    cached = directory / (name + ".json")
    if cached.is_file() and (directory / name).is_file():
        record = json.loads(cached.read_bytes())
        if record["sourceHash"] == file_sha256(video) and record[
            "imageHash"
        ] == file_sha256(directory / name):
            return record
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-vf",
            f"select=eq(n\\,{index})",
            "-frames:v",
            "1",
            "-c:v",
            "png",
            "-f",
            "image2pipe",
            "pipe:1",
        ]
    )
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("该时间未取得可用原帧")
    name = f"state-frame-{index}.png"
    write_atomic(directory / name, raw)
    record = dict(
        file=name,
        sourceVideo=video.name,
        sourceHash=file_sha256(video),
        frame=index,
        seconds=times[index],
        imageHash=file_sha256(directory / name),
        status="待绑定",
    )
    write_atomic(directory / (name + ".json"), canonical(record))
    return record


def frame_sheets(video, directory, *, whole_movie=False):
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(video),
            ]
        )
    )
    duration = float(info["format"]["duration"])
    if duration <= 0 or (duration > 30 and not whole_movie):
        raise ValueError("试审入口只接收单个 H3 短分段")
    width, height = 224, 280
    interval = max(0.24, duration / 160) if whole_movie else 0.24
    filt = (
        rf"select=isnan(prev_selected_t)+gte(t-prev_selected_t\,{interval}),"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,showinfo"
    )
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-vf",
            filt,
            "-fps_mode",
            "vfr",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    times = [
        float(v)
        for v in re.findall(rb"\bn:\s*\d+.*?pts_time:([\d.eE+-]+)", proc.stderr)
    ]
    stride = width * height * 3
    if not times or len(times) * stride != len(proc.stdout):
        raise ValueError("无法对应实际解码的帧与时间")
    paths = []
    for start in range(0, len(times), 16):
        sheet = Image.new(
            "RGB",
            (4 * width, ((min(16, len(times) - start) + 3) // 4) * (height + 20)),
            "black",
        )
        draw = ImageDraw.Draw(sheet)
        for index, t in enumerate(times[start : start + 16], start):
            x, y = (index - start) % 4 * width, (index - start) // 4 * (height + 20)
            frame = Image.frombytes(
                "RGB",
                (width, height),
                proc.stdout[index * stride : (index + 1) * stride],
            )
            sheet.paste(frame, (x, y))
            draw.text((x + 5, y + height + 2), f"{t:.3f} s", fill="white")
        path = directory / f"frames-{start//16+1}.jpg"
        buffer = io.BytesIO()
        sheet.save(buffer, format="JPEG", quality=86)
        write_atomic(path, buffer.getvalue())
        paths.append(path)
    return info, times, paths


def visual_review(
    video: Path, directory: Path, executor, slot, connection, brief, source_images=(), receipt_name="visual-review.json"
):
    if not getattr(executor, "reads_images", False):
        raise ValueError("本集评审连接没有图像输入能力")
    digest = file_sha256(video)
    receipt = directory / receipt_name
    evidence = {"sha256": digest, "expert": slot, "brief": brief}
    if receipt.exists():
        saved = json.loads(receipt.read_bytes())
        previous = dict(saved["evidence"])
        previous["brief"] = dict(previous["brief"])
        if "future" in brief:
            previous["brief"]["future"] = brief["future"]
        else:
            previous["brief"].pop("future", None)
        if previous == evidence:
            return saved
        raise ValueError("已有评审对应其他输入，请使用新的试审目录")
    work = directory / "work"
    work.mkdir(parents=True, exist_ok=True)
    media, times, images = frame_sheets(video, work)
    duration = float(media["format"]["duration"])
    props = {
        "decision": {"type": "string", "enum": ["采用", "采用并附剪点", "局部返工"]},
        "reason": {"type": "string"},
        "observations": {"type": "array", "items": {"type": "string"}},
        "usableStart": {"type": "number"},
        "usableEnd": {"type": "number"},
        "localChange": {"type": "string"},
    }
    if brief.get("shots"):
        props["affectedShots"] = {"type": "array", "items": {"type": "integer"}}
    props["stateSecond"] = {"type": ["number", "null"]}
    if source_images:
        props["join"] = {"type": "string", "enum": ["可直接剪", "调整剪点可用", "关键承接失败"]}
        props["cutAdvice"] = {"type": "string"}
    if brief.get("future"):
        fields = dict(
            target={"type": "string"},
            slot={"type": "integer"},
            note={"type": "string"},
            seconds={"type": "number"},
        )
        props["carryForward"] = {
            "type": "array",
            "items": dict(
                type="object",
                properties=fields,
                required=list(fields),
                additionalProperties=False,
            ),
        }
    schema = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
        "required": list(props),
    }
    prompt = (
        "请作为本集独立评审审这一段真实素材，中文回答。图片按原片实际时间排列，时间印在帧下。"
        "先描述看到的动作与转折和时间区间，核对节奏与核心剧情。能通过调整入点、出点保住必需动作就采用并附剪点。"
        "判返工前，先按观察到的时间尝试提前收尾或剪掉中间多余动作。若足以保留关键反应和剧情，"
        "给出可用区间与剪法；若仍须返工，在 reason 里说明哪项必需动作或状态无法通过剪辑保住。"
        "只能判断画面，这次没有向你发送声音。不得通过嘴型推断台词说完或推荐截短剧本对白。没有实测的对白起止时，有对白分段建议保留整段连续原速原声。区分剧本要求和实拍观察。"
        "逐段按采用剧本核对，剪点必须保住约定的关键动作、对白、反应与先后关系。可压缩与剧本无关的重复画面。核对整集所需节奏、实际可用秒数和跨段因果。如必须返工，仅指出局部需要替换的一句话。"
        "usableStart/End 为相对原片秒数，完全无可用范围填 0、0。"
        f"原片 {duration} 秒。若保留至片尾，usableEnd 可直接填此秒数。剧本、分镜及当前段要求：\n" + json.dumps(brief, ensure_ascii=False)
    )
    if brief.get("shots"):
        prompt += " 局部返工须在 affectedShots 填本段分镜顺序（从1起），在 reason 写实际时间区间与可见缺陷。"
    prompt += " 指出后段可引用的清晰状态帧时间 stateSecond，无合适画面填 null。"
    if brief.get("future"):
        prompt += (
            "对照 future 里的后段要求，看本段真实画面是否产生必须继承的道具状态。"
            "有明确需要才填 carryForward，给目标段、目标图片位、可见该状态的准确原帧秒数、局部参考指令。"
            "planned 是已绑定的来源和图位。只补尚未覆盖的状态，避开已占用图位和受保护的图。追加从首个空位连续编号，最多九张。无需补图填空列表。"
        )
    if source_images:
        prompt += (
            f" 前 {len(source_images)} 张联系表来自来源段，顺序及各段张数见 continuity；其后是本段。"
            "核对两段实际画面的出入点、物品状态和运动，给出具体剪接秒数及依据。叙事可剪即可。"
            "关键承接失败只在不能通过剪辑交代剧情时使用；这种情况下 decision 选局部返工。"
        )
    req = ExecRequest(
        "video-trial-" + digest[:16],
        "video_review",
        "review",
        prompt,
        schema,
        "ephemeral",
        work,
        slot.get("model"),
        slot.get("effort"),
        images=[*source_images, *images],
        connection=connection,
    )
    step("独立评审正在审本版视频")
    result = executor.start(req)
    if not result.ok:
        raise MediaCallError(f"独立评审调用失败 {result.error_class}: {result.diagnostic}")
    reply = result.structured
    validate(reply, schema)
    if brief.get("shots"):
        shots_n = len(brief["shots"])
        deduped = list(dict.fromkeys(reply["affectedShots"]))
        if any(not (1 <= n <= shots_n) for n in deduped):
            raise ValueError(f"受影响镜号超出范围（本段共 {shots_n} 镜）")
        reply["affectedShots"] = deduped
    if brief.get("shots") and (reply["decision"]=="局部返工" or reply.get("join")=="关键承接失败") and (not reply["affectedShots"] or not reply["localChange"].strip()):
        raise ValueError("局部返工缺少镜号或具体改法")
    start, end = reply["usableStart"], reply["usableEnd"]
    # A millisecond label can round the exact end up. Store the real endpoint
    # so downstream extraction and trimming never receive an out-of-range time.
    labelled_end = float(f"{duration:.3f}")
    if not (0 <= start <= end and (end <= duration or end == labelled_end)):
        raise ValueError(f"审片可用区间 {start}–{end} 秒无效，实际素材范围为 0–{duration} 秒")
    if end > duration:
        for key in ("usableStart", "usableEnd", "stateSecond"):
            if reply[key] == labelled_end:
                reply[key] = duration
        for carry in reply.get("carryForward", []):
            if carry["seconds"] == labelled_end:
                carry["seconds"] = duration
        if reply["usableStart"] > duration:
            raise ValueError(f"审片入点 {start} 秒超过片尾 {duration} 秒")
    if reply["decision"] != "局部返工" and reply["usableStart"] >= reply["usableEnd"]:
        raise ValueError("独立评审没有给出可采用的画面区间")
    if (
        reply["stateSecond"] is not None
        and not reply["usableStart"] <= reply["stateSecond"] <= reply["usableEnd"]
    ):
        reply["stateSecond"] = None
    record = dict(
        evidence=evidence,
        media=media,
        frameTimes=times,
        visualReview=reply,
        audioReview="未向本次独立评审通道提供声音",
        session=result.session_id,
    )
    write_atomic(receipt, canonical(record))
    return record


class MediaCallError(RuntimeError):
    """The selected executor did not return a result."""


def media_verdict(data, delivery, role, purpose, folder, prompt, schema, images=()):
    from .videosegment import episode_expert
    from .storage import sha256_bytes
    stage = "finecut" if purpose.startswith("finecut_") else "package"
    executor, slot, connection, _ = episode_expert(data, delivery, role, stage)
    basis = sha256_bytes(canonical([slot, prompt, schema, [file_sha256(p) for p in images]]))
    path = folder / (purpose + ".json")
    if path.is_file():
        saved = json.loads(path.read_bytes())
        if saved.get("basis") == basis:
            return saved
    folder.mkdir(parents=True, exist_ok=True)
    if images and not getattr(executor, "reads_images", False):
        raise ValueError("本集独立评审无法读取成片画面")
    labels = {"video_decision":"专家正在决定采用、剪辑或重拍", "finecut_plan":"写稿 AI 正在拟剪辑表",
              "finecut_review":"独立评审正在看成片画面", "finecut_decision":"专家正在决定是否交片"}
    if purpose in labels:
        step(labels[purpose])
    result = executor.start(ExecRequest(purpose+"-"+basis[:16], purpose, "review" if role=="review" else "draft",
        prompt, schema, "ephemeral", folder, slot.get("model"), slot.get("effort"), images=list(images), connection=connection))
    if not result.ok:
        raise MediaCallError(purpose+" 调用失败："+str(result.diagnostic))
    from jsonschema import ValidationError
    try:
        validate(result.structured, schema)
    except ValidationError as exc:
        raise ValueError("评审或决定格式错误："+exc.message) from exc
    record = dict(basis=basis, role=role, selection=slot, result=result.structured)
    write_atomic(path, canonical(record))
    return record


def decision_schema(options):
    fields = dict(action={"enum":options}, reason={"type":"string","minLength":1})
    return dict(type="object",properties=fields,required=list(fields),additionalProperties=False)


def verify_movie(data, delivery, movie, folder, context):
    folder.mkdir(exist_ok=True, parents=True)
    digest = file_sha256(movie)
    evidence = folder / "frames.json"
    frames = json.loads(evidence.read_bytes()) if evidence.exists() else {}
    if frames.get("video") != digest or not all((folder / name).is_file() for name in frames.get("images", [])):
        media, times, paths = frame_sheets(movie, folder, whole_movie=True)
        frames = dict(video=digest, media=media, times=times, images=[p.name for p in paths])
        write_atomic(evidence, canonical(frames))
    material = dict(video=digest, duration=frames["media"]["format"]["duration"], context=context, audio="未试听音频")
    reviewer = media_verdict(data, delivery, "review", "finecut_review", folder,
        "独立返看成片采样画面。对照采用剧本核对动作顺序、跨段衔接、穿帮与节奏。reason 指明实际观察和秒数；画面需要修改则选 revise。音频未试听。"+json.dumps(material,ensure_ascii=False),
        decision_schema(["pass","revise"]), [folder/p for p in frames["images"]])
    decision = media_verdict(data, delivery, "expert", "finecut_decision", folder,
        "决定是否交片。结合独立评审的可见证据与剧本，采用或返回具体剪辑修改意见。"+json.dumps([material,reviewer["result"]],ensure_ascii=False),
        decision_schema(["adopt","revise"]))
    if decision["result"]["action"] == "revise":
        from .bundledfinecut import FeedbackMismatch
        raise FeedbackMismatch(decision["result"]["reason"]+"；独审："+reviewer["result"]["reason"])
    return dict(video=digest,review=reviewer["result"],decision=decision["result"],audio="未试听音频")


def pick_source_time(data, delivery, folder, report, note, progress_label="专家正在选承接帧"):
    """Select a source frame for this particular prop, including late-added dependencies."""
    from .videosegment import episode_expert
    from .storage import sha256_bytes

    executor, slot, connection, _ = episode_expert(data, delivery)
    times = report["frameTimes"]
    verdict = report["visualReview"]
    times = [t for t in times if verdict["usableStart"] <= t <= verdict["usableEnd"]]
    if not times:
        return None
    digest = sha256_bytes(canonical([report["evidence"]["sha256"], times, note, slot]))
    cache = folder / "expert" / ("state-" + digest + ".json")
    if cache.exists():
        return json.loads(cache.read_bytes())["seconds"]
    schema = dict(
        type="object",
        properties={"seconds": {"type": ["number", "null"]}},
        required=["seconds"],
        additionalProperties=False,
    )
    work = folder / "expert" / ("pick-" + digest[:12]) / "work"
    work.mkdir(parents=True, exist_ok=True)
    images = sorted(
        (folder / "expert/work").glob("frames-*.jpg"),
        key=lambda p: int(p.stem.split("-")[-1]),
    )
    req = ExecRequest(
        "state-" + digest[:16],
        "video_state",
        "review",
        "从真实帧中选择最能看清这个状态的一帧；填图上标的秒数。"
        + "看不清填 null。可用时间："
        + str(times)
        + "。后段需要："
        + note,
        schema,
        "ephemeral",
        work,
        slot.get("model"),
        slot.get("effort"),
        images=images,
        connection=connection,
    )
    step(progress_label)
    result = executor.start(req)
    if not result.ok:
        reason = "无法核对来源状态帧：" + str(result.diagnostic)[:300]
        if result.error_class in ("timeout", "quota", "network"):
            raise MediaCallError(reason)
        raise ValueError(reason)
    validate(result.structured, schema)
    value = result.structured["seconds"]
    if value is None:
        write_atomic(cache, canonical(dict(seconds=None, note=note)))
        return None
    if min(abs(t - value) for t in times) > 0.01:
        raise ValueError("专家返回的状态时间与实际采样帧不符")
    seconds = min(times, key=lambda t: abs(t - value))
    write_atomic(cache, canonical(dict(seconds=seconds, note=note)))
    return seconds
