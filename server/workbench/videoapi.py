"""Director-facing video controls on the existing authenticated local API."""
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal

from .errors import Rejected, NotFound
from .videoqueue import safe_id
from . import videoplan


class Binding(BaseModel):
    source: str
    slot: int = Field(ge=1, le=9)
    seconds: float | None = Field(default=None, ge=0)
    note: str


class Segment(BaseModel):
    segment: str
    automatic: bool = False
    dependencies: list[Binding] = Field(default_factory=list)


class Workflow(BaseModel):
    workflow: str


class Account(BaseModel):
    name: str
    site: str
    instance: str = "plus"
    capacity: int = Field(ge=1)
    key: str = ""
    pool: str = ""
    pool_capacity: int | None = None


def router(queue, external, wb=None):
    api = APIRouter(prefix="/api/video")

    def call(fn, *args, **kwargs):
        if external:
            raise Rejected("请从主工作台打开视频生成")
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise Rejected(str(exc)) from exc

    @api.get("/skills/finecut.zip")
    def finecut_skill():
        from .skillarchive import archive
        path = call(archive, queue.data.parent/"output"/"技能包")
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @api.get("/workflows")
    def workflows():
        return call(queue.workflows)

    @api.put("/workflows/{kind}")
    def save_workflow(kind: Literal["h3", "suno"], body: Workflow):
        return call(queue.set_workflow, kind, body.workflow)

    @api.get("/accounts")
    def accounts():
        return call(queue.accounts)

    @api.put("/accounts")
    def save_account(body: Account):
        call(queue.account, **body.model_dump())
        return {"ok": True, "savedKeyToConfig": bool(body.key)}

    @api.get("/{delivery}")
    def status(delivery: str):
        from .db import connect
        view=call(queue.view, delivery)
        if wb and not view["music"]:
            with connect(wb.db_path) as db:
                row=db.execute("SELECT f.state,f.error FROM film_start f JOIN delivery d ON d.episode_id=f.episode_id "
                               "WHERE d.id=? AND d.scope='full' AND d.state='deliverable' "
                               "AND json_extract(d.versions,'$.publish')=f.publish AND (f.delivery=d.id OR (f.delivery IS NULL "
                               "AND d.id=(SELECT id FROM delivery WHERE episode_id=f.episode_id AND scope='full' AND state='deliverable' "
                               "AND json_extract(versions,'$.publish')=f.publish ORDER BY rowid DESC LIMIT 1)))",(delivery,)).fetchone()
            view["start"]=dict(row) if row else None
        return view

    @api.get("/{delivery}/references")
    def refs(delivery: str):
        slots, _ = call(videoplan.material, queue.data, delivery)
        return slots

    @api.post("/{delivery}/plan")
    def suggest(delivery: str):
        return call(
            videoplan.plan, queue.data, delivery, queue.root / call(safe_id, delivery)
        )

    @api.post("/{delivery}/queue")
    def submit(delivery: str, body: list[Segment]):
        call(queue.enqueue_batch, delivery, [s.model_dump() for s in body])
        return call(queue.view, delivery)

    @api.post("/{delivery}/production")
    def production(delivery: str, body: list[Segment]):
        call(
            queue.enqueue_batch,
            delivery,
            [s.model_dump() for s in body],
            whole_episode=True,
        )
        return call(queue.view, delivery)

    @api.get("/{delivery}/bgm")
    def bgm(delivery: str):
        info = call(queue.view, delivery)["music"]
        if not info or not info["audio"]:
            raise NotFound("本集配乐尚未返回")
        return FileResponse(queue.data / info["audio"])

    @api.post("/{delivery}/bgm/retry")
    def retry_music(delivery: str):
        call(safe_id, delivery)
        with queue.db() as db:
            row = db.execute(
                "SELECT * FROM soundtrack WHERE delivery=?", (delivery,)
            ).fetchone()
            if row and row["state"] == "精剪交接待恢复":
                db.execute("UPDATE media_retry SET until=0 WHERE delivery=? AND segment='' AND round=0", (delivery,))
                db.execute(
                    "UPDATE soundtrack SET state='已就绪',error='' WHERE delivery=?",
                    (delivery,),
                )
                queue.wake.set()
                return {"restored": True}
            if not row or row["state"] not in ("待核对", "需处理", "云端失败"):
                raise Rejected("当前没有需要恢复的配乐任务")
            if row["attempted"] and not row["task"]:
                raise Rejected("上次提交结果未明，请先核对云端原任务")
            if row["state"] == "云端失败":
                raise Rejected("本集配乐已提交过，云端失败后停止生成")
            db.execute(
                "UPDATE soundtrack SET state=CASE WHEN task IS NULL THEN '排队中' ELSE '生成中' END,error='' WHERE delivery=?",
                (delivery,),
            )
        queue.wake.set()
        return call(queue.view, delivery)

    @api.post("/{delivery}/continue")
    def continue_episode(delivery: str):
        from .filmauto import resume_delivery
        return call(resume_delivery, wb, delivery)

    @api.get("/{delivery}/final")
    def movie(delivery: str):
        view=call(queue.view, call(safe_id, delivery))
        if not view["cut"].get("published"):
            raise NotFound("尚无可播放的成片")
        return FileResponse((queue.data/view["music"]["handoff"]).with_name("final.mp4"),media_type="video/mp4")

    @api.get("/{delivery}/finecut")
    def finecut(delivery: str):
        import json

        info = call(queue.view, delivery)["music"]
        if not info or not info["handoff"]:
            raise NotFound("精剪正在等待采用视频与配乐")
        return json.loads((queue.data / info["handoff"]).read_bytes())

    @api.post("/{delivery}/finecut/undo")
    def undo_finecut(delivery: str):
        from . import filmedit
        return call(filmedit.undo_last_edit, wb, queue, call(safe_id, delivery))

    @api.post("/{delivery}/pause/{value}")
    def pause(delivery: str, value: bool):
        call(queue.pause, delivery, value)
        return status(delivery)

    @api.post("/{delivery}/{segment}/retry")
    def retry(delivery: str, segment: str):
        call(queue.retry, delivery, segment)
        return call(queue.view, delivery)

    @api.post("/{delivery}/{segment}/adopt/{round_no}")
    def adopt(delivery: str, segment: str, round_no: int):
        call(queue.adopt_round, delivery, segment, round_no)
        return call(queue.view, delivery)

    @api.get("/{delivery}/{segment}/reference/{slot}")
    def frame(delivery: str, segment: str, slot: int):
        return FileResponse(
            call(queue.reference_frame, delivery, segment, slot), media_type="image/png"
        )

    @api.get("/{delivery}/{segment}/video/{version}")
    def video(delivery: str, segment: str, version: str):
        folder = call(queue.media_folder, delivery, segment, version)
        if not (folder / "result.mp4").is_file():
            raise NotFound("视频尚未返回")
        return FileResponse(folder / "result.mp4", media_type="video/mp4")

    return api
