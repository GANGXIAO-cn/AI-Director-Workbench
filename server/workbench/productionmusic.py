"""An episode soundtrack shares RunningHub capacity with its video segments."""
import json
import re
import secrets
import subprocess
import time

import httpx

from .cloudtask import fetch_output, PollInterrupted
from .h3online import RunningHub
from .storage import canonical, file_sha256, write_atomic

from .onlineworkflows import DEFAULTS

WORKFLOW = DEFAULTS["suno"]
SCHEMA = """CREATE TABLE IF NOT EXISTS soundtrack(
 delivery TEXT PRIMARY KEY, brief TEXT NOT NULL, state TEXT NOT NULL, account TEXT,
 task TEXT, attempted INTEGER DEFAULT 0, error TEXT DEFAULT '', audio TEXT, handoff TEXT);"""


def brief(episode):
    wb = episode["workbench"]
    pub = wb["adopted"].get("publish")
    if episode["scope"] != "full" or not pub:
        raise ValueError("整集制作需要一份含已采用发布文案的整集交付")
    fields = {k: v for k, _, _, v in wb["page"]["publish"]["fields"]}
    style = fields.get("suno-style", "").strip()
    if not style:
        cues = wb.get("finecut", {}).get("cues", [])
        style = "Instrumental underscore. " + fields.get(
            "body", fields.get("title", "")
        )
        style += " " + " ".join(str(c["action"]) for c in cues if c.get("action"))
    return dict(
        publish=pub,
        title=fields.get("suno-title") or fields["title"],
        style=style,
        exclude=fields.get("suno-exclude", ""),
        segments=[s["id"] for s in episode["segments"]],
        seed=secrets.randbelow(2**31),
    )


def description(spec):
    """The Suno node accepts up to 400 characters including the exclusions."""
    style = " ".join(spec["style"].split())
    exclude = " ".join(spec.get("exclude", "").split())
    combined = style + ("\nAvoid: " + exclude if exclude else "")
    if len(combined) <= 400:
        return combined
    # Instrumental is already an explicit workflow parameter.
    redundant = {"vocals", "singing", "lyrics", "spoken word"}
    terms = [s.strip() for s in re.split("[,，;；]", exclude)
             if s.strip() and s.strip().lower() not in redundant]
    suffix = ("\nAvoid: " + ", ".join(terms))[:136].rstrip(", ") if terms else ""
    room = 400 - len(suffix)
    if len(style) > room:
        # Keep both the opening groove and closing cue of long briefs.
        split = (room-3)*2//3
        left, right = style[:split], style[-(room-3-split):]
        if " " in left: left = left.rsplit(" ", 1)[0]
        if " " in right: right = right.split(" ", 1)[1]
        style = left.rstrip(",; ") + " … " + right.lstrip(",; ")
    return style + suffix


def nodes(graph, spec):
    expected = {"47": "JjkText", "43": "RH_RhartAudioSunoV55Single", "49": "SaveAudio"}
    if any(graph.get(n, {}).get("class_type") != kind for n, kind in expected.items()):
        raise ValueError("在线工作流与已登记的 Suno 节点不同")
    suno = graph["43"]["inputs"]
    if suno.get("description") != ["47", 0] or graph["49"]["inputs"].get("audio") != [
        "43",
        0,
    ]:
        raise ValueError("Suno 的提示词或音频输出接线已改变")
    style = description(spec)
    return [
        dict(nodeId=node, fieldName=field, fieldValue=value)
        for node, field, value in (
            ("47", "text", style),
            ("43", "title", spec["title"]),
            ("43", "make_instrumental", "true"),
            ("43", "seed", spec["seed"]),
        )
    ]


