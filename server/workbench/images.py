"""Assets, reuse library and paid image batches.

Paid-batch guard: a batch is authorised against a summary hash; each request is persisted before it
is sent; a request whose outcome is unknown is never re-sent automatically.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import zlib

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


def image_size(data: bytes):
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":   # JPEG: walk SOF markers
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + length
    return None


# ---------------------------------------------------------------- providers
@dataclass
class ImageOutcome:
    state: str                 # succeeded | unknown | failed
    data: bytes | None = None
    provider_request_id: str | None = None
    error: str = ""


class FakeImageProvider:
    """Scripted outcomes for tests: 'ok' | 'timeout' | 'disconnect' | 'duplicate' | 'insufficient'."""

    def __init__(self, name="fake-image", script=None, price_cents=4):
        self.name, self.script, self.price_cents = name, list(script or []), price_cents
        self.sent = 0
        self.verified = "simulated"
        self.capabilities = dict(operations=["generate", "edit"], sizes=["64x64", "96x64", "1024x1024", "864x1536"],
                                 quality=["low", "medium", "high"])
        self.completed = {}

    def submit(self, req):
        self.sent += 1
        step = self.script.pop(0) if self.script else "ok"
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
    """Local stdio MCP server ~/Documents/自媒体 2/tools/openai-image-mcp (generate_image / edit_image /
    image_job_status). Each call is paid. Not exercised by tests; status is 'pending_verification' in-app."""

    name = "openai-image-mcp"
    price_cents = None
    verified = "pending_verification"
    capabilities = dict(operations=["generate", "edit"], sizes=["1024x1024", "864x1536", "1536x864"],
                        quality=["low", "medium", "high"])

    def __init__(self, root=Path.home() / "Documents/自媒体 2/tools/openai-image-mcp", poll_seconds=10, timeout=600):
        self.root, self.poll, self.timeout = Path(root), poll_seconds, timeout

    def _call(self, tool, arguments):
        python = self.root / ".venv/bin/python"
        proc = subprocess.Popen([str(python), "server.py"], cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        def send(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        try:
            send(dict(jsonrpc="2.0", id=1, method="initialize",
                      params=dict(protocolVersion="2025-06-18", capabilities={},
                                  clientInfo=dict(name="ai-director-workbench", version="1"))))
            proc.stdout.readline()
            send(dict(jsonrpc="2.0", method="notifications/initialized"))
            send(dict(jsonrpc="2.0", id=2, method="tools/call", params=dict(name=tool, arguments=arguments)))
            reply = json.loads(proc.stdout.readline())
        finally:
            proc.stdin.close()
            proc.terminate()
        content = reply.get("result", {}).get("structuredContent") or {}
        if not content:
            texts = [c.get("text", "") for c in reply.get("result", {}).get("content", []) if c.get("type") == "text"]
            content = json.loads(texts[0]) if texts else {}
        return content

    def status(self):
        return self._call("image_service_status", {})

    def submit(self, req):
        args = dict(prompt=req["prompt"], request_id=req["local_request_id"], size=req["size"])
        if req.get("model"):
            args["model"] = req["model"]
        if req.get("quality"):
            args["quality"] = req["quality"]
        tool = "generate_image"
        if req["operation"] == "edit":
            tool, args["image_paths"] = "edit_image", req["inputs"]
        first = self._call(tool, args)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            got = self.inspect(req["local_request_id"])
            if got and got.state != "unknown":
                return got
            time.sleep(self.poll)
        return ImageOutcome("unknown", provider_request_id=first.get("provider_request_id"), error="timeout")

    def inspect(self, local_request_id):
        info = self._call("image_job_status", dict(request_id=local_request_id))
        state = info.get("status")
        if state == "completed" and info.get("path"):
            return ImageOutcome("succeeded", Path(info["path"]).read_bytes(), info.get("provider_request_id"))
        if state in ("failed",):
            return ImageOutcome("failed", error=str(info.get("error")))
        return ImageOutcome("unknown", provider_request_id=info.get("provider_request_id"), error=str(state))


class ImagesMixin:
    # ============================================================ assets
    def register_asset(self, project_id, name, data: bytes, category, kind="image", episode_id=None, roles=(),
                       origin="import", grid=False, cells=None, asset_id=None, source_version_id=None,
                       changed_cells=None, note="", db=None):
        def apply(db):
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

    def asset_version(self, version_id, db=None):
        def get(db):
            v = loads(row(db, "SELECT av.*, a.name, a.kind, a.category, a.episode_id, a.project_id, a.roles FROM asset_version av "
                             "JOIN asset a ON a.id=av.asset_id WHERE av.id=?", version_id, missing="素材版本不存在"),
                      "cells", "changed_cells", "roles")
            return v
        if db is not None:
            return get(db)
        with connect(self.db_path) as db2:
            return get(db2)

    def asset_bytes(self, version_id):
        v = self.asset_version(version_id)
        return self.store.read_blob(v["sha256"])

    def episode_assets(self, episode_id):
        with connect(self.db_path) as db:
            rows = db.execute("SELECT av.id FROM asset_version av JOIN asset a ON a.id=av.asset_id WHERE a.episode_id=? "
                              "ORDER BY a.name, av.version_no", (episode_id,)).fetchall()
            out = [self.asset_version(r[0], db) for r in rows]
            marks = {}
            for r in db.execute("SELECT asset_version_id, mark FROM image_choice WHERE episode_id=?", (episode_id,)):
                marks.setdefault(r[0], []).append(r[1])
        for v in out:
            v["marks"] = marks.get(v["id"], [])
        return out

    # ============================================================ library
    def submit_reuse(self, version_id, usage, limits="", observation="", aspect=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                v = self.asset_version(version_id, db)
                if v["grid"]:
                    raise Rejected("九宫格只归本集，不能进入复用库")
                eid = new_id()
                db.execute("INSERT INTO library_entry VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (eid, v["project_id"], version_id, usage, aspect, limits, observation, "pending",
                            v["episode_id"], self.clock()))
                return dict(entryId=eid, status="pending")
            return self._command(db, command_id, "reuse", dict(v=version_id, u=usage, l=limits), run)

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
            rows = db.execute("SELECT l.*, a.name, a.kind, a.category, av.sha256, av.version_no, av.width, av.height "
                              "FROM library_entry l JOIN asset_version av ON av.id=l.asset_version_id "
                              "JOIN asset a ON a.id=av.asset_id WHERE l.project_id=? ORDER BY l.created_at", (project_id,))
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
            return self._command(db, command_id, "lib_copy", dict(e=entry_id, ep=episode_id), run)

    # ============================================================ image batches
    def image_providers(self):
        return {name: dict(verified=p.verified, capabilities=p.capabilities, priceCents=p.price_cents)
                for name, p in self.image_providers_map.items()}

    def create_batch(self, episode_id, provider, operation, items, size, model=None, quality=None, budget_cents=None,
                     command_id=None):
        """items: [{prompt, inputs:[assetVersionId], target:{name, category, assetId?}, segments:[..], cells:[..]}]"""
        if provider not in self.image_providers_map:
            raise Rejected("未配置的图像通道")
        prov = self.image_providers_map[provider]
        if operation not in prov.capabilities["operations"]:
            raise Rejected(f"{provider} 不支持 {operation}")
        if size not in prov.capabilities["sizes"]:
            raise Rejected(f"{provider} 不支持尺寸 {size}")
        blocked = self.blocked_segments(episode_id)
        hits = sorted({s for it in items for s in it.get("segments", []) if s in blocked})
        if hits:
            raise Rejected("依赖未确认空间锁定的段不能出图：" + "、".join(hits), detail={s: blocked[s] for s in hits})
        with transaction(self.db_path) as db:
            inputs = [[self.asset_version(i, db)["sha256"] for i in it.get("inputs", [])] for it in items]
            spec = dict(items=items, inputs=inputs, model=model, quality=quality)
            input_hash = sha256_bytes(canonical(dict(spec, operation=operation, size=size)))
            estimate = prov.price_cents * len(items) if prov.price_cents is not None else None
            summary = dict(provider=provider, verified=prov.verified, model=model, operation=operation, count=len(items),
                           size=size, inputs=inputs, budget_cents=budget_cents, estimate_cents=estimate)
            summary_hash = sha256_bytes(canonical(summary))
            def run():
                existing = db.execute("SELECT id FROM image_batch WHERE episode_id=? AND input_hash=? AND provider=?",
                                      (episode_id, input_hash, provider)).fetchone()
                if existing:
                    raise Conflict("相同输入的批次已存在，不重复提交", current=existing[0])
                bid = new_id()
                db.execute("INSERT INTO image_batch VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (bid, episode_id, input_hash, provider, model, operation, size, len(items), dumps(spec),
                            budget_cents, estimate, "estimated" if estimate is not None else "unknown", summary_hash, None,
                            "awaiting_authorization", self.clock()))
                for n, it in enumerate(items, 1):
                    db.execute("INSERT INTO image_request(id,batch_id,ordinal,local_request_id,state) VALUES(?,?,?,?,?)",
                               (new_id(), bid, n, f"adw-{bid[:8]}-{n:02d}", "pending"))
                return dict(batchId=bid, summary=summary, summaryHash=summary_hash)
            return self._command(db, command_id, "batch", dict(e=episode_id, h=input_hash, p=provider), run)

    def authorize_batch(self, batch_id, summary_hash, command_id, accept_unknown_cost=False, budget_cents=None):
        with transaction(self.db_path) as db:
            def run():
                b = row(db, "SELECT * FROM image_batch WHERE id=?", batch_id)
                if b["summary_hash"] != summary_hash:
                    raise Rejected("授权摘要与批次不符，请重新核对")
                if b["state"] != "awaiting_authorization":
                    raise Rejected("批次不在待授权状态")
                budget = budget_cents if budget_cents is not None else b["budget_cents"]
                if budget is None:
                    raise Rejected("付费确认须填写预算")
                if b["estimate_cents"] is None and not accept_unknown_cost:
                    raise Rejected("费用未知，须明确接受后才能提交")
                if b["estimate_cents"] is not None and b["estimate_cents"] > budget:
                    raise Rejected("估算超出预算：缩小批次或换配置")
                auth = dict(summaryHash=summary_hash, budget_cents=budget, acceptUnknownCost=accept_unknown_cost,
                            at=self.clock(), command=command_id)
                db.execute("UPDATE image_batch SET state='authorized', authorization=?, budget_cents=? WHERE id=?",
                           (dumps(auth), budget, batch_id))
                return dict(batchId=batch_id, state="authorized")
            return self._command(db, command_id, "authorize", dict(b=batch_id, h=summary_hash), run)

    def cancel_batch(self, batch_id, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                b = row(db, "SELECT state FROM image_batch WHERE id=?", batch_id)
                if b["state"] not in ("awaiting_authorization", "authorized"):
                    raise Rejected("已执行的批次不能取消，只能核对")
                db.execute("UPDATE image_batch SET state='cancelled' WHERE id=?", (batch_id,))
                return dict(batchId=batch_id, state="cancelled")
            return self._command(db, command_id, "cancel_batch", dict(b=batch_id), run)

    def execute_batch(self, batch_id):
        """Send only requests that were never sent. Unknown / submitted ones wait for reconciliation."""
        with connect(self.db_path) as db:
            b = row(db, "SELECT * FROM image_batch WHERE id=?", batch_id)
        if b["state"] not in ("authorized", "partial"):
            raise Rejected("批次未授权或已结束")
        prov = self.image_providers_map[b["provider"]]
        spec = json.loads(b["spec"])
        while True:
            with transaction(self.db_path) as db:
                req = db.execute("SELECT * FROM image_request WHERE batch_id=? AND state='pending' ORDER BY ordinal LIMIT 1",
                                 (batch_id,)).fetchone()
                if not req:
                    break
                db.execute("UPDATE image_request SET state='submitted', submitted_at=? WHERE id=?", (self.clock(), req["id"]))
            item = spec["items"][(req["replaces_id"] and self._ordinal_of(req["replaces_id"]) or req["ordinal"]) - 1]
            inputs = [str(self.store.blob_path(self.asset_version(i)["sha256"])) for i in item.get("inputs", [])]
            try:
                out = prov.submit(dict(local_request_id=req["local_request_id"], operation=b["operation"],
                                       prompt=item["prompt"], inputs=inputs, size=b["size"], model=b["model"],
                                       quality=spec.get("quality")))
            except Exception as exc:   # transport error after sending: outcome unknown, never re-sent
                out = ImageOutcome("unknown", error=repr(exc)[:300])
            self._land(batch_id, req["id"], out, b, item)
        return self.batch_view(batch_id)

    def _ordinal_of(self, request_id):
        with connect(self.db_path) as db:
            return db.execute("SELECT ordinal FROM image_request WHERE id=?", (request_id,)).fetchone()[0]

    def _land(self, batch_id, request_id, out, b, item):
        with transaction(self.db_path) as db:
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
                    db.execute("UPDATE image_request SET state='succeeded', provider_request_id=?, asset_version_id=?, sha256=?, "
                               "ended_at=? WHERE id=?", (out.provider_request_id, res["versionId"], res["sha256"],
                                                         self.clock(), request_id))
            else:
                db.execute("UPDATE image_request SET state=?, error=?, provider_request_id=?, ended_at=? WHERE id=?",
                           ("unknown" if out.state == "unknown" else "failed", out.error, out.provider_request_id,
                            self.clock(), request_id))
            self._batch_state(db, batch_id)

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
            item = json.loads(b["spec"])["items"][req["ordinal"] - 1]
            self._land(b["id"], request_id, ImageOutcome("succeeded", data, req["provider_request_id"]), b, item)
        elif finding == "not_executed":
            with transaction(self.db_path) as db:
                db.execute("UPDATE image_request SET state='confirmed_not_executed', ended_at=? WHERE id=?",
                           (self.clock(), request_id))
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
                if not b["authorization"]:
                    raise Rejected("批次未授权")
                n = db.execute("SELECT max(ordinal)+1 FROM image_request WHERE batch_id=?", (b["id"],)).fetchone()[0]
                nid = new_id()
                db.execute("INSERT INTO image_request(id,batch_id,ordinal,local_request_id,state,replaces_id) "
                           "VALUES(?,?,?,?,?,?)", (nid, b["id"], n, f"adw-{b['id'][:8]}-{n:02d}", "pending", request_id))
                db.execute("UPDATE image_batch SET state='partial' WHERE id=?", (b["id"],))
                return dict(requestId=nid)
            return self._command(db, command_id, "resubmit", dict(r=request_id), run)

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
            b = loads(row(db, "SELECT * FROM image_batch WHERE id=?", batch_id), "spec", "authorization")
            b["requests"] = [dict(r) for r in db.execute("SELECT * FROM image_request WHERE batch_id=? ORDER BY ordinal",
                                                         (batch_id,))]
        return b

    def episode_batches(self, episode_id):
        with connect(self.db_path) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM image_batch WHERE episode_id=? ORDER BY created_at", (episode_id,))]
        return [self.batch_view(i) for i in ids]

    # ============================================================ choice, redraw, adoption
    def mark_image(self, episode_id, version_id, mark, shot_ids=(), command_id=None):
        with transaction(self.db_path) as db:
            def run():
                self.asset_version(version_id, db)
                db.execute("INSERT OR IGNORE INTO image_choice VALUES(?,?,?,?,?,?)",
                           (new_id(), episode_id, version_id, mark, dumps(list(shot_ids)), self.clock()))
                return dict(ok=True)
            return self._command(db, command_id, "mark", dict(e=episode_id, v=version_id, m=mark), run)

    def redraw_cells(self, episode_id, base_version_id, cells, reference_version_ids, prompt, provider, size,
                     budget_cents=None, command_id=None):
        """修一格: edit A's cells using B as a drawing reference. The result is a new candidate of asset A."""
        base = self.asset_version(base_version_id)
        if not base["grid"]:
            raise Rejected("局部修格只适用于九宫格")
        if not cells or any(not 1 <= c <= 9 for c in cells):
            raise Rejected("格号须为 1–9")
        item = dict(prompt=prompt, inputs=[base_version_id, *reference_version_ids], cells=sorted(cells),
                    target=dict(assetId=base["asset_id"], name=base["name"], category=base["category"], grid=True,
                                cellsMeta=base["cells"], roles=base["roles"]))
        return self.create_batch(episode_id, provider, "edit", [item], size, budget_cents=budget_cents, command_id=command_id)

    def compare_cells(self, new_version_id):
        v = self.asset_version(new_version_id)
        changed = v["changed_cells"] or []
        return dict(base=v["source_version_id"], changed=changed, unchanged=[c for c in range(1, 10) if c not in changed])

    def image_references(self, version_id):
        """Segments whose adopted prompts reference any version of the same asset."""
        with connect(self.db_path) as db:
            v = self.asset_version(version_id, db)
            versions = {r[0] for r in db.execute("SELECT id FROM asset_version WHERE asset_id=?", (v["asset_id"],))}
            prompts_ = self._adopted_content(db, v["episode_id"], "prompts") or {}
        hits = []
        for seg, entry in (prompts_.get("segments") or {}).items():
            for ref in entry.get("refs", []):
                if ref.get("assetVersionId") in versions:
                    hits.append(dict(segment=seg, label=ref["label"], current=ref["assetVersionId"]))
        return hits

    def adopt_image(self, episode_id, version_id, command_id, confirm=False):
        refs = self.image_references(version_id)
        if not confirm:
            return dict(needsConfirm=True, references=refs)
        with transaction(self.db_path) as db:
            def run():
                v = self.asset_version(version_id, db)
                db.execute("INSERT OR IGNORE INTO image_choice VALUES(?,?,?,?,?,?)",
                           (new_id(), episode_id, version_id, "adopted", "[]", self.clock()))
                ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
                items = [dict(target=f"prompts:{r['segment']}", reason=f"{v['name']} 采用新版 v{v['version_no']}，需改引用并复审")
                         for r in refs if r["current"] != version_id]
                return dict(versionId=version_id, recheck=self._record_impacts(
                    db, dict(project_id=ep["project_id"], episode_id=ep["id"]), items, "image_adopted", f"asset:{version_id}"))
            return self._command(db, command_id, "adopt_image", dict(e=episode_id, v=version_id), run)
