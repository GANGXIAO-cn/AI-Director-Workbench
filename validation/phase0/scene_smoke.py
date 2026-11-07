"""Live Codex draft from one instruction. Isolated sample data; uses the signed-in text account."""
import json
from pathlib import Path
import tempfile

from executors import draft_request, probe
from storage import adopt, connect, digest, freeze, initialize, receive

SOURCE = "场02：值夜班的柴犬阿福站在门边，门挡着送货车。"
REQUEST = "只改场02的动作。让人看懂阿福为什么要推门，再写出它怎么推开门。不要对白。"


def run(root):
    work, store = root / "worker", root / "data"
    work.mkdir()
    store.mkdir()
    initialize(store)
    status = probe("codex", False, work)
    if not status["authenticated"] or status["missing_flags"]:
        raise RuntimeError("Codex capability check failed")
    source = store / "scene02.txt"
    source.write_text(SOURCE)
    run_id = "synthetic-scene02"
    fingerprint = freeze(store, run_id, source)
    with connect(store) as db:
        raw, = db.execute("SELECT manifest FROM snapshot WHERE hash=?", (fingerprint,)).fetchone()
    manifest = json.loads(raw)
    frozen = (store / "blobs" / manifest["inputs"][0]["sha256"]).read_text()
    context = dict(schemaVersion=1, kind="draft", runId=run_id, inputFingerprint=fingerprint,
                   baseVersionId=manifest["base"])
    prompt = ("这是虚构的剧本修改技术测试。只用提供的文字，禁用工具。"
              f"原文：{frozen}\n要求：{REQUEST}\n"
              f"固定字段：{json.dumps(context)}。changes 只返回一项，targetId 为 scene02，"
              "field 为 action，value 写出你创作的中文动作。")
    report, result = draft_request("codex", status["executable"], work, prompt)
    if result is None:
        print(json.dumps(report))
        return False
    receive(store, run_id, result)
    with connect(store) as db:
        before = db.execute("SELECT revision,draft_id FROM head").fetchone()
    changes = result["changes"]
    if before != (0, None) or len(changes) != 1 or changes[0]["value"].strip() in ("", SOURCE):
        raise ValueError("candidate stage failed")
    adopt(store, "synthetic-user-adoption", run_id, 0)
    with connect(store) as db:
        revision, stored = db.execute(
            "SELECT revision,contents FROM head JOIN draft ON draft.id=head.draft_id").fetchone()
    if revision != 1 or json.loads(stored) != result or source.read_text() != SOURCE:
        raise ValueError("adoption readback failed")
    print(json.dumps(dict(provider=status["version"], transport=report, input=fingerprint,
                          result_sha256=digest(stored), adopted_revision=revision,
                          changed_text=changes[0]["value"]), ensure_ascii=False, indent=2))
    return True


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="r7-scene-") as tmp:
        raise SystemExit(0 if run(Path(tmp)) else 1)
