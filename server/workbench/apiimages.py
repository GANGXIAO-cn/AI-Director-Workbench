"""Direct OpenAI Images API; shared concurrency limit and recoverable local receipts."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import threading

import httpx

from .errors import Rejected
from .images import ImageOutcome, image_size
from .storage import canonical, write_atomic
from .executors.cli import local_proxy_env


class OpenAIImageAPIProvider:
    remote_status = False
    name = "openai-image-api"
    currency = "USD"
    price_cents = None
    verified = "pending_verification"
    capabilities = dict(label="OpenAI 图片 API（直连）", operations=["generate", "edit"],
                        sizes=["1024x1024", "1024x1536", "1536x1024", "864x1536", "1536x864"],
                        quality=["low", "medium", "high", "auto"],
                        models=["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"])

    def __init__(self, root, credential_path=None, transport=None):
        self.root = Path(root)
        self.credential_path = Path(credential_path or Path.home() / "Documents/自媒体 2/tools/openai-image-mcp/.env.local")
        self.transport = transport
        self.condition = threading.Condition()
        self.active = 0
        self.max_concurrency = 1
        config = self.root / "settings.json"
        if config.is_file():
            self.max_concurrency = json.loads(config.read_text())["maxConcurrency"]

    def _credentials(self):
        names = ("OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID")
        config = {key: os.environ.get(key, "").strip() for key in names}
        # Match the existing MCP configuration precedence without copying its secrets.
        if self.credential_path.is_file():
            for line in self.credential_path.read_text().splitlines():
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() in names:
                    config[key.strip()] = value.strip().strip("\"'")
        return config

    def settings(self):
        with self.condition:
            return dict(maxConcurrency=self.max_concurrency, activeRequests=self.active,
                        keyConfigured=bool(self._credentials()["OPENAI_API_KEY"]))

    def save_settings(self, max_concurrency):
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 64:
            raise Rejected("最大并发须为 1–64 的整数")
        with self.condition:
            write_atomic(self.root / "settings.json", canonical(dict(maxConcurrency=max_concurrency)))
            self.max_concurrency = max_concurrency
            self.condition.notify_all()
        return self.settings()

    @contextmanager
    def slot(self):
        with self.condition:
            self.condition.wait_for(lambda: self.active < self.max_concurrency)
            self.active += 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def _job(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
            raise ValueError("无效的图片请求编号")
        return self.root / request_id

    def inspect(self, request_id):
        job = self._job(request_id)
        if not (job / "receipt.json").is_file():
            return None
        receipt = json.loads((job / "receipt.json").read_text())
        if (job / "result.png").is_file():
            return ImageOutcome("succeeded", (job / "result.png").read_bytes(), receipt.get("providerRequestId"))
        return ImageOutcome(receipt["state"], provider_request_id=receipt.get("providerRequestId"),
                            error=receipt.get("error", "请求结果待核对"))

    def submit(self, req):
        with self.slot():
            return self._submit(req)

    def _submit(self, req):
        config = self._credentials()
        if not config["OPENAI_API_KEY"]:
            return ImageOutcome("failed", error="未配置 OpenAI API Key")
        job = self._job(req["local_request_id"])
        try:
            job.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return self.inspect(req["local_request_id"]) or ImageOutcome("unknown", error="原请求正在执行")
        receipt = dict(state="unknown")
        write_atomic(job / "receipt.json", canonical(receipt))
        headers = {"Authorization": "Bearer " + config["OPENAI_API_KEY"]}
        for key, header in (("OPENAI_ORG_ID", "OpenAI-Organization"), ("OPENAI_PROJECT_ID", "OpenAI-Project")):
            if config[key]:
                headers[header] = config[key]
        payload = {key: req[key] for key in ("model", "prompt", "size", "quality") if req.get(key)}
        payload.update(n=1, output_format="png")
        request_id = None
        sent = False
        try:
            inputs = []
            for path in req.get("inputs", []):
                data = Path(path).read_bytes()
                png = data.startswith(b"\x89PNG")
                inputs.append(("image[]", ("reference.png" if png else "reference.jpg", data,
                                           "image/png" if png else "image/jpeg")))
            if req["operation"] == "edit" and not inputs:
                raise ValueError("编辑图片需要参考图")
            proxy = local_proxy_env().get("HTTPS_PROXY") if self.transport is None else None
            with httpx.Client(timeout=600, transport=self.transport, proxy=proxy) as client:
                url = "https://api.openai.com/v1/images/" + ("edits" if inputs else "generations")
                sent = True
                response = client.post(url, headers=headers, data=payload, files=inputs) if inputs else client.post(url, headers=headers, json=payload)
            request_id = response.headers.get("x-request-id")
            if response.is_error:
                state = "unknown" if response.status_code >= 500 else "failed"
                outcome = ImageOutcome(state, provider_request_id=request_id,
                                       error=f"OpenAI 图片 API 返回 HTTP {response.status_code}")
            else:
                data = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)
                if not image_size(data):
                    raise ValueError("返回图片无法解码")
                write_atomic(job / "result.png", data)
                outcome = ImageOutcome("succeeded", data, request_id)
        except httpx.TransportError:
            outcome = ImageOutcome("unknown", provider_request_id=request_id, error="API 连接中断或超时，请核对原请求")
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            outcome = ImageOutcome("unknown" if sent else "failed", provider_request_id=request_id,
                                   error="API 返回结果无法读取" if sent else "图片输入无法读取，未发送请求")
        receipt.update(state=outcome.state, providerRequestId=outcome.provider_request_id, error=outcome.error)
        write_atomic(job / "receipt.json", canonical(receipt))
        return outcome
