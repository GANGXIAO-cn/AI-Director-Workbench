"""Isolated launcher for real acceptance. Run with the server dependencies installed."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from workbench.app import create_app, default_executors, default_image_providers
from workbench.errors import Rejected
from workbench.executors.base import ExecOutcome
from workbench.service import Workbench


class GuardedExecutor:
    def __init__(self, provider, wrapped):
        self.provider = provider
        self.wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def start(self, request):
        if (self.provider, request.model, request.effort) != ("codex", "gpt-6.1-sol", "high"):
            return ExecOutcome(ok=False, error_class="unauthorized_model", diagnostic="真实验证仅授权 Codex GPT-6.1 Sol High")
        return self.wrapped.start(request)


class ValidationWorkbench(Workbench):
    def execute_batch_precheck(self, batch_id):
        batch = super().execute_batch_precheck(batch_id)
        if (batch["provider"], batch["model"]) != ("codex-native", "gpt-6.1-sol"):
            raise Rejected("真实验证仅授权 Codex 内置生图 GPT-6.1 Sol High")
        return batch


if __name__ == "__main__":
    import uvicorn
    data = Path(os.environ["ADW_DATA_DIR"]).resolve()
    if data == ROOT / "data" or (ROOT / "data") in data.parents or os.environ.get("ADW_FAKE") == "1":
        raise SystemExit("真实验证须使用隔离数据目录和真实执行器")
    executors = {name: GuardedExecutor(name, e) for name, e in default_executors().items()}
    wb = ValidationWorkbench(data, executors, image_providers=default_image_providers())
    uvicorn.run(create_app(wb), host="127.0.0.1", port=int(os.environ.get("ADW_REAL_PORT", "18789")))
