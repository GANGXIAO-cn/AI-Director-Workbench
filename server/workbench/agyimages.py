"""Run Antigravity's native image tool and import only the current request's artifact."""
from io import BytesIO
import json
from pathlib import Path
import re

from PIL import Image, ImageOps

from .executors.base import ExecRequest
from .executors.cli import AntigravityExecutor
from .images import ImageOutcome, image_size
from .storage import canonical, sha256_bytes, write_atomic


class AntigravityImageProvider:
    name = "antigravity-native"
    currency = "Antigravity额度"
    billing_mode = "subscription_usage"
    remote_status = False
    price_cents = None
    verified = "verified_generation"
    capabilities = dict(
        label="Google Antigravity",
        operations=["generate", "edit"],
        sizes=["1024x1024", "864x1536", "1536x864", "1024x1536", "1536x1024"],
        quality=["auto"],
        models=[],
        billingMode=billing_mode,
    )

    def __init__(self, root, executor=None):
        self.root = Path(root)
        self.executor = executor or AntigravityExecutor()
        self.connection = lambda: {}

    def directory(self, request_id):
        if not re.fullmatch(r"[\w-]{1,100}", request_id):
            raise ValueError("无效的出图请求编号")
        return self.root.resolve() / request_id

    def inspect(self, request_id):
        folder = self.directory(request_id)
        receipt = folder / "receipt.json"
        if not receipt.is_file():
            return None
        saved = json.loads(receipt.read_bytes())
        if saved["state"] == "succeeded":
            data = (folder / "result.png").read_bytes()
            if sha256_bytes(data) == saved["sha256"]:
                return ImageOutcome("succeeded", data, saved["session"])
            return ImageOutcome("unknown", error="Antigravity 图片与回执不符")
        return ImageOutcome(saved["state"], error=saved["error"])

    def submit(self, req):
        if req.get("model"):
            return ImageOutcome("failed", error="此原生出图工具未提供指定图像型号的参数，请选择原生工具默认或可指定型号的通道")
        rid = req["local_request_id"]
        folder = self.directory(rid)
        if not self.executor.binary():
            return ImageOutcome("failed", error="找不到 Google Antigravity CLI，请检查执行器连接")
        try:
            folder.mkdir(parents=True)
        except FileExistsError:
            return self.inspect(rid) or ImageOutcome("unknown", error="原出图请求已登记")
        work = folder / "work"
        work.mkdir()
        receipt = folder / "receipt.json"
        write_atomic(
            receipt, canonical(dict(state="unknown", error="等待原 Antigravity 出图结果"))
        )
        # Prefix binds a generated artifact (including subagent artifacts) to this request.
        marker = "adw_" + sha256_bytes(rid.encode())[:16]
        refs = []
        for index, source in enumerate(req.get("inputs", []), 1):
            data = Path(source).read_bytes()
            path = work / (
                f"ref-{index}" + (".jpg" if data.startswith(b"\xff\xd8") else ".png")
            )
            write_atomic(path, data)
            refs.append(str(path))
        prompt = (
            "使用 Google Antigravity 原生图像生成工具生成一张真实图片。调用 generate_image 时 ImageName 使用 "
            + marker
            + "，用本轮生成的原图作为结果。禁止通过绘图代码或其他平台替代。"
            + "尺寸与构图要求为 "
            + req["size"]
            + "。"
        )
        if refs:
            prompt += "按顺序把这些原图作为 ImagePaths 传入原生工具：" + json.dumps(
                refs, ensure_ascii=False
            )
        prompt += "返回生成的图片文件的绝对路径 imagePath。\n绘图要求：\n" + req["prompt"]
        schema = dict(
            type="object",
            properties={"imagePath": {"type": "string"}},
            required=["imagePath"],
        )
        connection = self.connection()
        result = self.executor.start(
            ExecRequest(
                "image-" + rid,
                "image_generation",
                "generate",
                prompt,
                schema,
                "new",
                work,
                timeout=360,
                model=connection.get('model'),
                effort=connection.get('effort'),
                connection=connection,
            )
        )
        try:
            if not result.ok:
                raise ValueError(result.diagnostic or "Antigravity 未返回图片")
            path = Path(result.structured["imagePath"]).resolve(strict=True)
            roots = [
                Path.home() / ".gemini" / app / "brain"
                for app in ("antigravity", "antigravity-cli")
            ]
            if marker not in path.stem or not any(
                path.is_relative_to(root.resolve()) for root in [work, *roots]
            ):
                raise ValueError("未取得本次 Antigravity 生成的图片文件")
            raw = path.read_bytes()
            size = image_size(raw)
            target = tuple(map(int, req["size"].split("x")))
            if not size or abs(size[0] * target[1] / (size[1] * target[0]) - 1) > 0.06:
                raise ValueError("Antigravity 返回的图片画幅与本次要求不符")
            with Image.open(BytesIO(raw)) as im:
                output = BytesIO()
                ImageOps.pad(
                    im.convert("RGB"), target, method=Image.Resampling.LANCZOS
                ).save(output, format="PNG")
            data = output.getvalue()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            diagnostic = str(exc)[:500]
            state = (
                "failed"
                if result.error_class in ("authentication", "quota", "unavailable")
                else "unknown"
            )
            write_atomic(receipt, canonical(dict(state=state, error=diagnostic)))
            return ImageOutcome(state, error=diagnostic)
        write_atomic(folder / "result.png", data)
        write_atomic(
            receipt,
            canonical(
                dict(
                    state="succeeded",
                    session=result.session_id,
                    sha256=sha256_bytes(data),
                )
            ),
        )
        return ImageOutcome("succeeded", data, result.session_id)
