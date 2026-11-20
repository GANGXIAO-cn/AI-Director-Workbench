"""Independent API providers. Catalog requests only send credentials scoped to this address."""
import hashlib
import ipaddress
import json
import os
import time
from urllib.parse import urlsplit

import httpx

from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, row

CATEGORIES = ("language", "image", "vision", "video", "audio")
MAX_BYTES = 4 * 1024 * 1024


def validate_address(address, auth):
    if not isinstance(address, str) or len(address) > 2048 or any(c.isspace() or ord(c) < 32 or c in "\\?#" for c in address):
        raise Rejected("接口地址须为完整的 HTTP(S) 基址，不含查询、凭证或片段")
    try:
        parts = urlsplit(address)
        host = parts.hostname
        _ = parts.port
    except ValueError:
        raise Rejected("接口地址或端口无效")
    if not host or parts.username is not None or parts.scheme not in ("http", "https"):
        raise Rejected("请输入完整的 HTTP(S) 基址，不含账号和密码")
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    if not local and (parts.scheme != "https" or auth == "local"):
        raise Rejected("远程服务须使用 HTTPS 与 Bearer 鉴权；本地免鉴权限回环地址")
    return address.rstrip("/")


def credential_var(p):
    # Changing the address or auth creates a fresh binding, including after a round trip.
    scope = dumps([p["id"], p["binding"], p["base_url"]]).encode()
    return "ADW_PROVIDER_KEY_" + hashlib.sha256(scope).hexdigest()[:32].upper()


def catalog_ids(base_url, key, all_pages):
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if key:
        headers["Authorization"] = "Bearer " + key
    ids = {}
    cursors = set()
    remaining = MAX_BYTES
    deadline = time.monotonic() + 30
    params = {}
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=6) as client:
            for _ in range(100):
                with client.stream("GET", base_url + "/models", params=params, headers=headers) as response:
                    if response.status_code != 200:
                        raise Rejected(f"模型目录返回 HTTP {response.status_code}")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise Rejected("模型目录未按请求返回未压缩数据")
                    body = bytearray()
                    for chunk in response.iter_raw():
                        remaining -= len(chunk)
                        if remaining < 0 or time.monotonic() > deadline:
                            raise Rejected("模型目录超过大小或时间上限")
                        body.extend(chunk)
                page = json.loads(body)
                if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                    raise Rejected("模型目录应包含 data 列表")
                for item in page["data"]:
                    model = item.get("id") if isinstance(item, dict) else None
                    if not isinstance(model, str) or not model.strip() or len(model) > 512 or any(ord(c) < 32 for c in model) or (key and key in model):
                        raise Rejected("模型目录包含无效的型号 ID")
                    ids[model] = None
                if not all_pages or not page.get("has_more"):
                    return list(ids)
                cursor = page.get("last_id") or (page["data"][-1]["id"] if page["data"] else None)
                if not isinstance(cursor, str) or cursor not in ids or cursor in cursors:
                    raise Rejected("模型目录分页游标缺失或重复")
                cursors.add(cursor)
                params = {"after": cursor}
    except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
        raise Rejected("无法读取有效模型目录，请核对基址、网络和返回格式") from None
    raise Rejected("模型目录超过分页上限")


