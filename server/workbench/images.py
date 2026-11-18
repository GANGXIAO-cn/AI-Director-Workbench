"""Assets, reuse library and paid image batches.

Paid-batch guard: a batch is authorised against a summary hash; each request is persisted before it
is sent; a request whose outcome is unknown is never re-sent automatically.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import json
from . import imagechoices, timeline
from io import BytesIO
from itertools import zip_longest
import os
import queue
import threading
from pathlib import Path
import struct
import subprocess
import time
import zlib

from PIL import Image

from .palette_inputs import SCOPE, stage_card
from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, loads, row
from .storage import canonical, sha256_bytes, write_atomic


# ---------------------------------------------------------------- image helpers
def png_bytes(width: int, height: int, rgb=(200, 120, 40)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def image_attachment(name, data):
    suffix = ".jpg" if data.startswith(b"\xff\xd8") else ".png"
    return dict(role="image", name=name + suffix, data=data)


def image_size(data: bytes):
    try:
        with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as source:
            source.verify()
        with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as source:
            source.load()
            return source.size
    except (ValueError, OSError, SyntaxError, Image.DecompressionBombError):
        return None


# ---------------------------------------------------------------- providers
TRANSPORT_FAILURE = "出图连接中断或超时，本次未收到图片（服务端执行情况未知）"
INTERRUPTED_IMAGE = '服务重启或连接中断，未取得图片，可重出'

@dataclass
class ImageOutcome:
    state: str                 # succeeded | unknown | failed
    data: bytes | None = None
    provider_request_id: str | None = None
    error: str = ""


class FakeImageProvider:
    """Scripted outcomes for tests: 'ok' | 'timeout' | 'disconnect' | 'duplicate' | 'insufficient'."""

    currency = "USD"

    def __init__(self, name="fake-image", script=None, price_cents=4):
        self.name, self.script, self.price_cents = name, list(script or []), price_cents
        self.sent = 0
        self.verified = "simulated"
        self.capabilities = dict(operations=["generate", "edit"], sizes=["64x64", "96x64", "1024x1024", "864x1536"],
                                 quality=["low", "medium", "high"])
        self.completed = {}
        self.timed_out = set()

    def submit(self, req):
        self.sent += 1
        marker = "【测试：超时】" in req.get("prompt", "") and req["prompt"] not in self.timed_out
        step = self.script.pop(0) if self.script else ("timeout" if marker else "ok")
        if marker:
            self.timed_out.add(req["prompt"])
        w, h = map(int, req["size"].split("x"))
        if step in ("ok", "duplicate"):
            data = png_bytes(w, h, (40 + self.sent * 20 % 200, 120, 200))
            self.completed[req["local_request_id"]] = data
            return ImageOutcome("succeeded", data, f"fake-req-{self.sent}")
        if step in ("timeout", "disconnect"):
            self.completed[req["local_request_id"]] = png_bytes(w, h)   # it may have completed remotely
            return ImageOutcome("unknown", error=step)
        if step == "insufficient":
            return ImageOutcome("failed", error="insufficient_quota")
        raise ValueError(step)

    def inspect(self, local_request_id):
        return None   # no verified remote query; unknown stays unknown until a person checks


class OpenAIImageMCPProvider:
    remote_status = True
    """Local stdio MCP server ~/Documents/自媒体 2/tools/openai-image-mcp (generate_image / edit_image /
    image_job_status). Each call is paid. Not exercised by tests; status is 'pending_verification' in-app."""

    name = "openai-image-mcp"
    currency = "USD"
    price_cents = None
    verified = "pending_verification"
    capabilities = dict(operations=["generate", "edit"], sizes=["1024x1024", "864x1536", "1536x864"],
                        quality=["low", "medium", "high"])

    def __init__(self, root=Path.home() / "Documents/自媒体 2/tools/openai-image-mcp", poll_seconds=10, timeout=600):
        self.root, self.poll, self.timeout = Path(root), poll_seconds, timeout

    @contextmanager
    def _session(self):
        python = self.root / ".venv/bin/python"
        with subprocess.Popen([str(python), "server.py"], cwd=self.root, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as proc:
            replies = queue.Queue()

            def read_replies():
                try:
                    for line in proc.stdout:
                        replies.put(json.loads(line))
                finally:
                    replies.put(None)

            threading.Thread(target=read_replies, daemon=True).start()
            sequence = 0

            def send(message):
                proc.stdin.write(json.dumps(dict(jsonrpc="2.0", **message)) + "\n")
                proc.stdin.flush()

            def rpc(method, params):
                nonlocal sequence
                sequence += 1
                send(dict(id=sequence, method=method, params=params))
                deadline = time.monotonic() + 30
                while True:
                    reply = replies.get(timeout=max(0, deadline - time.monotonic()))
                    if reply is None:
                        raise ConnectionError("图像服务已断开")
                    if reply.get("id") == sequence:
                        if "error" in reply:
                            raise RuntimeError(str(reply["error"]))
                        return reply

            try:
                rpc("initialize", dict(protocolVersion="2025-06-18", capabilities={},
                                       clientInfo=dict(name="ai-director-workbench", version="1")))
                send(dict(method="notifications/initialized"))
                yield lambda tool, args: self._decode(rpc("tools/call", dict(name=tool, arguments=args)))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

    @staticmethod
    def _decode(reply):
        result = reply.get("result", {})
        if result.get("isError"):
            raise RuntimeError(str(result.get("content"))[:500])
        content = result.get("structuredContent") or {}
        items = content.get("result") if isinstance(content.get("result"), list) else None
        if items is None and not content:
            items = result.get("content", [])
        if items is not None:   # FastMCP wraps a JSON string in a text item (verified 2026-11-07 against the local server)
            texts = [c.get("text", "") for c in items if c.get("type") == "text"]
            content = json.loads(texts[0]) if texts else {}
        return content

    def _call(self, tool, arguments):
        with self._session() as call:
            return call(tool, arguments)

    def status(self):
        return self._call("image_service_status", {})

    def _input_copy(self, filename):
        source = Path(filename)
        data = source.read_bytes()
        digest = sha256_bytes(data)
        if source.name != digest:
            raise ValueError("图像输入指纹不符")
        extension = ".png" if data.startswith(b"\x89PNG") else ".jpg"
        target = self.root / "outputs" / "workbench-inputs" / (digest + extension)
        if not target.is_file() or sha256_bytes(target.read_bytes()) != digest:
            write_atomic(target, data)
        return str(target.resolve())

    def submit(self, req):
        args = dict(prompt=req["prompt"], request_id=req["local_request_id"], size=req["size"])
        for key in ("model", "quality"):
            if req.get(key):
                args[key] = req[key]
        tool = "generate_image"
        if req["operation"] == "edit" or req.get("inputs"):
            tool = "edit_image"
            args["image_paths"] = [self._input_copy(path) for path in req["inputs"]]
        with self._session() as call:
            first = call(tool, args)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                info = call("image_job_status", dict(request_id=req["local_request_id"], include_preview=False))
                got = self._outcome(info)
                if got.state != "unknown" or info.get("status") not in ("queued", "running"):
                    return got
                time.sleep(self.poll)
        return ImageOutcome("unknown", provider_request_id=first.get("api_request_id"), error="timeout")

    def inspect(self, local_request_id):
        return self._outcome(self._call("image_job_status", dict(request_id=local_request_id, include_preview=False)))

    @staticmethod
    def _outcome(info):
        state = info.get("status")
        if state == "completed" and info.get("image_path"):
            return ImageOutcome("succeeded", Path(info["image_path"]).read_bytes(), info.get("api_request_id"))
        if state == "failed":
            return ImageOutcome("failed", provider_request_id=info.get("api_request_id"), error=str(info.get("error")))
        return ImageOutcome("unknown", provider_request_id=info.get("api_request_id"), error=str(state))


class ImagesMixin:
    # ============================================================ assets
    def register_asset(self, project_id, name, data: bytes, category, kind="image", episode_id=None, roles=(),
                       origin="import", grid=False, cells=None, asset_id=None, source_version_id=None,
                       changed_cells=None, note="", db=None):
        def apply(db):
            if episode_id is not None:
                self._episode_in(db, episode_id, project_id)
            if asset_id is not None:
                target = row(db, "SELECT project_id,episode_id,kind FROM asset WHERE id=?", asset_id)
                if target["project_id"] != project_id or target["episode_id"] not in (None, episode_id) or target["kind"] != kind:
                    raise Rejected("新版本的目标素材不属于当前项目或集，或媒体类型不符")
            size = image_size(data) if kind == "image" else None
            if kind == "image" and not size:
                raise Rejected("图片无法解码")
            aid = asset_id
            if not aid:
                aid = new_id()
                db.execute("INSERT INTO asset VALUES(?,?,?,?,?,?,?,?,?)",
                           (aid, project_id, episode_id, name, kind, category, origin, dumps(list(roles)), self.clock()))
            no = db.execute("SELECT coalesce(max(version_no),0)+1 FROM asset_version WHERE asset_id=?", (aid,)).fetchone()[0]
            digest = self.store.put_blob(data)
            vid = new_id()
            db.execute("INSERT INTO asset_version VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (vid, aid, no, digest, self.store.blob_path(digest).relative_to(self.store.root).as_posix(),
                        len(data), size[0] if size else None, size[1] if size else None, source_version_id,
                        int(grid), dumps(cells) if cells else None, dumps(changed_cells) if changed_cells else None,
                        None, note, self.clock()))
            return dict(assetId=aid, versionId=vid, versionNo=no, sha256=digest)
        if db is not None:
            return apply(db)
        with transaction(self.db_path) as db2:
            return apply(db2)

    def upload_reference_media(self, episode_id, name, stream, command_id):
        from . import h3export
        if not isinstance(name, str) or not name.strip() or Path(name).name != name or any(
                ord(c) < 32 or c in "/\\" for c in name):
            raise Rejected("参考文件名无效")
        try:
            kind = h3export.media_kind(name)
            limit = h3export.media_size_limit(kind)
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"{name} 超出 {limit // h3export.MIB} MiB 的限制")
            payload = dict(episode=episode_id, name=name, sha256=sha256_bytes(data))
            with connect(self.db_path) as db:
                episode = row(db, "SELECT project_id FROM episode WHERE id=?", episode_id, missing="本集不存在")
                prior = self._existing_command(db, command_id, "reference_media", payload)
                if prior is not None:
                    return prior
            stream.seek(0)
            duration = h3export.media_duration(Path("/dev/stdin"), kind, name, stream)
        except ValueError as exc:
            raise Rejected(str(exc)) from exc
        with transaction(self.db_path) as db:
            def register():
                result = self.register_asset(episode["project_id"], name, data, "reference", kind=kind,
                                             episode_id=episode_id, db=db)
                return dict(result, kind=kind, duration=duration)
            return self._command(db, command_id, "reference_media", payload, register)

    def asset_version(self, version_id, db=None):
        def get(db):
            v = loads(row(db, "SELECT av.*, a.name, a.kind, a.category, a.origin, a.episode_id, a.project_id, a.roles FROM asset_version av "
                             "JOIN asset a ON a.id=av.asset_id WHERE av.id=?", version_id, missing="素材版本不存在"),
                      "cells", "changed_cells", "roles")
            return v
        if db is not None:
            return get(db)
        with connect(self.db_path) as db2:
            return get(db2)

    def _episode_grid(self, db, episode_id, version_id):
        image = self.asset_version(version_id, db)
        if not image["grid"] or image["episode_id"] != episode_id:
            raise Rejected("请选择本集的九宫格图片版本")

    def grid_bindings(self, episode_id, version_id):
        with connect(self.db_path) as db:
            self._episode_grid(db, episode_id, version_id)
            return [dict(r) for r in db.execute("SELECT cell,plan_version_id,shot_id,offset_ms,revision "
                                                "FROM grid_cell_binding WHERE asset_version_id=? ORDER BY cell", (version_id,))]

    def set_grid_cell(self, episode_id, version_id, cell, expected_revision, plan_version_id=None,
                      shot_id=None, offset_ms=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                self._episode_grid(db, episode_id, version_id)
                if type(cell) is not int or not 1 <= cell <= 9 or type(expected_revision) is not int:
                    raise Rejected("请指定格号和当前记录版本")
                prior = db.execute("SELECT revision FROM grid_cell_binding WHERE asset_version_id=? AND cell=?",
                                   (version_id, cell)).fetchone()
                revision = prior[0] if prior else 0
                if revision != expected_revision:
                    raise Conflict("本格已由其他窗口修改，请刷新后核对", current=revision)
                if any(value is not None for value in (plan_version_id, shot_id, offset_ms)):
                    plan = db.execute("SELECT v.id,v.content FROM artifact a JOIN version v ON v.id=a.adopted_version_id "
                                      "WHERE a.episode_id=? AND a.stage='plan'", (episode_id,)).fetchone()
                    if not plan or plan["id"] != plan_version_id:
                        raise Conflict("本集采用的制作方案已变，请刷新后重新选择镜头")
                    timing = timeline.layout(json.loads(plan["content"]))["shots"].get(shot_id) if isinstance(shot_id,str) else None
                    if not timing or type(offset_ms) is not int or not 0 <= offset_ms <= timing["end_ms"] - timing["start_ms"]:
                        raise Rejected("镜头或镜内时刻超出制作方案范围")
                db.execute("INSERT INTO grid_cell_binding VALUES(?,?,?,?,?,?) ON CONFLICT(asset_version_id,cell) "
                           "DO UPDATE SET plan_version_id=excluded.plan_version_id,shot_id=excluded.shot_id,"
                           "offset_ms=excluded.offset_ms,revision=excluded.revision",
                           (version_id, cell, plan_version_id, shot_id, offset_ms, revision + 1))
                return dict(versionId=version_id, cell=cell, revision=revision + 1)
            payload = [episode_id, version_id, cell, expected_revision, plan_version_id, shot_id, offset_ms]
            return self._command(db, command_id, "grid_cell", payload, run)

    def asset_bytes(self, version_id):
        v = self.asset_version(version_id)
        return self.store.read_blob(v["sha256"])

    def episode_assets(self, episode_id):
        with connect(self.db_path) as db:
            rows = db.execute("SELECT av.id FROM asset_version av JOIN asset a ON a.id=av.asset_id "
                              "JOIN episode e ON e.id=a.episode_id AND e.project_id=a.project_id WHERE e.id=? "
                              "ORDER BY a.name, av.version_no", (episode_id,)).fetchall()
            out = [self.asset_version(r[0], db) for r in rows]
            marks = {}
            for r in db.execute("SELECT asset_version_id, mark FROM image_choice WHERE episode_id=?", (episode_id,)):
                marks.setdefault(r[0], []).append(r[1])
            rules = {r["versionId"]: r for r in imagechoices.requirements(db, episode_id)}
        for v in out:
            if v["id"] in rules:
                v["requirement"] = rules[v["id"]]
            v["marks"] = marks.get(v["id"], [])
        return out

    # ============================================================ library
    def submit_reuse(self, version_id, usage, limits="", observation="", aspect=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                v = self.asset_version(version_id, db)
                if v["grid"]:
                    raise Rejected("九宫格只归本集，不能进入复用库")
                return self._library_entry(db, v, usage, limits, observation, aspect)
            return self._command(db, command_id, "reuse", dict(v=version_id, u=usage, l=limits), run)

    def _library_entry(self, db, version, usage, limits="", observation="", aspect=None):
        prior = db.execute("SELECT id,status FROM library_entry WHERE asset_version_id=?", (version["id"],)).fetchone()
        if prior:
            return dict(entryId=prior["id"], status=prior["status"])
        entry_id = new_id()
        db.execute("INSERT INTO library_entry VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (entry_id, version["project_id"], version["id"], usage, aspect, limits, observation,
                    "pending", version["episode_id"], self.clock()))
        return dict(entryId=entry_id,status="pending")

    def upload_library_image(self, project_id, filename, data, category, usage, previous_entry_id=None, command_id=None):
        if not isinstance(usage,str) or not usage.strip():
            usage = "导演上传"
        payload = dict(project=project_id, name=filename, digest=sha256_bytes(data), category=category,
                       usage=usage, previous=previous_entry_id)
        with transaction(self.db_path) as db:
            def run():
                row(db, "SELECT id FROM project WHERE id=?", project_id)
                previous = None
                if previous_entry_id:
                    entry = row(db, "SELECT asset_version_id FROM library_entry WHERE id=? AND project_id=?",
                                previous_entry_id, project_id, missing="本项目没有此资产条目")
                    previous = self.asset_version(entry["asset_version_id"],db)
                    if previous["kind"] != "image" or previous["grid"]:
                        raise Rejected("请选择可复用图片")
                elif category not in ("identity","costume","scene","state","prop","product","reference"):
                    raise Rejected("请选择图片分类")
                asset = self.register_asset(project_id, previous["name"] if previous else Path(filename).name,
                                            data, previous["category"] if previous else category,
                                            episode_id=previous["episode_id"] if previous else None,
                                            roles=previous["roles"] if previous else (),
                                            asset_id=previous["asset_id"] if previous else None,
                                            source_version_id=previous["id"] if previous else None, db=db)
                return dict(asset, **self._library_entry(db,self.asset_version(asset["versionId"],db),usage.strip()))
            return self._command(db,command_id,"library_upload",payload,run)

    def review_library(self, entry_id, status, limits=None, command_id=None):
        if status not in ("approved", "limited", "retired", "rejected"):
            raise Rejected("未知审核结论")
        with transaction(self.db_path) as db:
            def run():
                row(db, "SELECT id FROM library_entry WHERE id=?", entry_id)
                if status == "limited" and not (limits or "").strip():
                    raise Rejected("限条件使用须写明限制")
                db.execute("UPDATE library_entry SET status=?, limits=coalesce(?,limits) WHERE id=?", (status, limits, entry_id))
                return dict(entryId=entry_id, status=status)
            return self._command(db, command_id, "lib_review", dict(e=entry_id, s=status, l=limits), run)

    def library(self, project_id, q="", status=None, kind=None):
        with connect(self.db_path) as db:
            rows = db.execute("SELECT l.*, e.display_code AS sourceEpisodeCode, a.name, a.kind, a.category, av.sha256, av.version_no, av.width, av.height "
                              "FROM library_entry l JOIN asset_version av ON av.id=l.asset_version_id "
                              "JOIN asset a ON a.id=av.asset_id LEFT JOIN episode e ON e.id=l.source_episode_id WHERE l.project_id=? ORDER BY l.created_at", (project_id,))
            out = [dict(r) for r in rows if (not q or q in r["name"] or q in r["usage"]) and (not status or r["status"] == status)
                   and (not kind or r["category"] == kind)]
            for e in out:
                e["usedBy"] = [dict(r) for r in db.execute(
                    "SELECT a.episode_id, av.id AS copy_version FROM asset_version av JOIN asset a ON a.id=av.asset_id "
                    "WHERE av.library_entry_id=?", (e["id"],))]
        return out

    def copy_to_episode(self, entry_id, episode_id, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                return self._copy_library_image(db, entry_id, episode_id)
            return self._command(db, command_id, "lib_copy", dict(e=entry_id, ep=episode_id), run)

    def _copy_library_image(self, db, entry_id, episode_id):
        e = row(db, "SELECT * FROM library_entry WHERE id=?", entry_id)
        if e["status"] not in ("approved", "limited"):
            raise Rejected("只有已核准的复用条目可以复制到本集")
        self._episode_in(db, episode_id, e["project_id"])
        v = self.asset_version(e["asset_version_id"], db)
        data = self.store.read_blob(v["sha256"])
        res = self.register_asset(e["project_id"], v["name"], data, v["category"], v["kind"], episode_id, v["roles"],
                                  origin="library_copy", source_version_id=v["id"], note=f"复用 {entry_id}", db=db)
        db.execute("UPDATE asset_version SET library_entry_id=? WHERE id=?", (entry_id, res["versionId"]))
        return dict(res, limits=e["limits"])

    # ============================================================ image batches
    def image_providers(self):
        return {name: dict(verified=p.verified, capabilities=p.capabilities, priceCents=p.price_cents, currency=p.currency,
                           **({"settings":p.settings()} if hasattr(p, "save_settings") else {}))
                for name, p in self.image_providers_map.items()}

    def save_image_provider_settings(self, provider, max_concurrency):
        channel = self.image_providers_map.get(provider)
        if channel is None or not hasattr(channel, "save_settings"):
            raise Rejected("此图像通道不支持并发设置")
        return channel.save_settings(max_concurrency)

    def create_batch(self, episode_id, provider, operation, items, size, model=None, quality=None, budget_cents=None,
                     command_id=None):
        """items: [{prompt, inputs:[assetVersionId], target:{name, category, assetId?}, segments:[..], cells:[..]}]"""
        if any("needKey" in item.get("target", {}) for item in items):
            raise Rejected("请通过备图工作单创建素材批次")
        with transaction(self.db_path) as db:
            return self._create_image_batch(db, episode_id, provider, operation, items, size, model, quality, budget_cents, command_id)

    def _create_image_batch(self, db, episode_id, provider, operation, items, size, model, quality, budget_cents, command_id):
        if provider not in self.image_providers_map:
            raise Rejected("未配置的图像通道")
        prov = self.image_providers_map[provider]
        models = prov.capabilities.get("models")
        if models and model not in models:
            raise Rejected("请选择本图像通道支持的型号")
        budget_cents = None  # Current production policy does not gate generation on cost.
        if operation not in prov.capabilities["operations"]:
            raise Rejected(f"{provider} 不支持 {operation}")
        if size not in prov.capabilities["sizes"]:
            raise Rejected(f"{provider} 不支持尺寸 {size}")
        quality = quality or "low"
        if quality not in prov.capabilities["quality"]:
            raise Rejected(f"{provider} 不支持质量 {quality}")
        if not items or any(not it.get("prompt", "").strip() for it in items):
            raise Rejected("批次至少需要一张图，每张须有绘图要求")
        if any(len(it.get("inputs", [])) > 16 or (operation == "edit" and not it.get("inputs")) for it in items):
            raise Rejected("带图请求须提供 1–16 张有序输入")
        ep = self._episode_in(db, episode_id)
        locks = self._space_locks(db, episode_id)
        inputs = []
        scene_inputs = []
        library_sources = {}
        prepared_items = []
        for item in items:
            aid = item.get("target", {}).get("assetId")
            if aid:
                target = row(db, "SELECT project_id,episode_id,kind FROM asset WHERE id=?", aid, missing="目标素材不存在")
                if target["project_id"] != ep["project_id"] or target["episode_id"] not in (None, episode_id) or target["kind"] != "image":
                    raise Rejected("本批次只能为本项目、本集可用的图像生成新版本")
            ordered = [self.asset_version(i, db) for i in item.get("inputs", [])]
            if any(v["kind"] != "image" or v["project_id"] != ep["project_id"]
                   or v["episode_id"] not in (None, episode_id) for v in ordered):
                raise Rejected("出图输入必须是本项目、本集可用的图像版本")
            entry_id = item.get("libraryEntryId")
            if entry_id:
                entry = row(db, "SELECT * FROM library_entry WHERE project_id=? AND id=?", ep["project_id"], entry_id)
                base = ordered[0] if ordered else {}
                if operation != "edit" or entry["status"] not in ("approved","limited") or not (
                        base.get("id") == entry["asset_version_id"] or
                        base.get("library_entry_id") == entry_id and base.get("source_version_id") == entry["asset_version_id"]):
                    raise Rejected("请重新选择已核准的资产库底图版本")
                original = self.asset_version(entry["asset_version_id"], db)
                library_sources[entry_id] = dict(versionId=original["id"],name=original["name"],
                                                 versionNo=original["version_no"],sha256=original["sha256"],limits=entry["limits"])
                if entry["limits"]:
                    item = dict(item,prompt=item["prompt"]+"\n底图使用条件："+entry["limits"])
            prepared_items.append(item)
            inputs.append([v["sha256"] for v in ordered])
            scoped = item.get("segments", [])
            if not isinstance(scoped, list) or any(not isinstance(seg,str) for seg in scoped):
                raise Rejected("每张图的适用段须为段号列表")
            relevant = [lock for lock in locks if set(lock["segments"]).intersection(scoped)]
            blocked = [lock for lock in relevant if lock["status"] == "pending"]
            if blocked:
                names = [seg for seg in scoped if any(seg in lock["segments"] for lock in blocked)]
                raise Rejected("依赖未确认空间锁定的段不能出图：" + "、".join(names),
                               detail={seg:[lock["label"] for lock in blocked if seg in lock["segments"]] for seg in names})
            scene_inputs.append([dict(lock,positions=[n for n,h in enumerate(inputs[-1],1) if h==lock["sha256"]])
                                 for lock in relevant])
        spec = dict(items=prepared_items, inputs=inputs, model=model, quality=quality)
        if library_sources:
            spec["librarySources"] = library_sources
        if any(scene_inputs):
            spec["sceneLocks"] = scene_inputs
        palettes = self.episode_palettes(episode_id,"assets")["cards"]
        if palettes:
            spec["palettes"] = [stage_card(card, "assets") for card in palettes]
            reference = "\n".join(card["visual"] + "\n" + card["image"] for card in palettes)
            spec["items"] = [dict(item, prompt=item["prompt"] + "\n" + SCOPE + "\n" + reference) for item in spec["items"]]
        from .enhancements import entries as method_entries
        config = self.step_config(episode_id, "assets")
        if config.get("references"):
            spec["citations"] = [{k: v for k, v in ref.items() if k != "metadata"}
                                 for ref in config["references"]]
            excerpts = "\n\n".join(f"《{ref['title']}》 · {ref['reason']}\n{ref['text']}"
                                   for ref in spec["citations"])
            spec["items"] = [dict(item, prompt=item["prompt"] + "\n本批选用的知识库段落\n" + excerpts)
                             for item in spec["items"]]
        definition = config.get("form")
        if definition:
            notes = [f"{label}：{config[key]}" for key,label in definition["fields"].items() if config.get(key)]
            if notes:
                spec["stepRequirements"] = dict(revision=config["revision"],form=definition,notes=notes)
                guidance = "\n".join(notes)
                spec["items"] = [dict(item,prompt=item["prompt"]+"\n本批画面要求\n"+guidance) for item in spec["items"]]
        methods = method_entries(config.get("enhancements", []))
        if methods:
            spec["enhancements"] = [entry["methodSource"] for entry in methods]
            guidance = "\n".join(entry["name"] + "\n" + entry["text"] for entry in methods)
            spec["items"] = [dict(item, prompt=item["prompt"] + "\n本次选用的美术参考方法，服从本项目设定和当前画面要求。\n" + guidance)
                             for item in spec["items"]]
        input_hash = sha256_bytes(canonical(dict(spec, operation=operation, size=size)))
        spec["currency"] = prov.currency
        if getattr(prov, "billing_mode", None) == "subscription_usage":
            spec["billingMode"] = "subscription_usage"
        estimate = prov.price_cents * len(items) if prov.price_cents is not None else None
        summary = dict(provider=provider, currency=prov.currency, verified=prov.verified, model=model, operation=operation, count=len(items),
                       size=size, inputs=inputs, quality=quality, inputHash=input_hash, budget_cents=budget_cents, estimate_cents=estimate)
        def run():
            # Keep annotations as of authorisation preparation, even if the source is edited before landing.
            bindings = {}
            for item in items:
                if operation == "edit" and item.get("target", {}).get("grid") and item.get("inputs"):
                    source = item["inputs"][0]
                    records = [dict(r) for r in db.execute(
                        "SELECT cell,plan_version_id,shot_id,offset_ms FROM grid_cell_binding "
                        "WHERE asset_version_id=? AND plan_version_id IS NOT NULL ORDER BY cell", (source,))]
                    if records:
                        bindings[source] = records
            if bindings:
                spec["gridBindings"] = bindings
            # A cancelled, never-sent batch keeps its receipt. Tie its replacement to that receipt.
            candidate_hash = input_hash
            while True:
                existing = db.execute("SELECT id,state,spec FROM image_batch WHERE episode_id=? AND input_hash=? AND provider=?",
                                      (episode_id, candidate_hash, provider)).fetchone()
                if not existing:
                    break
                # A failed historical batch may be rebuilt after every outcome is resolved.
                historical_failure = existing["state"] == "failed" and not json.loads(existing["spec"]).get("currency")
                if existing["state"] != "cancelled" and not historical_failure:
                    raise Conflict("相同输入的批次已存在，不重复提交", current=existing["id"])
                candidate_hash = sha256_bytes(canonical([input_hash, existing["id"]]))
            receipt = dict(summary, inputHash=candidate_hash)
            summary_hash = sha256_bytes(canonical(receipt))
            bid = new_id()
            db.execute("INSERT INTO image_batch VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (bid, episode_id, candidate_hash, provider, model, operation, size, len(items), dumps(spec),
                        budget_cents, estimate, "subscription_usage" if spec.get("billingMode") == "subscription_usage" else "estimated" if estimate is not None else "unknown", summary_hash, None,
                        "awaiting_authorization", self.clock()))
            for n, it in enumerate(items, 1):
                db.execute("INSERT INTO image_request(id,batch_id,ordinal,local_request_id,state) VALUES(?,?,?,?,?)",
                           (new_id(), bid, n, f"adw-{bid[:8]}-{n:02d}", "pending"))
            return dict(batchId=bid, summary=receipt, summaryHash=summary_hash)
        return self._command(db, command_id, "batch", dict(e=episode_id, h=input_hash, p=provider, budget=budget_cents, currency=prov.currency), run)

    def _batch_currency(self, batch):
        provider = self.image_providers_map.get(batch["provider"])
        if not provider:
            raise Rejected("图像通道不可用")
        return provider.currency

    def authorize_batches(self, episode_id, batches, command_id):
        with transaction(self.db_path) as db:
            def run():
                self._episode_in(db, episode_id)
                if not isinstance(batches, dict) or not batches:
                    raise Rejected("请选择待批准的批次")
                for batch_id, summary_hash in batches.items():
                    batch = row(db, "SELECT * FROM image_batch WHERE id=? AND episode_id=?", batch_id, episode_id)
                    if batch["summary_hash"] != summary_hash:
                        raise Rejected("批次内容已变化，请刷新核对")
                    if batch["state"] == "authorized":
                        continue
                    if batch["state"] != "awaiting_authorization":
                        raise Rejected("批次状态已变化，请刷新核对")
                    currency = self._batch_currency(batch)
                    auth = dict(summaryHash=summary_hash, currency=currency, budget_cents=None,
                                costPolicy="not_gated", at=self.clock(), command=command_id)
                    db.execute("UPDATE image_batch SET state='authorized',authorization=?,budget_cents=NULL WHERE id=?",
                               (dumps(auth), batch_id))
                return dict(batchIds=list(batches))
            return self._command(db, command_id, "authorize_batches", [episode_id,batches], run)

    def authorize_batch(self, batch_id, summary_hash, command_id, accept_unknown_cost=False, budget_cents=None, accept_usage=False):
        with transaction(self.db_path) as db:
            def run():
                b = row(db, "SELECT * FROM image_batch WHERE id=?", batch_id)
                if b["summary_hash"] != summary_hash:
                    raise Rejected("授权摘要与批次不符，请重新核对")
                if b["state"] != "awaiting_authorization":
                    raise Rejected("批次不在待授权状态")
                currency = self._batch_currency(b)
                auth = dict(summaryHash=summary_hash, currency=currency, budget_cents=None,
                            costPolicy="not_gated", at=self.clock(), command=command_id)
                db.execute("UPDATE image_batch SET state='authorized', authorization=?, budget_cents=NULL WHERE id=?",
                           (dumps(auth), batch_id))
                return dict(batchId=batch_id, state="authorized")
            payload = dict(b=batch_id, h=summary_hash, budget=budget_cents, unknown=accept_unknown_cost)
            if accept_usage:
                payload["usage"] = accept_usage
            return self._command(db, command_id, "authorize", payload, run)

    def cancel_batch(self, batch_id, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                b = row(db, "SELECT state FROM image_batch WHERE id=?", batch_id)
                started = db.execute("SELECT 1 FROM image_request WHERE batch_id=? AND state!='pending' LIMIT 1",
                                     (batch_id,)).fetchone()
                if started or b["state"] not in ("awaiting_authorization", "authorized"):
                    raise Rejected("已执行的批次不能取消，只能核对")
                db.execute("UPDATE image_batch SET state='cancelled' WHERE id=?", (batch_id,))
                return dict(batchId=batch_id, state="cancelled")
            return self._command(db, command_id, "cancel_batch", dict(b=batch_id), run)

    def execute_batch_precheck(self, batch_id):
        with connect(self.db_path) as db:
            b = row(db, "SELECT * FROM image_batch WHERE id=?", batch_id)
        if b["state"] not in ("authorized", "partial"):
            raise Rejected("批次未授权或已结束")
        currency = self._batch_currency(b)
        if not b["authorization"]:
            raise Rejected("批次尚未批准")
        return b

    def execute_batch(self, batch_id):
        """Send only requests that were never sent. Unknown / submitted ones wait for reconciliation."""
        b = self.execute_batch_precheck(batch_id)
        prov = self.image_providers_map[b["provider"]]
        with connect(self.db_path) as db:
            pending = db.execute("SELECT count(*) FROM image_request WHERE batch_id=? AND state='pending'", (batch_id,)).fetchone()[0]
        workers = min(getattr(prov, "max_concurrency", 1), pending)
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="image-batch") as pool:
                futures = [pool.submit(self._execute_image_batch_loop, b, prov) for _ in range(workers)]
                for future in futures:
                    future.result()
        else:
            self._execute_image_batch_loop(b, prov)
        return self.batch_view(batch_id)

    def _execute_image_batch_loop(self, b, prov):
        batch_id = b["id"]
        spec = json.loads(b["spec"])
        while True:
            with transaction(self.db_path) as db:
                if row(db, "SELECT state FROM image_batch WHERE id=?", batch_id)["state"] == "cancelled":
                    break
                req = db.execute("SELECT * FROM image_request WHERE batch_id=? AND state='pending' ORDER BY ordinal LIMIT 1",
                                 (batch_id,)).fetchone()
                if not req:
                    break
                db.execute("UPDATE image_request SET state='submitted', submitted_at=? WHERE id=?", (self.clock(), req["id"]))
            ordinal = self._original_ordinal(req)
            item = spec["items"][ordinal - 1]
            inputs = []
            try:
                for digest in spec["inputs"][ordinal - 1]:
                    self.store.read_blob(digest)
                    inputs.append(str(self.store.blob_path(digest)))
            except (OSError, ValueError) as exc:
                self._land(batch_id, req["id"], ImageOutcome("failed", error="冻结的输入图片不可用：" + str(exc)), b, item)
                continue
            try:
                out = prov.submit(dict(local_request_id=req["local_request_id"], operation=b["operation"],
                                       prompt=item["prompt"], inputs=inputs, size=b["size"], model=b["model"],
                                       quality=spec.get("quality")))
            except Exception as exc:   # transport error after sending: outcome unknown, never re-sent
                out = ImageOutcome("unknown", error=repr(exc)[:300])
            self._land(batch_id, req["id"], out, b, item)
            if out.state == "unknown":
                self.settle_unknown_requests([batch_id])

    def _original_ordinal(self, request):
        if not request["replaces_id"]:
            return request["ordinal"]
        with connect(self.db_path) as db:
            while request["replaces_id"]:
                request = row(db, "SELECT ordinal,replaces_id FROM image_request WHERE id=?", request["replaces_id"])
        return request["ordinal"]

    def _land(self, batch_id, request_id, out, b, item):
        with transaction(self.db_path) as db:
            current = row(db, "SELECT state FROM image_request WHERE id=? AND batch_id=?", request_id, batch_id)
            if current["state"] not in ("submitted", "unknown"):
                return False
            if out.state == "succeeded":
                size = image_size(out.data)
                want = tuple(map(int, b["size"].split("x")))
                if size != want:
                    db.execute("UPDATE image_request SET state='failed', error=?, ended_at=? WHERE id=?",
                               (f"尺寸不符：{size}≠{want}", self.clock(), request_id))
                else:
                    target = item.get("target", {})
                    ep = row(db, "SELECT project_id FROM episode WHERE id=?", b["episode_id"])
                    res = self.register_asset(ep["project_id"], target.get("name", "候选图"), out.data,
                                              target.get("category", "state"), "image", b["episode_id"],
                                              target.get("roles", []), origin="generated", grid=bool(target.get("grid")),
                                              cells=target.get("cellsMeta"), asset_id=target.get("assetId"),
                                              source_version_id=(item.get("inputs") or [None])[0] if b["operation"] == "edit" else None,
                                              changed_cells=item.get("cells"), note=f"批次 {batch_id}", db=db)
                    if target.get("grid") and b["operation"] == "edit":
                        source = item["inputs"][0]
                        bindings = json.loads(b["spec"]).get("gridBindings", {}).get(source, [])
                        db.executemany("INSERT INTO grid_cell_binding VALUES(?,?,?,?,?,1)",
                                       [(res["versionId"], c["cell"], c["plan_version_id"], c["shot_id"], c["offset_ms"])
                                        for c in bindings])
                    if target.get("needKey"):
                        self._land_asset_need(db, b["episode_id"], target["needKey"], res)
                    db.execute("UPDATE image_request SET state='succeeded', provider_request_id=?, asset_version_id=?, sha256=?, "
                               "ended_at=? WHERE id=?", (out.provider_request_id, res["versionId"], res["sha256"],
                                                         self.clock(), request_id))
            else:
                db.execute("UPDATE image_request SET state=?, error=?, provider_request_id=?, ended_at=? WHERE id=?",
                           ("unknown" if out.state == "unknown" else "failed", out.error, out.provider_request_id,
                            self.clock(), request_id))
            self._batch_state(db, batch_id)
            return True

    def _batch_state(self, db, batch_id):
        states = [r[0] for r in db.execute("SELECT state FROM image_request WHERE batch_id=? AND id NOT IN "
                                           "(SELECT replaces_id FROM image_request WHERE replaces_id IS NOT NULL)", (batch_id,))]
        if all(s == "succeeded" for s in states):
            state = "completed"
        elif any(s in ("unknown", "submitted") for s in states):
            state = "pending_reconcile"
        elif any(s == "pending" for s in states):
            state = "partial"
        elif any(s == "succeeded" for s in states):
            state = "partially_succeeded"
        else:
            state = "failed"
        db.execute("UPDATE image_batch SET state=? WHERE id=?", (state, batch_id))

    def reconcile_request(self, request_id, finding, data: bytes | None = None, command_id=None):
        """finding: 'completed' (with the image found in the provider's records) | 'not_executed'."""
        with connect(self.db_path) as db:
            req = row(db, "SELECT * FROM image_request WHERE id=?", request_id)
            b = row(db, "SELECT * FROM image_batch WHERE id=?", req["batch_id"])
        if req["state"] not in ("unknown", "submitted"):
            raise Rejected("该请求不需要核对")
        if finding == "completed":
            if not data:
                raise Rejected("登记已完成须提供实际图片")
            ordinal = self._original_ordinal(req)
            item = json.loads(b["spec"])["items"][ordinal - 1]   # a resubmitted request keeps the original item
            if not self._land(b["id"], request_id, ImageOutcome("succeeded", data, req["provider_request_id"]), b, item):
                raise Conflict("该请求已核对，请刷新回执")
        elif finding == "not_executed":
            with transaction(self.db_path) as db:
                changed = db.execute("UPDATE image_request SET state='confirmed_not_executed', ended_at=? "
                                     "WHERE id=? AND state IN ('unknown','submitted')", (self.clock(), request_id))
                if not changed.rowcount:
                    raise Conflict("该请求已核对，请刷新回执")
                self._batch_state(db, b["id"])
        else:
            raise Rejected("未知核对结论")
        return self.batch_view(b["id"])

    def resubmit_request(self, request_id, command_id):
        """After a request is confirmed not executed (or failed), send it again under the batch's authorisation."""
        with transaction(self.db_path) as db:
            def run():
                req = row(db, "SELECT * FROM image_request WHERE id=?", request_id)
                if req["state"] not in ("confirmed_not_executed", "failed"):
                    raise Rejected("结果未查明前不能重新提交")
                b = row(db, "SELECT * FROM image_batch WHERE id=?", req["batch_id"])
                self._batch_currency(b)
                if not b["authorization"]:
                    raise Rejected("批次未授权")
                if db.execute("SELECT 1 FROM image_request WHERE replaces_id=?", (request_id,)).fetchone():
                    raise Conflict("此请求已登记补发，请查看最新请求")
                n = db.execute("SELECT max(ordinal)+1 FROM image_request WHERE batch_id=?", (b["id"],)).fetchone()[0]
                nid = new_id()
                db.execute("INSERT INTO image_request(id,batch_id,ordinal,local_request_id,state,replaces_id) "
                           "VALUES(?,?,?,?,?,?)", (nid, b["id"], n, f"adw-{b['id'][:8]}-{n:02d}", "pending", request_id))
                db.execute("UPDATE image_batch SET state='partial' WHERE id=?", (b["id"],))
                db.execute("INSERT INTO asset_need_request SELECT ?,need_id FROM asset_need_request WHERE request_id=?", (nid,request_id))
                return dict(requestId=nid)
            return self._command(db, command_id, "resubmit", dict(r=request_id), run)

    def settle_unknown_requests(self, batch_ids=None):
        with connect(self.db_path) as db:
            requests=[dict(r) for r in db.execute("SELECT * FROM image_request WHERE state='unknown'")
                      if batch_ids is None or r['batch_id'] in batch_ids]
        for req in requests:
            # A portable copy has no authority over work still running in the source workbench.
            if req.get('error')=='请核对原工作台的请求结果':continue
            with connect(self.db_path) as db:
                batch=dict(row(db,'SELECT * FROM image_batch WHERE id=?',req['batch_id']))
            prov=self.image_providers_map.get(batch['provider'])
            if prov is None:continue
            try:out=prov.inspect(req['local_request_id'])
            except Exception:out=None
            if not out or out.state not in ('succeeded','failed'):
                # Absence of a query API or local result does not prove non-execution.
                # Preserve the request identity; the expert monitor owns its deadline.
                continue
            ordinal=self._original_ordinal(req)
            item=json.loads(batch['spec'])['items'][ordinal-1]
            self._land(batch['id'],req['id'],out,batch,item)

    def mark_interrupted_requests(self):
        """On restart: anything left 'submitted' becomes 'unknown' (待核对), never re-sent."""
        with transaction(self.db_path) as db:
            ids = [r[0] for r in db.execute("SELECT DISTINCT batch_id FROM image_request WHERE state='submitted'")]
            db.execute("UPDATE image_request SET state='unknown', error='interrupted' WHERE state='submitted'")
            for bid in ids:
                self._batch_state(db, bid)
        return ids

    def batch_view(self, batch_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            b = loads(row(db, "SELECT * FROM image_batch WHERE id=?", batch_id), "spec", "authorization")
            b["currency"] = b["spec"].get("currency")
            provider = self.image_providers_map.get(b["provider"])
            b["billingCurrent"] = bool(provider)
            b["requests"] = [dict(r) for r in db.execute("SELECT r.*, EXISTS (SELECT 1 FROM image_request next WHERE next.replaces_id=r.id) AS replaced "
                                                         "FROM image_request r WHERE r.batch_id=? ORDER BY r.ordinal",(batch_id,))]
            ordinals = {}
            for request in b["requests"]:
                parent = request["replaces_id"]
                origin = ordinals.get(parent) if parent else request["ordinal"]
                request["sourceOrdinal"] = ordinals[request["id"]] = origin
            groups = []
            labels = {}
            frozen = b["spec"].get("inputs") or []
            for index, item in enumerate(b["spec"]["items"]):
                ids = item.get("inputs")
                hashes = frozen[index] if index < len(frozen) else None
                if hashes is None and not ids:
                    groups.append(None)
                    continue
                group = []
                for vid, digest in zip_longest(ids or [], hashes or []):
                    if vid not in labels:
                        labels[vid] = db.execute("SELECT a.name,v.version_no,v.sha256 FROM asset_version v "
                            "JOIN asset a ON a.id=v.asset_id JOIN episode e ON e.project_id=a.project_id "
                            "WHERE v.id=? AND e.id=? AND (a.episode_id IS NULL OR a.episode_id=e.id)",
                            (vid,b["episode_id"])).fetchone()
                    record = dict(versionId=vid, sha256=digest)
                    label = labels[vid]
                    if label and label["sha256"] == digest:
                        record.update(name=label["name"], versionNo=label["version_no"])
                    group.append(record)
                groups.append(group)
            b["inputGroups"] = groups
        return b

    def episode_batches(self, episode_id):
        with connect(self.db_path) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM image_batch WHERE episode_id=? ORDER BY created_at", (episode_id,))]
        return [self.batch_view(i) for i in ids]

    # ============================================================ choice, redraw, adoption
    def _episode_image(self, db, episode_id, version_id):
        asset = self.asset_version(version_id, db)
        self._episode_in(db, episode_id, asset["project_id"])
        if asset["episode_id"] != episode_id or asset["kind"] != "image":
            raise Rejected("请选择本集的图片版本")
        return asset

    def mark_image(self, episode_id, version_id, mark, shot_ids=(), command_id=None, enabled=True, requirement=None):
        with transaction(self.db_path) as db:
            def run():
                asset = self._episode_image(db, episode_id, version_id)
                if mark not in ("required","backup","recommended"):
                    raise Rejected("只能标记本集的候选图")
                if requirement is not None and (not enabled or mark != "required"):
                    raise Rejected("用途和目标段只用于必用图")
                if enabled:
                    db.execute("INSERT OR IGNORE INTO image_choice VALUES(?,?,?,?,?,?)",
                               (new_id(), episode_id, version_id, mark, dumps(list(shot_ids)), self.clock()))
                    if shot_ids:
                        db.execute("UPDATE image_choice SET shot_ids=? WHERE episode_id=? AND asset_version_id=? AND mark=?",
                                   (dumps(list(shot_ids)), episode_id, version_id, mark))
                    if shot_ids and mark == 'required' and requirement is None:
                        previous = db.execute("SELECT d.choice_id,d.details FROM required_image_details d JOIN image_choice c ON c.id=d.choice_id WHERE c.episode_id=? AND c.asset_version_id=? AND c.mark='required'", (episode_id,version_id)).fetchone()
                        if previous:
                            body = json.loads(previous['details'])
                            body.pop('autoBindings', None)
                            body.pop('bindingBase', None)
                            body['selectedBy'] = 'user'
                            db.execute("UPDATE required_image_details SET details=? WHERE choice_id=?", (dumps(body),previous['choice_id']))
                    if requirement is not None:
                        details = imagechoices.validate_request(requirement, self._adopted_content(db, episode_id, "plan"))
                        details["sha256"] = asset["sha256"]
                        cid = row(db, "SELECT id FROM image_choice WHERE episode_id=? AND asset_version_id=? AND mark=?",
                                  episode_id, version_id, mark)["id"]
                        prior = db.execute("SELECT details FROM required_image_details WHERE choice_id=?", (cid,)).fetchone()
                        if prior:
                            previous = json.loads(prior['details'])
                            previous.pop('autoBindings', None)
                            previous.pop('bindingBase', None)
                            details = {**previous, **details}
                        db.execute("INSERT INTO required_image_details VALUES(?,?) ON CONFLICT(choice_id) "
                                   "DO UPDATE SET details=excluded.details", (cid, dumps(details)))
                else:
                    db.execute("DELETE FROM image_choice WHERE episode_id=? AND asset_version_id=? AND mark=?",(episode_id,version_id,mark))
                return dict(ok=True)
            payload = dict(e=episode_id, v=version_id, m=mark, enabled=enabled, shots=list(shot_ids))
            if requirement is not None:
                payload["requirement"] = requirement
            return self._command(db, command_id, "mark", payload, run)

    def bind_required_image(self, episode_id, version_id, shot_ids, plan_version_id,
                            source, basis_hash, reason, expected, command_id=None, need_key=None):
        """Update one AI contribution without replacing director or other uses."""
        with transaction(self.db_path) as db:
            art = row(db, "SELECT adopted_version_id FROM artifact WHERE episode_id=? AND stage='plan'", episode_id)
            if art['adopted_version_id'] != plan_version_id:
                raise Conflict('制作方案已变化，请按当前方案重新匹配素材用途')
            if need_key is not None:
                need = db.execute('SELECT adopted_version_id FROM asset_need WHERE episode_id=? AND need_key=? AND removed=0',(episode_id,need_key)).fetchone()
                if not need or need[0]!=version_id:
                    raise Conflict('素材采用图片已变化，请按当前图片重新匹配')
            asset = self._episode_image(db, episode_id, version_id)
            rules = imagechoices.requirements(db, episode_id)
            current = next((r for r in rules if r['versionId'] == version_id), None)
            if current != expected:
                applied=(current or {}).get('autoBindings',{}).get(source,{})
                if applied.get('basisHash')==basis_hash and applied.get('shotIds')==shot_ids and set(shot_ids)<=set(current['shotIds']):
                    return dict(ok=True)
                raise Conflict('素材用途已变化，请按当前绑定重新匹配')
            plan = self._adopted_content(db, episode_id, 'plan')
            shots = {s['id']: s['segment'] for s in plan['shots']}
            release = (not shot_ids and (source == 'worksheet' or source.startswith('grid:'))
                       and source in (current or {}).get('autoBindings', {}))
            if (not shot_ids and not release) or len(set(shot_ids)) != len(shot_ids) or set(shot_ids) - shots.keys():
                raise Rejected('素材用途须指定当前方案中不重复的镜头')
            def apply():
                old = current or dict(shotIds=[], targetSegments=[], selectedBy='agent',
                                      reason=reason, fitness='not_tested', limitations='', sha256=asset['sha256'])
                details = {k: v for k, v in old.items() if k not in ('versionId', 'name', 'shotIds')}
                sources = dict(details.get('autoBindings', {}))
                # Only remove the contribution this source actually introduced.
                base = details.get('bindingBase')
                if base is None:
                    introduced = {s for value in sources.values() for s in value.get('addedShotIds', [])}
                    derived_before = {s for value in sources.values() for s in value.get('addedSegments', [])}
                    base = dict(shotIds=sorted(set(old['shotIds'])-introduced),targetSegments=sorted(set(old['targetSegments'])-derived_before))
                details['bindingBase'] = base
                kept = set(base['shotIds'])
                targets = set(base['targetSegments'])
                for key, value in sources.items():
                    if key != source:
                        kept.update(value.get('shotIds', []))
                        targets.update(value.get('targetSegments', []))
                added = set(shot_ids) - kept
                derived = {shots[s] for s in shot_ids}
                if release:
                    sources.pop(source)
                    if not sources and not kept and not targets and old['selectedBy'] == 'agent':
                        db.execute("DELETE FROM image_choice WHERE episode_id=? AND asset_version_id=? AND mark='required'", (episode_id, version_id))
                        return dict(ok=True)
                else:
                    sources[source] = dict(basisHash=basis_hash, shotIds=list(shot_ids), targetSegments=sorted(derived),
                                           addedShotIds=sorted(added), addedSegments=sorted(derived-targets), reason=reason, needKey=need_key)
                details.update(autoBindings=sources, targetSegments=sorted(targets | derived))
                choice = db.execute("SELECT id FROM image_choice WHERE episode_id=? AND asset_version_id=? AND mark='required'", (episode_id,version_id)).fetchone()
                choice_id = choice['id'] if choice else new_id()
                db.execute("INSERT INTO image_choice VALUES(?,?,?,?,?,?) ON CONFLICT(episode_id,asset_version_id,mark) DO UPDATE SET shot_ids=excluded.shot_ids",
                           (choice_id,episode_id,version_id,'required',dumps(sorted(kept | set(shot_ids))),self.clock()))
                db.execute("INSERT INTO required_image_details VALUES(?,?) ON CONFLICT(choice_id) DO UPDATE SET details=excluded.details", (choice_id,dumps(details)))
                return dict(ok=True)
            return self._command(db, command_id, 'bind_required_image',
                                 [episode_id,version_id,shot_ids,plan_version_id,source,basis_hash,reason,expected,need_key], apply)

    def redraw_cells(self, episode_id, base_version_id, cells, reference_version_ids, prompt, provider, size,
                     budget_cents=None, command_id=None, quality=None, segments=None, model=None):
        """修一格: edit A's cells using B as a drawing reference. The result is a new candidate of asset A."""
        base = self.asset_version(base_version_id)
        if not base["grid"]:
            raise Rejected("局部修格只适用于九宫格")
        if not cells or any(not 1 <= c <= 9 for c in cells):
            raise Rejected("格号须为 1–9")
        cell_text = "、".join(str(c) for c in sorted(set(cells)))
        instruction = f"以第一张九宫格为底图，按从左到右、从上到下编号，只修改第 {cell_text} 格；保留其他格。\n{prompt}"
        item = dict(prompt=instruction, inputs=[base_version_id, *reference_version_ids], cells=sorted(cells),
                    target=dict(assetId=base["asset_id"], name=base["name"], category=base["category"], grid=True,
                                cellsMeta=base["cells"], roles=base["roles"]))
        if segments:
            item["segments"] = segments
        return self.create_batch(episode_id, provider, "edit", [item], size, model=model, quality=quality, budget_cents=budget_cents, command_id=command_id)

    def compare_cells(self, new_version_id):
        v = self.asset_version(new_version_id)
        base = v["source_version_id"]
        requested = v["changed_cells"] or []
        result = dict(base=base, requested=requested, changed=[], unchanged=[], comparable=False)
        if not base or not v["grid"]:
            return result
        with Image.open(BytesIO(self.asset_bytes(base))) as original, Image.open(BytesIO(self.asset_bytes(new_version_id))) as candidate:
            if original.size != candidate.size:
                return result
            before, after = original.convert("RGBA"), candidate.convert("RGBA")
            width, height = before.size
            for index in range(9):
                y, x = divmod(index, 3)
                box = (x * width // 3, y * height // 3, (x + 1) * width // 3, (y + 1) * height // 3)
                changed = before.crop(box).tobytes() != after.crop(box).tobytes()
                result["changed" if changed else "unchanged"].append(index + 1)
        result["comparable"] = True
        return result

    def image_references(self, version_id):
        """Segments whose adopted prompts reference any version of the same asset."""
        with connect(self.db_path) as db:
            v = self.asset_version(version_id, db)
            return self._image_references(db, v)

    def _image_references(self, db, v):
        versions = dict(db.execute("SELECT id,version_no FROM asset_version WHERE asset_id=?", (v["asset_id"],)))
        adopted = db.execute("SELECT v.id,v.display_no,v.content FROM artifact a JOIN version v ON v.id=a.adopted_version_id "
                             "WHERE a.episode_id=? AND a.stage='prompts'", (v["episode_id"],)).fetchone()
        if not adopted:
            return []
        prompts_ = json.loads(adopted["content"])
        hits = []
        for seg, entry in (prompts_.get("segments") or {}).items():
            for ref in entry.get("refs", []):
                if ref.get("assetVersionId") in versions:
                    current = ref["assetVersionId"]
                    hits.append(dict(segment=seg, label=ref["label"], current=current, imageVersion=versions[current],
                                     shots=ref.get("shots") or [], purpose=ref.get("role",""), txt=entry.get("txt",""),
                                     promptVersionId=adopted["id"], promptVersion=adopted["display_no"]))
        return hits

    def adopt_image(self, episode_id, version_id, command_id, confirm=False, expected=None):
        if not confirm:
            with connect(self.db_path) as db:
                v = self._episode_image(db, episode_id, version_id)
                refs = self._image_references(db, v)
                return dict(needsConfirm=True, references=refs, fingerprint=sha256_bytes(canonical([version_id, refs])))
        with transaction(self.db_path) as db:
            def run():
                v = self._episode_image(db, episode_id, version_id)
                refs = self._image_references(db, v)
                if expected is not None and expected != sha256_bytes(canonical([version_id, refs])):
                    raise Conflict("采用提示词的引用已变化，请重新核对镜头和 TXT")
                return self._adopt_episode_image(db, episode_id, version_id, v, refs)
            payload = dict(e=episode_id, v=version_id)
            if expected is not None:
                payload["expected"] = expected
            return self._command(db, command_id, "adopt_image", payload, run)

    def _adopt_episode_image(self, db, episode_id, version_id, v, refs):
        db.execute("INSERT OR IGNORE INTO image_choice VALUES(?,?,?,?,?,?)",
                   (new_id(), episode_id, version_id, "adopted", "[]", self.clock()))
        source = f"asset:{version_id}"
        pending = {r[0] for r in db.execute(
            "SELECT target_ref FROM impact WHERE episode_id=? AND source_ref=? "
            "AND kind='image_adopted' AND state='pending'", (episode_id, source))}
        affected = dict.fromkeys("prompts:"+r["segment"] for r in refs if r["current"] != version_id)
        items = [dict(target=t, reason=f"{v['name']} 采用新版 v{v['version_no']}，需改引用并复审")
                 for t in affected if t not in pending]
        return dict(versionId=version_id, recheck=self._record_impacts(
            db, v, items, "image_adopted", source))

    # ============================================================ generation canvas (J15)
    def canvas_view(self, episode_id):
        with connect(self.db_path) as db:
            self._episode_in(db, episode_id)
            r = db.execute("SELECT doc, revision FROM canvas WHERE episode_id=?", (episode_id,)).fetchone()
            if r:
                return dict(doc=json.loads(r["doc"]), revision=r["revision"])
            prompts = self._adopted_content(db,episode_id,"prompts") or {}
        nodes,edges=[],[]
        counts={}
        def add(id,kind,column,title,data):
            ordinal=counts.get(column,0)
            nodes.append(dict(id=id,type=kind,x=270+column*330,y=70+ordinal*320,title=title,data=data))
            counts[column]=ordinal+1
        for asset in self.episode_assets(episode_id):
            if asset["kind"]=="image":
                col=2 if asset["grid"] else 0 if asset["category"]=="identity" else 1
                add(asset["id"],"lock" if asset["category"]=="identity" else "reference",col,
                    f"{asset['name']} v{asset['version_no']}",dict(assetVersionId=asset["id"]))
        image_ids={n["id"] for n in nodes}
        for sid,entry in prompts.get("segments",{}).items():
            usage="use-"+sid
            add(usage,"usage",3,sid+" 实际输入",dict(segment=sid))
            edges.extend({"from":ref["assetVersionId"],"to":usage,"kind":"input"}
                         for ref in entry.get("refs",[]) if ref.get("assetVersionId") in image_ids)
        for batch in self.episode_batches(episode_id):
            spec=batch["spec"]
            items=spec.get("items",[])
            ident="batch-"+batch["id"]
            add(ident,"generate",4,(items[0].get("target",{}).get("name") if items else None) or "图像批次",
                dict(batchId=batch["id"],provider=batch["provider"],operation=batch["operation"],size=batch["size"],
                     quality=spec.get("quality"),model=batch["model"],
                     count=max(1,len(items)),prompt=items[0].get("prompt","") if items else ""))
        return dict(doc=dict(nodes=nodes,edges=edges),revision=0)

    def save_canvas(self, episode_id, doc, expected_revision, command_id=None):
        """Layout and wiring only; generation still goes through create_batch + authorization."""
        with transaction(self.db_path) as db:
            return self._write_canvas(db, episode_id, doc, expected_revision)

    def _write_canvas(self, db, episode_id, doc, expected_revision):
        r = db.execute("SELECT revision FROM canvas WHERE episode_id=?", (episode_id,)).fetchone()
        cur = r["revision"] if r else 0
        if cur != expected_revision:
            raise Conflict("画布已在别处更新，请刷新后再改", current=cur)
        ids = {n["id"] for n in doc.get("nodes", [])}
        if any(e["from"] not in ids or e["to"] not in ids for e in doc.get("edges", [])):
            raise Rejected("连线指向不存在的节点")
        db.execute("INSERT INTO canvas VALUES(?,?,?) ON CONFLICT(episode_id) DO UPDATE SET doc=excluded.doc, "
                   "revision=excluded.revision", (episode_id, dumps(doc), cur + 1))
        return dict(revision=cur + 1)
