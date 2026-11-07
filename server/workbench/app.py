"""FastAPI local service. Binds to loopback; writes require the session token plus a local Host/Origin."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import inspect
import json

from .executors import AntigravityExecutor, ClaudeExecutor, CodexExecutor
from .images import OpenAIImageMCPProvider
from .methodpack import REPO, list_configs
from .service import Conflict, NotFound, Rejected, Workbench

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class Worker:
    """Runs queued runs in background threads (no leases in phase 1, by user decision)."""

    def __init__(self, wb: Workbench, threads=2, inline=False):
        self.wb, self.inline = wb, inline
        self.pool = None if inline else ThreadPoolExecutor(max_workers=threads)

    def submit(self, run_id):
        if self.inline:
            self._drain(run_id)
        else:
            self.pool.submit(self._drain, run_id)

    def _drain(self, run_id):
        queue = [run_id]
        while queue:
            queue += self.wb.execute_run(queue.pop(0))


def default_executors():
    if os.environ.get("ADW_FAKE") == "1":   # UI walkthrough without spending executor quota
        from .executors import FakeExecutor
        return {"codex": FakeExecutor("codex"), "claude": FakeExecutor("claude"),
                "antigravity": FakeExecutor("antigravity", isolation="unverified", reads_images=False)}
    return {"codex": CodexExecutor(os.environ.get("ADW_CODEX_PATH")),
            "claude": ClaudeExecutor(os.environ.get("ADW_CLAUDE_PATH")),
            "antigravity": AntigravityExecutor(os.environ.get("ADW_AGY_PATH"))}


def default_image_providers():
    if os.environ.get("ADW_FAKE") == "1":
        from .images import FakeImageProvider
        return {"fake-image": FakeImageProvider("fake-image"),
                "fake-image-unpriced": FakeImageProvider("fake-image-unpriced", price_cents=None)}
    return {"openai-image-mcp": OpenAIImageMCPProvider()}


COMMANDS = {
    "create_project", "create_episode", "record_leftovers", "start_episode", "create_task", "post_message", "retry_run",
    "save_manual", "submit_review", "waive", "adopt", "answer_clarification", "abandon_task", "stop_run", "open_stage", "save_canvas", "combine", "split_segment",
    "resolve_impact", "create_pack", "edit_cuts", "add_beat", "bookmark", "save_pack", "adapt_from_pack",
    "add_space_item", "confirm_space_item", "set_cast", "reorder_refs", "set_sound", "submit_reuse", "review_library",
    "copy_to_episode", "create_batch", "authorize_batch", "cancel_batch", "reconcile_request", "resubmit_request",
    "mark_image", "redraw_cells", "adopt_image", "start_prompt_review", "waive_segment_issue", "set_publish_settings",
    "deliver", "package", "start_closure", "decide_items", "confirm_closure", "propose_setting_change",
    "choose_episode_settings", "update_preset", "episode_spec_change", "cite", "add_topic", "drop_topic",
    "create_group", "continue_in_group", "authorize_expert", "set_task_executor", "adopt_pack", "backup", "restore",
}
QUERIES = {
    "list_projects", "project_view", "episode_view", "kickoff_preview", "read_import", "compare", "continue_check",
    "plan_preview", "adoption_preview", "impacts", "ref_frames", "episode_bookmarks", "pack_view", "trace_shot", "blocked_segments", "cast_matrix", "episode_assets",
    "asset_version", "library", "image_providers", "batch_view", "episode_batches", "compare_cells", "image_references",
    "prompt_review_status", "publish_settings", "compose_publish", "delivery_problems", "delivery_view", "verify_delivery",
    "closure_view", "similar", "topics", "group_view", "model_catalog", "validate_selection", "regress_pack",
    "storage_status", "project_todos", "project_overview", "adoption_preview", "expert_view", "canvas_view", "ref_questions", "task_records", "run_manifest", "probe",
}
ASYNC = {"execute_batch", "expert_run", "ask_cut"}


def create_app(wb: Workbench | None = None, inline_worker=False, token: str | None = None) -> FastAPI:
    wb = wb or Workbench(Path(os.environ.get("ADW_DATA_DIR", REPO / "data")), default_executors(),
                         image_providers=default_image_providers())
    if isinstance(wb.executors.get("claude"), ClaudeExecutor) and (path := wb.get_setting("claude_path")):
        wb.executors["claude"].configured = path
    worker = Worker(wb, inline=inline_worker)
    token = token or secrets.token_urlsafe(24)
    app = FastAPI(title="AI Director Workbench")
    app.state.wb, app.state.worker, app.state.token = wb, worker, token
    for run_id in wb.pending_runs():   # resume queued work after restart
        worker.submit(run_id)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS and host != "testserver":
            return JSONResponse({"error": "host not allowed"}, 403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin.split("://")[-1].rsplit(":", 1)[0] not in LOCAL_HOSTS:
                return JSONResponse({"error": "origin not allowed"}, 403)
            if not secrets.compare_digest(request.headers.get("x-adw-token", ""), token):
                return JSONResponse({"error": "missing session token"}, 403)
        return await call_next(request)

    @app.exception_handler(NotFound)
    @app.exception_handler(Rejected)
    @app.exception_handler(Conflict)
    async def handled(request, exc):
        body = {"error": str(exc)}
        if isinstance(exc, Conflict):
            body["current"] = exc.current
        if getattr(exc, "detail", None) is not None:
            body["detail"] = exc.detail
        return JSONResponse(body, exc.status)

    def queued(result):
        for key in ("runId",):
            if result.get(key):
                worker.submit(result[key])
        return result

    @app.get("/api/session")
    def session():
        return {"token": token}

    @app.get("/api/configs")
    def configs():
        return list_configs()

    @app.get("/api/executors")
    def executors():
        return wb.probe()

    @app.put("/api/settings/claude_path")
    def set_claude_path(body: dict = Body(...)):
        path = body.get("path") or None
        if path and not os.access(path, os.X_OK):
            raise Rejected("路径不可执行")
        wb.put_setting("claude_path", path)
        if isinstance(wb.executors.get("claude"), ClaudeExecutor):
            wb.executors["claude"].configured = path
        return {"path": path}

    @app.get("/api/projects")
    def projects():
        return wb.list_projects()

    @app.post("/api/projects")
    def create_project(body: dict = Body(...)):
        return wb.create_project(body["configId"], body.get("name"), body.get("summary"), body.get("commandId"))

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        return wb.project_view(pid)

    @app.post("/api/projects/{pid}/episodes")
    def create_episode(pid: str, body: dict = Body(...)):
        return wb.create_episode(pid, body["displayCode"], body.get("title", ""), body.get("leftoverFacts", []),
                                 body.get("commandId"))

    @app.put("/api/episodes/{eid}/leftovers")
    def leftovers(eid: str, body: dict = Body(...)):
        return wb.record_leftovers(eid, body["facts"], body["expectedRevision"])

    @app.post("/api/projects/{pid}/imports")
    async def import_file(pid: str, file: UploadFile = File(...), purpose: str = Form(...),
                          episodeId: str | None = Form(None), commandId: str | None = Form(None)):
        data = await file.read()
        return wb.import_file(pid, file.filename, data, purpose, episodeId or None, "upload", commandId)

    @app.get("/api/imports/{iid}")
    def read_import(iid: str):
        return wb.read_import(iid)

    @app.get("/api/episodes/{eid}/kickoff-preview")
    def preview(eid: str, relation: str = "independent", predecessors: str = ""):
        return wb.kickoff_preview(eid, relation, [p for p in predecessors.split(",") if p])

    @app.post("/api/episodes/{eid}/start")
    def start(eid: str, body: dict = Body(...)):
        return wb.start_episode(eid, body["sourceType"], body["executorPlan"], body.get("sourceImportId"),
                                body.get("ideaText"), body.get("topicId"), body.get("goal", ""),
                                body.get("relation", "independent"), body.get("predecessorIds", []),
                                body.get("specOverrides"), body.get("commandId"))

    @app.get("/api/episodes/{eid}")
    def episode(eid: str):
        return wb.episode_view(eid)

    @app.post("/api/episodes/{eid}/tasks")
    def create_task(eid: str, body: dict = Body(...)):
        return queued(wb.create_task(eid, body["stage"], body["goal"], body.get("autoRevise", False),
                                     body.get("executor"), body.get("commandId")))

    @app.post("/api/tasks/{tid}/messages")
    def message(tid: str, body: dict = Body(...)):
        return queued(wb.post_message(tid, body["text"], body["commandId"], body.get("selection"),
                                      body.get("baseVersionId")))

    @app.post("/api/runs/{rid}/retry")
    def retry(rid: str, body: dict = Body(default={})):
        return queued(wb.retry_run(rid, body.get("provider"), body.get("model"), body.get("effort"),
                                   body.get("commandId")))

    @app.get("/api/runs/{rid}/manifest")
    def manifest(rid: str):
        return wb.run_manifest(rid)

    @app.post("/api/artifacts/{aid}/versions")
    def save(aid: str, body: dict = Body(...)):
        return wb.save_manual(aid, body.get("baseVersionId"), body["content"], body["commandId"],
                              body.get("origin", "manual"), body.get("note", ""))

    @app.post("/api/versions/{vid}/reviews")
    def review(vid: str, body: dict = Body(...)):
        return queued(wb.submit_review(vid, body["commandId"], body.get("taskId"), body.get("provider"),
                                       body.get("model"), body.get("effort")))

    @app.post("/api/issues/{iid}/waivers")
    def waive(iid: str, body: dict = Body(...)):
        return wb.waive(iid, body["versionId"], body.get("reason", ""), body["commandId"])

    @app.post("/api/artifacts/{aid}/adoptions")
    def adopt(aid: str, body: dict = Body(...)):
        return wb.adopt(aid, body["versionId"], body.get("expectedAdoptedVersionId"), body["commandId"],
                        body.get("andContinue", False))

    def call(name, args):
        fn = getattr(wb, name)
        params = inspect.signature(fn).parameters
        if "command_id" in params and not args.get("command_id"):
            args = dict(args, command_id=secrets.token_hex(12))
        if "command_id" not in params:
            args = {k: v for k, v in args.items() if k != "command_id"}   # the UI always sends one
        result = fn(**args)
        if isinstance(result, dict):
            for key in ("runId",):
                if result.get(key):
                    worker.submit(result[key])
        return result

    @app.post("/api/cmd/{name}")
    def command(name: str, body: dict = Body(default={})):
        if name in ASYNC:
            if hasattr(wb, name + "_precheck"):
                getattr(wb, name + "_precheck")(**body)       # refusals reach the UI before the run is queued
            if worker.inline:
                return getattr(wb, name)(**body)
            worker.pool.submit(getattr(wb, name), **body)
            return {"accepted": True}
        if name not in COMMANDS:
            raise NotFound("未知命令")
        return call(name, body)

    @app.get("/api/q/{name}")
    def query(name: str, args: str = "{}"):
        if name not in QUERIES:
            raise NotFound("未知查询")
        return getattr(wb, name)(**json.loads(args))

    @app.post("/api/upload/{kind}")
    async def upload_any(kind: str, file: UploadFile = File(...), meta: str = Form("{}")):
        data, m = await file.read(), json.loads(meta)
        if kind == "reference":
            return wb.import_reference(m["projectId"], file.filename, data, m.get("commandId"))
        if kind == "asset":
            return wb.register_asset(m["projectId"], m.get("name") or file.filename, data, m.get("category", "scene"),
                                     episode_id=m.get("episodeId"), roles=m.get("roles", []), grid=m.get("grid", False))
        if kind == "knowledge":
            return wb.import_knowledge(m["projectId"], [dict(name=file.filename, data=data, kind=m.get("kind"))],
                                       m.get("commandId"))
        if kind == "reconcile":
            return wb.reconcile_request(m["requestId"], "completed", data)
        raise NotFound("未知上传")

    @app.get("/api/blob/{digest}")
    def blob(digest: str):
        from fastapi.responses import Response
        return Response(wb.store.read_blob(digest), media_type="image/png")

    @app.get("/api/delivery-file/{delivery_id}/{path:path}")
    def delivery_file(delivery_id: str, path: str):
        d = wb.delivery_view(delivery_id)
        root = (wb.store.root / d["relpath"]).resolve()
        target = (root / path).resolve()
        if root not in target.parents or path not in d["files"]:
            raise NotFound("文件不在交付清单内")
        return FileResponse(target)

    dist = REPO / "web" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = (dist / path).resolve()
            if path and dist.resolve() in f.parents and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


def main():
    import uvicorn
    port = int(os.environ.get("ADW_PORT", "8765"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
