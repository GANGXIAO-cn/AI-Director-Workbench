"""Timing and read-only recovery for an already accepted RunningHub task."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .storage import canonical, write_atomic


class PollInterrupted(Exception):
    """Service shutdown. The next worker can continue with the saved task ID."""


def timing(folder):
    path = folder / "cloud-task.json"
    return json.loads(path.read_bytes()) if path.is_file() else {}


def fetch_output(
    hub, task, pending: Path, node, *, file_type=None, submitted_at=None, stop=None
):
    folder = pending.parent
    state = timing(folder)
    if state.get("task") != str(task):
        state = dict(
            task=str(task),
            startedAt=submitted_at or time.time(),
            observedOnly=submitted_at is None,
        )
        write_atomic(folder / "cloud-task.json", canonical(state))

    def update(**values):
        if any(state.get(k) != v for k, v in values.items()):
            state.update(values)
            write_atomic(folder / "cloud-task.json", canonical(state))

    def wait(seconds):
        if stop is None:
            time.sleep(seconds)
        elif stop.wait(seconds):
            raise PollInterrupted()

    while True:
        wait(max(0, state.get("nextCheck", 0) - time.time()))
        phase = "查询"
        try:
            result = hub.query(task)
            code = str(result.get("code"))
            if code in ("804", "813") or (code == "0" and not result.get("data")):
                update(failures=0, retryMessage="", nextCheck=0)
                wait(12)
                continue
            if code in ("0", "805") and not state.get("endedAt"):
                update(endedAt=time.time())
            if code != "0":
                update(failures=0, retryMessage="", nextCheck=0)
                return result
            outputs = [
                r["fileUrl"]
                for r in result["data"]
                if str(r.get("nodeId")) == node
                and (not file_type or str(r.get("fileType", "")).lower() == file_type)
                and urlsplit(r.get("fileUrl", "")).scheme == "https"
            ]
            if len(outputs) != 1:
                raise ValueError("未找到唯一的云端正式输出文件")
            phase = "下载"
            update(phase=phase)
            with hub.client.stream("GET", outputs[0]) as response:
                response.raise_for_status()
                with pending.open("wb") as target:
                    for chunk in response.iter_bytes():
                        target.write(chunk)
            update(failures=0, retryMessage="", nextCheck=0)
            return result
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if status not in (408, 425, 429) and not 500 <= status < 600:
                    raise
                reason = f"HTTP {status}"
            else:
                reason = "连接超时或中断"
            failures = state.get("failures", 0) + 1
            delay = min(300, 24 * 2 ** min(failures - 1, 4))
            update(
                failures=failures,
                nextCheck=time.time() + delay,
                retryMessage=f"{phase}遇到{reason}，自动重查原任务",
            )