class ProvidersMixin:
    def register_api_executors(self):
        from .executors.chat_api import ChatAPIExecutor
        owner = getattr(self, 'parent', self)
        with connect(owner.db_path) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM api_provider")]
        for ident in ids:
            name = "api:" + ident
            if name not in self.executors:
                self.executors[name] = ChatAPIExecutor(owner.db_path, ident, owner.credentials)

    def api_providers(self):
        with connect(self.db_path) as db:
            providers = []
            for p in db.execute("SELECT * FROM api_provider ORDER BY name,id"):
                models, labels = json.loads(p["models"]), json.loads(p["labels"])
                env_name = credential_var(p)
                providers.append(dict(id=p["id"], name=p["name"], address=p["base_url"], auth=p["auth"],
                                      revision=p["revision"], envName=env_name,
                                      keyConfigured=p["auth"] == "bearer" and bool(self.credentials.get(env_name) or os.environ.get(env_name)),
                                      models=[dict(id=m, categories=labels.get(m, [])) for m in models],
                                      checkedAt=p["checked_at"], syncedAt=p["synced_at"], diagnostic=p["diagnostic"]))
        return providers

    def save_provider(self, name, address, auth, provider_id=None, revision=None, command_id=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or auth not in ("local", "bearer"):
            raise Rejected("请填写服务商名称并选择鉴权方式")
        address = validate_address(address, auth)
        with transaction(self.db_path) as db:
            def save():
                if not provider_id:
                    pid = new_id()
                    db.execute("INSERT INTO api_provider(id,name,base_url,auth,binding) VALUES(?,?,?,?,?)",
                               (pid, name.strip(), address, auth, new_id()))
                    return {"id": pid}
                p = row(db, "SELECT * FROM api_provider WHERE id=?", provider_id)
                if p["revision"] != revision:
                    raise Conflict("服务商配置已更新，请刷新")
                if (p["base_url"], p["auth"]) != (address, auth):
                    db.execute("UPDATE api_provider SET binding=?,models='[]',labels='{}',checked_at=NULL,synced_at=NULL,diagnostic=NULL WHERE id=?",
                               (new_id(), provider_id))
                    self.credentials.forget(credential_var(p))
                db.execute("UPDATE api_provider SET name=?,base_url=?,auth=?,revision=revision+1 WHERE id=?",
                           (name.strip(), address, auth, provider_id))
                return {"id": provider_id}
            result = self._command(db, command_id, "save_provider", [provider_id, revision, name, address, auth], save)
        self.register_api_executors()
        return result

    def set_provider_key(self, provider_id, revision, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 8192 or not key.isascii() or not all(33 <= ord(c) <= 126 for c in key):
            raise Rejected("请填写有效的 Bearer 密钥")
        with transaction(self.db_path) as db:
            p = row(db, "SELECT * FROM api_provider WHERE id=?", provider_id)
            if p["revision"] != revision or p["auth"] != "bearer":
                raise Conflict("服务商配置已变更，请刷新")
            self.credentials.set(credential_var(p), key)
            db.execute("UPDATE api_provider SET revision=revision+1,checked_at=NULL,diagnostic=NULL WHERE id=?", (provider_id,))
        return {"configured": True, "savedToConfig": True}

    def read_provider_models(self, provider_id, revision, sync=False):
        with transaction(self.db_path) as db:
            p = row(db, "SELECT * FROM api_provider WHERE id=?", provider_id)
            if p["revision"] != revision:
                raise Conflict("服务商配置已更新，请刷新")
            seq = p["probe_seq"] + 1
            db.execute("UPDATE api_provider SET probe_seq=? WHERE id=?", (seq, provider_id))
            key = None
            if p["auth"] == "bearer":
                key = self.credentials.get(credential_var(p)) or os.environ.get(credential_var(p))
        try:
            if p["auth"] == "bearer" and not key:
                raise Rejected("密钥未配置")
            models = catalog_ids(p["base_url"], key, sync)
            diagnostic = None
        except Rejected as exc:
            models, diagnostic = [], str(exc)
        with transaction(self.db_path) as db:
            stamp = self.clock()
            updated = db.execute("UPDATE api_provider SET checked_at=?,diagnostic=? WHERE id=? AND revision=? AND probe_seq=?",
                                 (stamp, diagnostic, provider_id, revision, seq))
            if not updated.rowcount:
                raise Conflict("检测期间配置或请求已更新，请刷新")
            if sync and diagnostic is None:
                db.execute("UPDATE api_provider SET models=?,synced_at=? WHERE id=?", (dumps(models), stamp, provider_id))
        return {"ok": diagnostic is None, "diagnostic": diagnostic}

    def label_provider_model(self, provider_id, revision, model, categories, prior):
        if not isinstance(categories, list) or any(c not in CATEGORIES for c in categories):
            raise Rejected("未知模型分类")
        with transaction(self.db_path) as db:
            p = row(db, "SELECT * FROM api_provider WHERE id=?", provider_id)
            labels = json.loads(p["labels"])
            if revision != p["revision"] or model not in json.loads(p["models"]) or labels.get(model, []) != prior:
                raise Conflict("型号或分类已更新，请刷新")
            labels[model] = [category for category in CATEGORIES if category in categories]
            db.execute("UPDATE api_provider SET labels=? WHERE id=?", (dumps(labels), provider_id))
        return {"ok": True}