def generate(queue, job, account):
    delivery = job["delivery"]

    def save(**values):
        with queue.db() as db:
            db.execute(
                "UPDATE soundtrack SET "
                + ", ".join(k + "=?" for k in values)
                + " WHERE delivery=?",
                (*values.values(), delivery),
            )

    try:
        with queue.db() as db:
            row = dict(
                db.execute(
                    "SELECT * FROM soundtrack WHERE delivery=?", (delivery,)
                ).fetchone()
            )
        if not row["task"] and row["attempted"]:
            raise ValueError("上次提交未取得任务号，请核对云端记录")
        with httpx.Client(timeout=120) as client:
            hub = RunningHub(client, account["site"], account["key"])
            task = row["task"]
            new_task = not task
            if new_task:
                spec = json.loads(row["brief"])
                workflow = spec.get("workflow", WORKFLOW)
                params = nodes(hub.graph(workflow), spec)
                queue.check_dispatch(delivery)
                save(attempted=1)
                resp = hub.submit(workflow, params, account["instance"])
                task = (
                    (resp.get("data") or {}).get("taskId")
                    if isinstance(resp.get("data"), dict)
                    else None
                )
                if not task:
                    code = str(resp.get("code"))
                    if code in ("415", "421"):
                        with queue.db() as db:
                            db.execute(
                                "UPDATE soundtrack SET state='排队中',attempted=0 WHERE delivery=?",
                                (delivery,),
                            )
                            db.execute(
                                "INSERT OR REPLACE INTO cooldown SELECT name,? FROM account WHERE name=? OR (pool<>'' AND pool=?)",
                                (time.time() + 30, account["name"], account["pool"]),
                            )
                        return
                    if code not in ("None", "0"):
                        save(attempted=0)
                    raise ValueError("Suno 提交未取得任务号（" + code + "）")
                save(task=str(task))
            folder = queue.root / delivery / "music"
            temp = folder / "soundtrack.partial"
            result = fetch_output(hub, task, temp, "49", submitted_at=time.time() if new_task else None, stop=queue.stop)
            code = str(result.get("code"))
            if code == "805":
                failed = (result.get("data") or {}).get("failedReason",{}) if isinstance(result.get("data"),dict) else {}
                message = str(failed.get("exception_message","") if isinstance(failed,dict) else "").strip()
                if not message:
                    message = str(result.get("msg","") or "").strip()
                error = ("Suno 要求配乐描述最多 400 字符，未生成音乐" if "description length must be <= 400" in message
                         else f"云端确认本次配乐生成失败：{message[:220]}" if message
                         else "云端确认本次配乐生成失败")
                save(state="云端失败", error=error)
                return
            if code != "0":
                raise ValueError("Suno 查询返回 " + code)
            info = json.loads(
                subprocess.check_output(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=codec_type:format=duration,format_name",
                        "-of",
                        "json",
                        str(temp),
                    ]
                )
            )
            fmt = info["format"]
            if (
                not any(s["codec_type"] == "audio" for s in info["streams"])
                or not float(fmt["duration"]) > 0
            ):
                raise ValueError("配乐文件缺少有效音轨")
            ext = next(
                (
                    v
                    for k, v in (
                        ("flac", "flac"),
                        ("mp3", "mp3"),
                        ("wav", "wav"),
                        ("ogg", "ogg"),
                        ("mov", "m4a"),
                        ("aac", "aac"),
                    )
                    if k in fmt["format_name"].split(",")
                ),
                None,
            )
            if not ext:
                raise ValueError("配乐返回了未知音频格式")
            path = folder / ("soundtrack." + ext)
            temp.replace(path)
            save(audio=str(path.relative_to(queue.data)), state="已就绪", error="")
            return
    except PollInterrupted:
        return
    except Exception as exc:
        with queue.db() as db:
            r = db.execute(
                "SELECT attempted,task FROM soundtrack WHERE delivery=?", (delivery,)
            ).fetchone()
            state = "待核对" if r["attempted"] else "需处理"
            if (
                r["task"]
                and db.execute(
                    "INSERT OR IGNORE INTO recovery VALUES(?,?,?,?)",
                    (delivery, "", 0, "配乐取回"),
                ).rowcount
            ):
                state = "生成中"
            db.execute(
                "UPDATE soundtrack SET state=?,error=? WHERE delivery=?",
                (state, str(exc).replace(account["key"], "[hidden]")[:350], delivery),
            )
    finally:
        queue.wake.set()


def advance(queue, db):
    rows = db.execute(
        "SELECT * FROM soundtrack s WHERE state IN ('已就绪','精剪交接待恢复') AND handoff IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM episode_control c WHERE c.delivery=s.delivery AND c.paused=1) AND NOT EXISTS "
        "(SELECT 1 FROM media_retry r WHERE r.delivery=s.delivery AND r.segment='' AND r.until>?)", (time.time(),)
    ).fetchall()
    for row in rows:
        try:
            prepare_handoff(queue, db, row)
        except (OSError, ValueError, KeyError) as exc:
            db.execute("INSERT INTO media_retry(delivery,segment,round,until,attempts) VALUES(?,'',0,?,1) "
                       "ON CONFLICT(delivery,segment,round) DO UPDATE SET until=excluded.until,attempts=attempts+1",
                       (row["delivery"], time.time()+60))
            db.execute(
                "UPDATE soundtrack SET state='精剪交接待恢复',error=? WHERE delivery=?",
                (str(exc)[:350], row["delivery"]),
            )


def prepare_handoff(queue, db, row):
    spec = json.loads(row["brief"])
    jobs = {
        r["segment"]: r["adopted"]
        for r in db.execute(
            "SELECT segment,adopted FROM job WHERE delivery=? AND state IN ('已采用','已出片待选剪')",
            (row["delivery"],),
        )
    }
    if not all(jobs.get(s) for s in spec["segments"]):
        return
    clips = []
    for s in spec["segments"]:
        folder = queue.data / jobs[s]
        video = folder / "result.mp4"
        review = json.loads((folder / "expert/visual-review.json").read_bytes())
        if not video.is_file() or not (queue.data / row["audio"]).is_file():
            raise ValueError("精剪交接所需的采用原片或音乐文件缺失")
        clips.append(
            dict(
                segment=s,
                video=str(video.relative_to(queue.data)),
                sha256=review["evidence"]["sha256"],
                review=review["visualReview"],
            )
        )
    path = queue.root / row["delivery"] / "finecut" / "inputs.json"
    write_atomic(
        path,
        canonical(
            dict(
                delivery=row["delivery"],
                publish=spec["publish"],
                clips=clips,
                bgm=row["audio"],
                bgmSha256=file_sha256(queue.data / row["audio"]),
                bgmAudioReady=True,
                bgmListened=False,
                bgmStatus="音频文件已返回（AI 未试听）",
            )
        ),
    )
    db.execute(
        "UPDATE soundtrack SET handoff=?,state='已就绪',error='' WHERE delivery=?",
        (str(path.relative_to(queue.data)), row["delivery"]),
    )
