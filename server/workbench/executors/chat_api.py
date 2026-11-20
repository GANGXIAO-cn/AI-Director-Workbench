"""A selected OpenAI-compatible chat endpoint, with one fresh conversation per call."""
import base64
import json
import mimetypes
import os
from pathlib import Path

import httpx

from .base import Executor, ExecOutcome
from ..db import connect
from ..providers import credential_var, validate_address


class ChatAPIExecutor(Executor):
    isolation = "session"
    reads_images = True
    strict_schema = False
    union_schema = False

    def __init__(self, db_path, provider_id, keys):
        self.db_path = db_path
        self.provider_id = provider_id
        self.name = "api:" + provider_id
        self.keys = keys

    def config(self):
        with connect(self.db_path) as db:
            return dict(db.execute("SELECT * FROM api_provider WHERE id=?", (self.provider_id,)).fetchone())

    def probe(self):
        config = self.config()
        authenticated = config["auth"] == "local" or bool(self.key(config))
        return dict(provider=self.name, status="configured", authenticated=None if authenticated else False)

    def key(self, config):
        key_name = credential_var(config)
        return self.keys.get(key_name) or os.environ.get(key_name)

    def start(self, request):
        config = self.config()
        if request.connection.get("binding", config["binding"]) != config["binding"]:
            return ExecOutcome(False, error_class="unavailable", diagnostic="服务地址已变化，请重新选择当前连接")
        key = self.key(config) if config["auth"] == "bearer" else None
        if config["auth"] == "bearer" and not key:
            return ExecOutcome(False, error_class="authentication", diagnostic="所选 API 尚未配置凭证")
        if not request.model:
            return ExecOutcome(False, error_class="unavailable", diagnostic="请指定 API 型号")
        prompt = request.prompt
        if request.schema:
            prompt += "\nReturn one JSON object matching this schema:\n" + json.dumps(request.schema, ensure_ascii=False)
        content = prompt
        if request.images:
            content = [{"type": "text", "text": prompt}]
            for path in map(Path, request.images):
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        payload = dict(model=request.model, messages=[dict(role="user", content=content)])
        if request.effort:
            payload["reasoning_effort"] = request.effort
        url = validate_address(config["base_url"], config["auth"]) + "/chat/completions"
        headers = {"Authorization": "Bearer " + key} if key else {}
        try:
            with httpx.Client(follow_redirects=False, trust_env=False, timeout=request.timeout) as client:
                response = client.post(url, headers=headers, json=payload)
            if response.status_code >= 300:
                code = response.status_code
                reason = {401:"authentication", 403:"authentication", 429:"quota"}.get(code, "network" if code >= 500 else "unavailable")
                return ExecOutcome(False, error_class=reason, diagnostic=f"所选模型请求返回 HTTP {code}")
            body = response.json()
            raw = body["choices"][0]["message"]["content"].strip()
            if raw.startswith("```") and raw.endswith("```"):
                raw = raw.split("\n", 1)[-1][:-3].strip()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("需要 JSON 对象")
            return ExecOutcome(True, structured=result, reported_model=body.get("model"))
        except httpx.TimeoutException:
            return ExecOutcome(False, error_class="timeout", diagnostic="所选 API 未在期限内返回")
        except httpx.HTTPError:
            return ExecOutcome(False, error_class="network", diagnostic="所选 API 连接失败")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return ExecOutcome(False, error_class="schema", diagnostic="所选 API 回包缺少有效的 JSON 对象")
