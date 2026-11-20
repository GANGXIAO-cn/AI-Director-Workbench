"""Native Codex app-server image generation, invoked by the workbench batch worker.

Protocol is taken from the installed Codex app-server JSON Schema. Each request
has its own persistent profile and receipt; uncertain requests are never resent.
No host skills, MCP, plugins, shell or alternate model is involved.
"""
import base64
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time

from .executors.cli import local_proxy_env, redact
from .images import ImageOutcome, image_size
from .storage import canonical, sha256_bytes, write_atomic


class CodexImageProvider:
    remote_status = False
    name = "codex-native"
    currency = "Codex额度"
    billing_mode = "subscription_usage"
    price_cents = None
    verified = "pending_verification"
    capabilities = dict(operations=["generate", "edit"], sizes=["1024x1536", "1536x1024", "1024x1024"],
                        quality=["auto"], models=["gpt-6.1-sol"],
                        billingMode="subscription_usage", label="Codex 内置生图")

    def __init__(self, root, binary=None, timeout=600):
        self.root = Path(root).resolve()
        from .projectops import text_catalog
        models = [m['id'] for m in text_catalog()['codex']['models'] if m.get('id')]
        self.capabilities = dict(type(self).capabilities, models=list(dict.fromkeys([*models, "gpt-6.1-sol"])))
        self.configured = binary
        self.timeout = timeout

    def _job(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
            raise ValueError("无效的图像请求编号")
        return self.root / request_id

    def inspect(self, request_id):
        job = self._job(request_id)
        receipt = job / "receipt.json"
        if not receipt.is_file():
            return None
        entry = json.loads(receipt.read_text())
        if entry.get("state") == "succeeded":
            data = (job / "result.png").read_bytes()
            if sha256_bytes(data) != entry["sha256"]:
                return ImageOutcome("unknown", error="Codex图像回执指纹不符")
            return ImageOutcome("succeeded", data, entry.get("itemId"))
        return ImageOutcome(entry.get("state", "unknown"), error=entry.get("error", "等待核对Codex原请求"))

    def submit(self, req):
        if not req.get("model"):
            return ImageOutcome("failed", error="请选择 Codex 生图型号")
        binary = self.configured or shutil.which("codex")
        if not binary:
            return ImageOutcome("failed", error="未找到本机Codex")
        job = self._job(req["local_request_id"])
        if job.exists():
            saved = job / "receipt.json"
            if saved.is_file() and json.loads(saved.read_text()).get("inputHash") != sha256_bytes(canonical(req)):
                return ImageOutcome("failed", error="本请求编号已绑定其他输入或型号")
            return self.inspect(req["local_request_id"]) or ImageOutcome("unknown", error="原请求已创建，须先核对")
        job.mkdir(parents=True)
        profile = job / "profile"
        profile.mkdir(mode=0o700)
        login = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
        if not login.is_file():
            return ImageOutcome("failed", error="本机Codex尚未登录")
        (profile / "auth.json").symlink_to(login.resolve())
        work = job / "work"
        work.mkdir()
        receipt = dict(state="unknown", requestId=req["local_request_id"], model=req["model"],
                       inputHash=sha256_bytes(canonical(req)), nativeTool="imageGeneration", events=[])
        write_atomic(job / "receipt.json", canonical(receipt))
        env = {k:os.environ[k] for k in ("PATH", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "SHELL") if k in os.environ}
        env.update(local_proxy_env(), HOME=str(job), CODEX_HOME=str(profile))
        config = {"project_doc_max_bytes":0,
                  "features.image_generation":True, "features.skip_host_skill_discovery":True,
                  "features.shell_tool":False, "features.memories":False, "features.apps":False,
                  "features.plugins":False, "features.multi_agent":False, "features.skill_search":False,
                  "web_search":"disabled"}
        args = [str(binary), "app-server", "--stdio"]
        for key, value in config.items():
            args += ["-c", key + "=" + json.dumps(value)]
        messages = queue.Queue()
        diagnostics = []
        proc = None
        sent_turn = False
        def record(state, error="", **extra):
            receipt.update(state=state, **extra)
            if error:
                receipt["error"] = redact(error)
            write_atomic(job / "receipt.json", canonical(receipt))
            return ImageOutcome(state, provider_request_id=receipt.get("itemId"), error=receipt.get("error", ""))
        try:
            proc = subprocess.Popen(args, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, bufsize=1)
            def drain_stdout():
                for line in proc.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        continue
                messages.put(None)
            def drain_stderr():
                for line in proc.stderr:
                    diagnostics.append(redact(line, 1000))
                    if len(diagnostics) > 8:
                        diagnostics.pop(0)
            threading.Thread(target=drain_stdout, daemon=True).start()
            threading.Thread(target=drain_stderr, daemon=True).start()
            def send(message):
                proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            send(dict(id=1, method="initialize", params=dict(clientInfo=dict(name="ai_director_workbench",version="1.0.0"))))
            deadline = time.monotonic() + self.timeout
            image = None
            while time.monotonic() < deadline:
                try:
                    message = messages.get(timeout=min(2, max(.1, deadline-time.monotonic())))
                except queue.Empty:
                    continue
                if message is None:
                    return record("unknown" if sent_turn else "failed", "Codex进程提前结束：" + "".join(diagnostics))
                if message.get("error"):
                    return record("unknown" if sent_turn else "failed", json.dumps(message["error"],ensure_ascii=False))
                if message.get("id") == 1 and "result" in message:
                    send(dict(method="initialized"))
                    send(dict(id=2, method="thread/start", params=dict(model=req["model"],cwd=str(work),
                              sandbox="read-only",approvalPolicy="never",ephemeral=True,config=config)))
                elif message.get("id") == 2 and "result" in message:
                    result = message["result"]
                    if result.get("model") != req["model"]:
                        return record("failed", "Codex返回了未授权的型号；未发送生图任务")
                    thread_id = result["thread"]["id"]
                    receipt["threadId"] = thread_id
                    prompt = ("使用Codex内置image_gen工具完成恰好一张实际图片。必须调用内置生图工具，不能以文字、SVG、代码、"
                              "MCP、插件或其他API替代。不要调用shell，不读取个人技能。不输出第二张或多余版本。"
                              f"请求图片尺寸{req['size']}，以原生生成结果为准，不做程序缩放。"
                              "若附有图片，严格按顺序作为编辑目标或参考。完成后只简短说明。\n工作台已确认的绘图要求：\n" + req["prompt"])
                    inputs = [dict(type="text", text=prompt)]
                    inputs.extend(dict(type="localImage",path=str(Path(p).resolve())) for p in req.get("inputs",[]))
                    send(dict(id=3,method="turn/start",params=dict(threadId=thread_id, model=req["model"],input=inputs)))
                    sent_turn = True
                    record("unknown")
                method = message.get("method", "")
                params = message.get("params") or {}
                if method:
                    receipt["events"].append(method)
                    receipt["events"] = receipt["events"][-200:]
                    if method in ("turn/started", "item/started", "item/completed", "turn/completed", "error"):
                        write_atomic(job / "receipt.json", canonical(receipt))
                if method == "item/completed" and params.get("item", {}).get("type") == "imageGeneration":
                    item = params["item"]
                    receipt.update(itemId=item["id"], status=item.get("status"), revisedPrompt=item.get("revisedPrompt"))
                    if item.get("failure"):
                        return record("failed", json.dumps(item["failure"],ensure_ascii=False))
                    if image is not None:
                        return record("unknown", "Codex返回多张图片，须核对原请求")
                    if item.get("result"):
                        image = base64.b64decode(item["result"], validate=True)
                    elif item.get("savedPath"):
                        saved = Path(item["savedPath"]).resolve()
                        if job not in saved.parents:
                            return record("unknown", "Codex结果路径超出本请求目录")
                        image = saved.read_bytes()
                    if not image or not image_size(image):
                        return record("unknown", "Codex未返回可读取的实际图片")
                    write_atomic(job / "result.png", image)
                    receipt["sha256"] = sha256_bytes(image)
                    record("succeeded")
                if method == "turn/completed":
                    if image:
                        record("succeeded")
                        return ImageOutcome("succeeded", image, receipt.get("itemId"))
                    return record("failed", "Codex未生成图片：" + json.dumps(params.get("turn",{}).get("error"),ensure_ascii=False))
                if "id" in message and method:
                    # Do not execute dynamic tools or approve model-requested operations.
                    send(dict(id=message["id"],error=dict(code=-32601,message="本图像通道只执行Codex内置生图")))
            return record("unknown", "等待Codex原生生图超时；保留原请求，不自动重发")
        except Exception as exc:
            return record("unknown" if sent_turn else "failed", str(exc))
        finally:
            if proc:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close()
