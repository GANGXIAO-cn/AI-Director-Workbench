"""Compile an adopted delivery, generate a segment and review its actual media."""
import json
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx

from .production_steps import step
from .cloudtask import fetch_output
from .h3online import RunningHub, overrides, prompt_for_segment
from .storage import canonical, file_sha256, sha256_bytes, write_atomic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "resources/h3-packager"))
import packager


def load_segment(data, delivery, sid):
    with sqlite3.connect(
        (data / "workbench.sqlite").resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        row = db.execute(
            "SELECT relpath,manifest_hash,state FROM delivery WHERE id=?", (delivery,)
        ).fetchone()
    if not row or row[2] != "deliverable":
        raise ValueError("请选择当前有效交付")
    root = (data / row[0]).resolve()
    manifest = json.loads((root / "manifest.json").read_bytes())
    if (
        sha256_bytes(canonical(manifest)) != row[1]
        or manifest["deliveryId"] != delivery
    ):
        raise ValueError("交付清单与登记版本不同")
    project = packager.load_project(str(root / "00_图文对照.html"))
    if sha256_bytes(project["raw"]) != manifest["files"]["episode.json"]:
        raise ValueError("交付剧集版本已改变")
    _, package, inputs = packager.package_contents(project, [sid])
    seg = package["segments"][0]
    source = next(x for x in project["data"]["segments"] if x["id"] == sid)
    if file_sha256(root / source["prompt"]) != manifest["files"][source["prompt"]]:
        raise ValueError("提示词与采用版本不同")
    assets = {a["id"]: a for a in package["assets"]}
    seg["prompt"] = prompt_for_segment(
        seg["prompt"],
        seg["refs"],
        {k: v["kind"] for k, v in assets.items()},
        source.get("prompt_format") == "h3_official",
    )
    return seg, assets, inputs


from weakref import WeakValueDictionary

_workbenches = WeakValueDictionary()

def register_workbench(workbench):
    _workbenches[workbench.db_path.resolve()] = workbench

def episode_expert(data, delivery, role="expert", stage="package"):
    from .filmroles import selections
    from .executors.cli import CodexExecutor, AntigravityExecutor

    with sqlite3.connect(
        (data / "workbench.sqlite").resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        rel, episode_id = db.execute(
            "SELECT relpath,episode_id FROM delivery WHERE id=?", (delivery,)
        ).fetchone()

        def setting(name, default):
            row = db.execute(
                "SELECT value FROM app_setting WHERE key=?", (name,)
            ).fetchone()
            return json.loads(row[0]) if row else default

        slot = selections(db, episode_id, stage)[role]
        provider = slot.get("provider")
        workbench = _workbenches.get((data / "workbench.sqlite").resolve())
        if workbench is not None:
            slot = workbench.active_selection(slot)
            provider = slot['provider']
            connection = workbench.run_connection(provider, slot.get('model'), slot.get('effort'))
            return workbench.executors[provider], {**slot, **connection}, connection, data / rel
        if str(provider).startswith('api:'):
            from .executors.chat_api import ChatAPIExecutor
            from .local_credentials import LocalCredentials
            executor = ChatAPIExecutor(data / "workbench.sqlite", provider[4:], LocalCredentials(data))
            connection = setting("connection:"+provider,{})
            return executor, slot, connection, data / rel
        classes = {"codex": CodexExecutor, "antigravity": AntigravityExecutor}
        if provider not in classes:
            raise ValueError("本集所选角色连接未注册")
        executor = classes[provider](setting(provider + "_path", None))
        connection = setting("connection:" + provider, {})
    return executor, slot, connection, data / rel


def script_segments(script, board):
    if script.get("shootingSegments"):
        return script["shootingSegments"]
    # Scene-based scripts acquire their segment boundaries in the adopted storyboard.
    scenes = {scene["id"]: scene for scene in script.get("scenes", [])}
    segments = []
    for segment in board.get("segments", []):
        shots = [shot for shot in board["shots"] if shot["segment"] == segment["id"]]
        origins = []
        for shot in shots:
            origin = shot.get("origin")
            scene = origin.get("scene") if isinstance(origin, dict) else origin
            if scene in scenes and scene not in origins:
                origins.append(scene)
        segments.append(dict(id=segment["id"], action="；".join(shot.get("action", "") for shot in shots),
                             scenes=[scenes[key] for key in origins]))
    return segments


def review(
    data, delivery, segment, out, continuity=None, source_images=(), future=None
):
    from workbench.videoreview import visual_review, media_verdict, decision_schema

    state = json.loads((out / "trial.json").read_bytes())
    if state.get("status") != "downloaded" or file_sha256(
        out / "result.mp4"
    ) != state.get("sha256"):
        raise ValueError("当前试片尚无已核对的视频文件")
    executor, slot, connection, root = episode_expert(data, delivery, "review")
    manifest = json.loads((root / "manifest.json").read_bytes())

    def source(name):
        path = root / "交接来源" / name
        if file_sha256(path) != manifest["files"][path.relative_to(root).as_posix()]:
            raise ValueError("交接稿与采用版本不同")
        return json.loads(path.read_bytes())

    script, board = source("剧本.json"), source("制作方案.json")
    segments = script_segments(script, board)
    brief = dict(
        script=[s for s in segments if s["id"] == segment],
        shots=[
            {k: s.get(k) for k in ("id", "action", "reaction", "continuity", "sound")}
            for s in board["shots"]
            if s.get("segment") == segment
        ],
    )
    if not brief["shots"]:
        raise ValueError("没有找到本段采用分镜")
    if future:
        brief["future"] = [
            {
                "id": s["id"],
                "start": s.get("startState"),
                "action": s.get("action"),
                **future[s["id"]],
            }
            for s in segments
            if s["id"] in future
        ]
    if continuity:
        brief["continuity"] = continuity
    result = visual_review(
        out / "result.mp4",
        out / "expert",
        executor,
        slot,
        connection,
        brief,
        source_images,
        receipt_name="independent-review.json",
    )
    opinion = result["visualReview"]
    options = ["采用", "采用并附剪点"] if opinion["usableEnd"] > opinion["usableStart"] else []
    if opinion.get("affectedShots") and opinion.get("localChange","").strip():
        options.append("局部返工")
    if not options:
        options = ["局部返工"]
    join_warn = "注意：独立评审标记了「关键承接失败」，请重点评估是否需要局部返工或可否附剪点采用。" if opinion.get("join") == "关键承接失败" else ""
    verdict = media_verdict(data, delivery, "expert", "video_decision", out/"expert",
        "根据该版素材的独立审片观察，决定采用、保留剪点或按具体镜号返工。说明取舍依据。" + join_warn
        + json.dumps(dict(segment=segment,version=state["sha256"],shots=brief["shots"],review=opinion),ensure_ascii=False),
        decision_schema(options))
    chosen = dict(opinion, decision=verdict["result"]["action"], reason=verdict["result"]["reason"])
    if chosen["decision"]=="局部返工":
        chosen["localChange"] = " ".join("[Shot "+str(n)+"]" for n in opinion["affectedShots"])+" "+opinion["localChange"]
    final = dict(result, visualReview=chosen, independentReview=opinion, expertDecision=verdict)
    write_atomic(out/"expert/visual-review.json",canonical(final))
    return final


class CloudCapacity(Exception):
    """Provider has explicitly declined admission without creating a task."""


def reset_failed(out):
    receipt = out/"trial.json"
    state = json.loads(receipt.read_bytes())
    if state.get("status") not in ("cloud_failed","rejected"):
        raise ValueError("只有云端明确失败的任务能重新提交")
    archive = out/"failed-submission.json"
    if not archive.is_file():
        write_atomic(archive, canonical(state))
    state.update(task=None, status="retry_ready")
    write_atomic(receipt, canonical(state))


def generate(
    data,
    delivery,
    segment,
    out,
    account,
    *,
    workflow,
    submit=True,
    probe_empty=True,
    prepared=None,
    stop=None,
):
    seg, assets, inputs = prepared or load_segment(data, delivery, segment)
    key, site = account["key"], account["site"]
    receipt = out / "trial.json"
    saved = json.loads(receipt.read_bytes()) if receipt.exists() else None
    if saved is not None:
        workflow = saved["binding"]["workflow"]
    binding = dict(
        site=site,
        workflow=workflow,
        keyFingerprint=sha256_bytes(key.encode())[:16],
        delivery=delivery,
        segment=segment,
        inputHash=sha256_bytes(canonical([seg, assets])),
    )
    state = (
        saved
        if saved is not None
        else dict(binding=binding, files={}, seed=seg["params"]["seed"])
    )
    if saved and not saved.get("task") and saved.get("status") in ("retry_ready", "capacity"):
        # A declined cloud request may have used the old prompt compiler. Keep
        # the account, workflow, references and generation settings pinned.
        old_text = next((n["fieldValue"] for n in saved.get("nodeInfoList", [])
                         if n["nodeId"] == "263" and n["fieldName"] == "text"), None)
        if old_text is not None:
            old_hash = sha256_bytes(canonical([dict(seg, prompt=old_text), assets]))
            if saved["binding"] == dict(binding, inputHash=old_hash):
                state["binding"] = binding
    if state["binding"] != binding:
        raise ValueError("本次配置或采用稿与已有试片不同，请先核对原任务")

    def save():
        write_atomic(receipt, canonical(state))

    if state["seed"] is None:
        state["seed"] = secrets.randbelow(2**50)
    with httpx.Client(timeout=120) as client:
        hub = RunningHub(client, site, key)
        new_task = not state.get("task")
        if new_task:
            if state.get("status") in ("submitting", "rejected"):
                raise ValueError("此前已提交，请核对 trial.json；不会自动重提")
            step("工作台正在核对 H3 素材和参数")
            graph = hub.graph(workflow)
            instance = account.get("instance") or seg["params"]["instanceType"]

            def compile_nodes(files):
                return overrides(
                    graph,
                    seg,
                    assets,
                    files,
                    state["seed"],
                    probe_empty=probe_empty,
                )

            preview = compile_nodes({aid: "<待上传>" for aid in assets})
            print(
                "在线参数已核对，参考数",
                len(assets),
                "待验证的空参考位",
                sum(n["fieldValue"] is None for n in preview),
                flush=True,
            )
            if not submit:
                return
            for aid, a in assets.items():
                if aid not in state["files"]:
                    step("工作台正在上传参考素材")
                    state["files"][aid] = hub.upload(a["name"], inputs[a["path"]])
                    save()
            nodes = compile_nodes(state["files"])
            step("正在向 H3 提交，本轮接单待确认")
            state.update(status="submitting", nodeInfoList=nodes, instanceType=instance)
            save()
            result = hub.submit(workflow, nodes, instance)
            reply = result.get("data") or {}
            state.update(
                task=reply.get("taskId") if isinstance(reply, dict) else None,
                code=result.get("code"),
            )
            if not state["task"]:
                if str(state["code"]) in ("415", "421"):
                    state["status"] = "capacity"
                    save()
                    raise CloudCapacity("云端名额已满")
                state.update(
                    status="rejected"
                    if str(state["code"]) not in ("0", "None")
                    else "submitting",
                    message=str(result.get("msg", "")).replace(key, "[hidden]")[:400],
                )
                save()
                raise ValueError(f"云端未返回任务号，{state.get('code')} {state.get('message')}")
            state["status"] = "submitted"
            save()
        if (
            state["status"] == "downloaded"
            and file_sha256(out / "result.mp4") == state["sha256"]
        ):
            return
        print("云端任务", state["task"], flush=True)
        pending = out / "result.partial"
        submitted_at = receipt.stat().st_mtime if state.get("status") == "submitted" else None
        result = fetch_output(hub, state["task"], pending, "214", file_type="mp4", submitted_at=submitted_at, stop=stop)
        code = str(result.get("code"))
        if code != "0":
            state.update(
                status="cloud_failed" if code == "805" else "query_error", code=code
            )
            reason = result.get("data") or {}
            if isinstance(reason, dict):
                detail = reason.get("failedReason", {})
                if isinstance(detail, str):
                    try:
                        detail = json.loads(detail)
                    except ValueError:
                        detail = {"exception_message": detail}
                if isinstance(detail, dict):
                    state["error"] = {
                        k: str(detail[k]).replace(key, "[hidden]")[:500]
                        for k in (
                            "node_id",
                            "node_name",
                            "exception_type",
                            "exception_message",
                        )
                        if k in detail
                    }
            save()
            raise ValueError(f"查询返回 {code} {state.get('error','')}")
        info = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,width,height",
                    "-of",
                    "json",
                    str(pending),
                ]
            )
        )
        if float(info["format"].get("duration") or 0) <= 0 or not any(s["codec_type"] == "video" for s in info["streams"]):
            raise ValueError("缺少视频轨")
        dest = out / "result.mp4"
        pending.replace(dest)
        state.update(status="downloaded", media=info, sha256=file_sha256(dest))
        save()
        print("已取回", dest, info, flush=True)
        return


