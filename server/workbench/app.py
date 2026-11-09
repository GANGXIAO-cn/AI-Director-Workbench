"""FastAPI local service. Binds to loopback; writes require the session token plus a local Host/Origin."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import os
from base64 import b64encode
from hashlib import sha256
from pathlib import Path
import secrets

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import inspect
import json

from .executors import AntigravityExecutor, ClaudeExecutor, CodexExecutor
from .executors.cli import CliExecutor
from .images import OpenAIImageMCPProvider
from .methodpack import REPO, list_configs
from .service import EDITOR_GUARD, Conflict, NotFound, Rejected, Workbench

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def check_call_args(signature, args):
    if not isinstance(args, dict):
        raise Rejected("调用参数须为 JSON 对象")
    try:
        signature.bind(**args)
    except TypeError as exc:
        raise Rejected(f"调用参数无效：{exc}") from exc


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
    "revise_baseline", "follow_baseline",
    "start_topic_research", "accept_research_topic",
    "add_reply_to_canvas","send_free_chat","stop_free_chat","retry_free_chat",
    "set_quota_retry_policy", "save_provider", "read_provider_models", "label_provider_model",
    "start_story_comparison", "preview_acting",
    "add_script_document", "refresh_script_index", "restore_step_revision",
    "use_fixed_role", "import_historical_episode", "episode_contact_sheet", "save_contact_options", "export_episode_contact_sheet", "historical_contact_sheet", "export_historical_contact_sheet", "save_product_placement", "set_project_sections", "use_project_product", "mount_resource", "restore_version", "set_stage_methods", "set_custom_methods", "save_palettes", "add_palette", "set_episode_palettes", "save_step_config",
    "set_cooperation_mode", "set_stage_edit", "set_optional_stage", "export_project_folder", "create_project", "rename_project", "set_recent_visibility", "visit_project", "create_episode", "record_leftovers", "start_episode", "create_task", "post_message", "retry_run",
    "save_manual", "submit_review", "waive", "adopt", "answer_clarification", "abandon_task", "stop_run", "open_stage", "save_canvas", "combine", "split_segment",
    "resolve_impact", "create_pack", "edit_cuts", "add_beat", "bookmark", "save_pack", "adapt_from_pack",
    "add_space_item", "confirm_space_item", "set_cast", "set_role_baseline", "save_shot_observation", "reorder_refs", "edit_prompt_reference", "set_sound", "submit_reuse", "review_library",
    "copy_to_episode", "create_batch", "authorize_batch", "cancel_batch", "reconcile_request", "resubmit_request",
    "set_grid_cell", "mark_image", "redraw_cells", "adopt_image", "start_prompt_review", "start_prompt_review_batch", "waive_segment_issue", "set_publish_settings",
    "deliver", "package", "package_preview", "start_closure", "decide_items", "confirm_closure", "propose_setting_change",
    "choose_episode_settings", "update_preset", "episode_spec_change", "cite", "take_source_topic", "add_topic", "drop_topic",
    "create_group", "continue_in_group", "authorize_expert", "pause_expert", "manual_expert", "set_task_executor", "adopt_pack", "backup", "restore",
}
QUERIES = {
    "free_chat","free_chat_options","topic_researches",
    "episode_contact_options", "space_locks", "api_providers",
    "step_history", "segment_palette_trace", "rhythm_review", "shot_reference_view",
    "project_role_assets", "historical_episodes", "historical_receipt", "historical_review_inputs", "product_placements", "project_resources", "project_section", "episode_project_sections", "available_configs", "groups", "project_knowledge",
    "resource_mounts", "browse_scripts", "search_script_bodies", "script_excerpt", "browse_palettes", "palette_detail", "project_palettes", "episode_palettes", "step_config", "step_form",
    "project_presets", "episode_owner", "list_projects", "project_view", "episode_view", "kickoff_preview", "read_import", "compare", "continue_check",
    "grid_bindings", "draft_prompts", "plan_preview", "adoption_preview", "impacts", "ref_frames", "episode_bookmarks", "pack_view", "trace_shot", "blocked_segments", "cast_matrix", "episode_assets",
    "asset_version", "library", "image_providers", "batch_view", "episode_batches", "compare_cells", "image_references",
    "prompt_signals", "prompt_review_status", "publish_settings", "compose_publish", "delivery_problems", "delivery_view", "verify_delivery",
    "enhancement_catalog", "closure_view", "similar", "project_baselines", "source_styles", "source_topics", "topics", "group_view", "model_catalog", "validate_selection", "regress_pack", "pack_versions", "compare_packs", "stage_methods", "custom_methods", "custom_method_text", "project_stage_methods", "script_plan_budget", "acting_review",
    "storage_status", "packager_status", "project_todos", "project_overview", "adoption_preview", "expert_view", "expert_history", "expert_preview", "canvas_view", "ref_questions", "activity_log", "stage_checklist", "task_records", "run_manifest", "run_methods", "version_sources", "probe",
}
ASYNC = {"execute_batch", "expert_run", "ask_cut"}


def create_app(wb: Workbench | None = None, inline_worker=False, token: str | None = None, *, external=False) -> FastAPI:
    wb = wb or Workbench(Path(os.environ.get("ADW_DATA_DIR", REPO / "data")), default_executors(),
                         image_providers=default_image_providers())
    for name, executor in wb.executors.items():
        if not external and isinstance(executor, CliExecutor) and (path := wb.get_setting(name + "_path")):
            executor.configured = path
    worker = Worker(wb, inline=inline_worker)
    token = token or secrets.token_urlsafe(24)
    from .folderserver import FolderServers
    folders = None if external else FolderServers(wb)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if folders:
                folders.shutdown()
            if worker.pool:
                worker.pool.shutdown(wait=True)

    app = FastAPI(title="AI Director Workbench",lifespan=lifespan)
    app.state.wb, app.state.worker, app.state.token = wb, worker, token
    for run_id in ([] if external else wb.pending_runs()):   # resume queued work after restart
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
        if external and request.url.path.startswith("/api/") and not wb.available():
            return JSONResponse({"error":"项目目录离线或已移位，请在主工作台关闭目录并重新定位"},503)
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

    @app.get("/api/project-product/{project_id}/{code}")
    def project_product(project_id: str, code: str, episode_id: str | None = None):
        return Response(wb.project_product_image(project_id, code, episode_id), media_type="image/webp")

    @app.get("/api/palette-image/{card_id}")
    def palette_image(card_id: str, filename: str):
        return Response(wb.palette_image(card_id, filename), media_type="image/png" if filename.endswith(".png") else "image/jpeg")

    @app.get("/api/session")
    def session():
        return {"token": token, "externalProject": external}

    @app.get("/api/configs")
    def configs():
        return list_configs()

    @app.get("/api/executors")
    def executors():
        return wb.probe()

    @app.put("/api/settings/executors/{provider}")
    def configure_executor(provider: str, body: dict = Body(...)):
        executor = wb.executors.get(provider)
        if not isinstance(executor, CliExecutor):
            raise Rejected("仅本机 CLI 支持设置路径")
        path = body.get("path") or None
        if path:
            if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file() or not os.access(path, os.X_OK):
                raise Rejected("请填写本机可执行文件的绝对路径")
            if Path(path).name != {"codex":"codex", "claude":"claude", "antigravity":"agy"}[provider]:
                raise Rejected("所选文件名与执行器不匹配")
        wb.put_setting(provider + "_path", path)
        executor.configured = path
        return {"ok": True, "path": path}

    @app.put("/api/settings/claude_path")
    def set_claude_path(body: dict = Body(...)):
        return configure_executor("claude", body)

    @app.put("/api/settings/providers/{provider_id}/key")
    def set_provider_key(provider_id: str, body: dict = Body(...)):
        return wb.set_provider_key(provider_id, body.get("revision"), body.get("key"))

    @app.post("/api/project-folder/open")
    def open_folder(body:dict = Body(...)):
        if folders is None:
            raise Rejected("请在主工作台打开其他项目目录")
        try:
            return folders.open(body.get("folder"),body.get("expected"))
        except (OSError,ValueError) as exc:
            raise Rejected(f"无法读取项目目录：{exc}") from exc

    @app.get("/api/project-folder")
    def list_folders():
        return dict(external=external, opened=folders.entries() if folders else [], recent=folders.recent() if folders else [])

    @app.post("/api/project-folder/close")
    def close_folder(body:dict=Body(...)):
        if not folders or not isinstance(body.get("folder"),str):
            raise Rejected("请在主工作台关闭独立目录")
        return folders.close(body["folder"])

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
                          episodeId: str | None = Form(None), commandId: str | None = Form(None), forStory: bool = Form(False)):
        data = await file.read()
        return wb.import_file(pid, file.filename, data, purpose, episodeId or None, "episode_story" if forStory else "upload", commandId)

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
                                body.get("specOverrides"), body.get("commandId"), preset_name=body.get("presetName","default"),
                                preset_revision=body.get("presetRevision"), use_story_skill=body.get("useStorySkill", False),
                                cooperation_mode=body.get("cooperationMode"))

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
        signature = inspect.signature(fn)
        params = signature.parameters
        if "command_id" in params and not args.get("command_id"):
            args = dict(args, command_id=secrets.token_hex(12))
        if "command_id" not in params:
            args = {k: v for k, v in args.items() if k != "command_id"}   # the UI always sends one
        guard = args.pop("editor_guard", None)
        check_call_args(signature, args)
        token = EDITOR_GUARD.set(guard)
        try:
            result = fn(**args)
        finally:
            EDITOR_GUARD.reset(token)
        if isinstance(result, dict):
            if result.get("runId"):
                worker.submit(result["runId"])
            if name in ("send_free_chat","retry_free_chat","start_topic_research"):
                args = (result["turnId"],result["attemptId"])
                if worker.inline:
                    wb.execute_free_chat(*args)
                else:
                    worker.pool.submit(wb.execute_free_chat,*args)
            if name in ("start_story_comparison","start_prompt_review_batch"):
                for rid in result["runIds"]:
                    worker.submit(rid)
        return result

    @app.post("/api/with-files/{name}")
    async def with_files(name: str, args: str = Form(...), files: list[UploadFile] = File(...)):
        from .attachments import LIMIT
        from starlette.concurrency import run_in_threadpool
        if name not in ("start_episode", "create_task", "post_message"):
            raise NotFound("该操作不支持附件")
        try:
            body = json.loads(args)
        except ValueError as exc:
            raise Rejected("附件请求参数无效") from exc
        if not isinstance(body, dict) or "files" in body or len(files) > 6:
            raise Rejected("请核对附件参数，每次最多 6 份")
        remaining = LIMIT
        body["files"] = []
        for file in files:
            raw = await file.read(remaining+1)
            remaining -= len(raw)
            if remaining < 0:
                raise Rejected("本次附件合计超过 30 MiB")
            body["files"].append((file.filename or "图片", raw))
        return await run_in_threadpool(call, name, body)

    @app.post("/api/cmd/{name}")
    def command(name: str, body: dict = Body(default={})):
        if name in ("start_episode","create_task","post_message") and "files" in body:
            raise Rejected("含附件的请求须使用文件上传入口")
        if name == "expert_run":
            check_call_args(inspect.signature(wb.claim_expert), body)
            claimed = wb.claim_expert(**body)
            if claimed:
                if worker.inline:
                    return wb.advance_expert(body["auth_id"])
                worker.pool.submit(wb.advance_expert, body["auth_id"])
            return wb.expert_view(body["auth_id"])
        if name in ASYNC:
            check_call_args(inspect.signature(getattr(wb, name)), body)
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
        try:
            supplied = json.loads(args)
        except ValueError as exc:
            raise Rejected("查询参数须为有效 JSON") from exc
        fn = getattr(wb, name)
        check_call_args(inspect.signature(fn), supplied)
        return fn(**supplied)

    @app.post("/api/upload/{kind}")
    async def upload_any(kind: str, file: UploadFile = File(...), meta: str = Form("{}")):
        m = json.loads(meta)
        if kind == "reference-media":
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(wb.upload_reference_media, m.get("episodeId"), file.filename,
                                           file.file, m.get("commandId"))
        if kind == "custom-method":
            from .custommethods import MAX_BYTES
            return wb.import_custom_method(m["projectId"], file.filename, await file.read(MAX_BYTES+1),
                                           m.get("title"), m.get("methodId"), m.get("expectedRevision"))
        data = await file.read()
        if kind == "publish-pack":
            return wb.import_publish_pack(m["projectId"], file.filename, data)
        if kind == "reference":
            return wb.import_reference(m["projectId"], file.filename, data, m.get("commandId"))
        if kind == "library-image":
            return wb.upload_library_image(m["projectId"], file.filename or "图片", data, m.get("category"),
                                           m.get("usage"), m.get("previousEntryId"), m.get("commandId"))
        if kind == "asset":
            return wb.register_asset(m["projectId"], m.get("name") or file.filename, data, m.get("category", "scene"),
                                     episode_id=m.get("episodeId"), roles=m.get("roles", []), grid=m.get("grid", False))
        if kind == "knowledge":
            return wb.import_knowledge(m["projectId"], [dict(name=file.filename, data=data, kind=m.get("kind"))],
                                       m.get("commandId"))
        if kind == "reconcile":
            return wb.reconcile_request(m["requestId"], "completed", data)
        raise NotFound("未知上传")

    @app.get("/api/reference-media/{episode_id}/{version_id}")
    def reference_media(episode_id: str, version_id: str):
        from .h3export import MEDIA_MIME, EXTENSIONS
        asset = wb.asset_version(version_id)
        ext = Path(asset["name"]).suffix.lower()
        if (asset["episode_id"] != episode_id or asset["kind"] not in ("audio", "video")
                or ext not in EXTENSIONS[asset["kind"]]):
            raise NotFound("本集没有该音视频参考")
        return FileResponse(wb.store.blob_path(asset["sha256"]), media_type=MEDIA_MIME[ext],
                            headers={"X-Content-Type-Options":"nosniff"})

    @app.get("/api/story/{version_id}/markdown")
    def story_markdown(version_id: str):
        return Response(wb.story_markdown(version_id), media_type="text/markdown")

    @app.get("/api/blob/{digest}")
    def blob(digest: str):
        from fastapi.responses import Response
        raw = wb.store.read_blob(digest)
        mime = "image/jpeg" if raw.startswith(b"\xff\xd8") else "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "application/octet-stream"
        return Response(raw, media_type=mime)

    @app.get("/api/history-report/{project_id}/{receipt}/{index}/{name:path}")
    def history_report(project_id: str, receipt: str, index: int, name: str):
        raw = wb.historical_report_file(project_id, receipt, index, name)
        headers = {"X-Content-Type-Options":"nosniff", "Referrer-Policy":"no-referrer",
                   "Content-Security-Policy":"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"}
        if name == "preview":
            return Response(raw, media_type="text/html", headers=headers)
        if name in {"report.zip", "report.html"}:
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
            mime = "application/zip" if name == "report.zip" else "application/octet-stream"
        else:
            mime = {".md":"text/plain",".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",
                    ".webp":"image/webp",".gif":"image/gif"}[Path(name).suffix.lower()]
        return Response(raw, media_type=mime, headers=headers)

    delivery_headers = {}
    if external:
        templates = Path(__file__).with_name("delivery_templates")
        hashes = ["'sha256-" + b64encode(sha256((templates/name).read_bytes()).digest()).decode() + "'"
                  for name in ("image-selection.js","prompt-copy.js")]
        delivery_headers = {"X-Content-Type-Options":"nosniff", "Content-Security-Policy":
            "default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; script-src " + " ".join(hashes)}

    @app.get("/api/delivery-file/{delivery_id}/{path:path}")
    def delivery_file(delivery_id: str, path: str):
        d = wb.delivery_view(delivery_id)
        root = (wb.store.root / d["relpath"]).resolve()
        target = (root / path).resolve()
        if root not in target.parents or path not in d["files"]:
            raise NotFound("文件不在交付清单内")
        return FileResponse(target,headers=delivery_headers)

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
