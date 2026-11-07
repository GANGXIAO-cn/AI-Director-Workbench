"""FastAPI local service. Binds to loopback; writes require the session token plus a local Host/Origin."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .executors import ClaudeExecutor, CodexExecutor
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
        return {"codex": FakeExecutor("codex"), "claude": FakeExecutor("claude")}
    return {"codex": CodexExecutor(os.environ.get("ADW_CODEX_PATH")),
            "claude": ClaudeExecutor(os.environ.get("ADW_CLAUDE_PATH"))}


def create_app(wb: Workbench | None = None, inline_worker=False, token: str | None = None) -> FastAPI:
    wb = wb or Workbench(Path(os.environ.get("ADW_DATA_DIR", REPO / "data")), default_executors())
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

    dist = REPO / "web" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(dist / "index.html")

    return app


def main():
    import uvicorn
    port = int(os.environ.get("ADW_PORT", "8765"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