def prepare(data, delivery, segment, dependencies, accepted, out, changes=()):
    """Replace a single numbered image in place, pinning the exact reviewed source."""
    from .videoreview import state_frame, pick_source_time

    seg, assets, inputs = load_segment(data, delivery, segment)
    receipt = out / "trial.json"
    saved = json.loads(receipt.read_bytes()) if receipt.is_file() else {}
    images = [r for r in seg["refs"] if assets[r["assetId"]]["kind"] == "image"]
    original_count = len(images)
    bindings = []
    skipped = []
    for dep in sorted(dependencies, key=lambda d: d["slot"]):
        src = accepted[dep["source"]]
        media = src / "result.mp4"
        report = json.loads((src / "expert/visual-review.json").read_bytes())
        result = report["visualReview"]
        if report["evidence"]["sha256"] != file_sha256(media):
            raise ValueError("承接来源与采用视频不一致")
        if result["usableEnd"] <= result["usableStart"]:
            skipped.append(dict(source=dep["source"], slot=dep["slot"], note=dep["note"],
                                reason="来源已出片，可用区间内没有可取的帧"))
            continue
        seconds = dep["seconds"]
        if seconds is None:
            seconds = pick_source_time(data, delivery, src, report, dep["note"],
                                       f"专家正在为图 {dep['slot']} 从 {dep['source']} 选承接帧")
            if seconds is None:
                # A planned reference is provisional until a visible state is found.
                skipped.append(dict(source=dep["source"], slot=dep["slot"], note=dep["note"],
                                    reason="来源原片未辨清所需状态，沿用本段采用稿"))
                continue
        if not result["usableStart"] <= seconds <= result["usableEnd"]:
            raise ValueError("来源帧超出本段审定的可用区间")
        step(f"工作台正在取 {dep['source']} 第 {seconds:g} 秒的承接帧")
        frame = state_frame(media, src, seconds, result["usableStart"], result["usableEnd"])
        if frame is None:
            skipped.append(dict(source=dep["source"], slot=dep["slot"], note=dep["note"],
                                reason="采用区间内没有可提取的原帧"))
            continue
        slot = dep["slot"]
        if slot > original_count:
            slot -= sum(s["slot"] > original_count for s in skipped)
        if slot == len(images) + 1 and slot <= 9:
            # An earlier optional append may have no usable frame. Number only actual images.
            ref = {"label": f"图片{slot}"}
            seg["refs"].append(ref)
            images.append(ref)
            old = {"name": ""}
        elif 1 <= slot <= len(images):
            ref = images[slot - 1]
            old = assets.pop(ref["assetId"])
            if old.get("fixedRole"):
                raise ValueError("角色身份参考必须保留")
        else:
            raise ValueError("请替换已有图或追加到下一个可用图位")
        raw = (src / frame["file"]).read_bytes()
        aid = frame["imageHash"]
        asset = dict(
            id=aid,
            name=frame["file"],
            path="states/" + aid + ".png",
            kind="image",
            sha256=aid,
        )
        assets[aid] = asset
        ref["assetId"] = aid
        inputs[asset["path"]] = raw
        note = dep["note"].strip()
        bindings.append(
            dict(
                source=dep["source"],
                slot=slot,
                **({"plannedSlot": dep["slot"]} if slot != dep["slot"] else {}),
                replaced=old["name"],
                sourceHash=frame["sourceHash"],
                imageHash=aid,
                seconds=frame["seconds"],
                note=note,
            )
        )
    if saved.get("task") or saved.get("status") in ("submitting","rejected"):
        # Polling an already submitted job must use its actual recorded inputs.
        seg["prompt"] = next(n["fieldValue"] for n in saved["nodeInfoList"] if n["nodeId"]=="263" and n["fieldName"]=="text")
    elif changes or bindings:
        from .h3rework import compile_revision
        seg["prompt"] = compile_revision(data,delivery,seg["prompt"],changes,bindings,out.parent)
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(
        out / "bindings.json", canonical(dict(bindings=bindings, skipped=skipped, changes=list(changes)))
    )
    return (seg, assets, inputs), bindings
