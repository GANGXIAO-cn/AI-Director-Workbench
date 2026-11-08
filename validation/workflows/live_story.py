"""Real four-step acceptance for the authorized synthetic umbrella story."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from workbench.db import connect
from workbench.executors import CodexExecutor
from workbench.executors.base import ExecOutcome
from workbench.service import Workbench


class OncePerStage(CodexExecutor):
    def __init__(self):
        super().__init__()
        self.sent = set()

    def start(self, request):
        if request.run_id in self.sent or len(self.sent) >= 4:
            return ExecOutcome(ok=False, error_class="call_limit", diagnostic="本轮不追加格式重试")
        assert not request.images and request.model == "gpt-6.1-sol"
        self.sent.add(request.run_id)
        return super().start(request)


def main(directory):
    directory.mkdir(exist_ok=False)
    wb = Workbench(directory / "data", {"codex": OncePerStage()})
    pid = wb.create_project("_template", "站台雨伞")["projectId"]
    eid = wb.create_episode(pid, "E01", "站台雨伞")["episodeId"]
    role = dict(provider="codex", model="gpt-6.1-sol", effort="medium")
    wb.start_episode(eid, "idea", {"create": role, "review": role},
                     idea_text="末班车进站前，两位陌生人在候车亭拿错相同的雨伞。写一个仅靠可见动作讲清的短故事。")
    allowed = {"method", "stage_contract", "project_settings", "kickoff", "request", "reviewed_version",
               "requirements", "adopted_story", "adopted_script", "bookmarks"}
    log = []

    def once(stage, run_id):
        frozen = wb.run_manifest(run_id)
        manifest = frozen["manifest"]
        assert frozen["matches"] and manifest["episodeId"] == eid
        for entry in wb.store.manifest_texts(manifest):
            assert entry["role"] in allowed and not entry.get("binary")
            if entry["role"] == "method":
                source = manifest["methodPack"]["files"][entry["name"]]
                assert source["sha256"] == entry["sha256"] and not source["path"].startswith("project/")
            if entry["role"] == "bookmarks":
                assert not json.loads(entry["text"])
        print(f"{stage} {run_id}", flush=True)
        wb.execute_run(run_id)
        with connect(wb.db_path) as db:
            run = dict(db.execute("SELECT status,reason,diagnostic,input_hash,session_mode FROM run WHERE id=?", (run_id,)).fetchone())
            version = db.execute("SELECT id,content FROM version WHERE source_run_id=?", (run_id,)).fetchone()
        log.append(dict(stage=stage, run=run_id, **run, content=json.loads(version[1]) if version else None))
        (directory/"四步验收.json").write_text(json.dumps(log, ensure_ascii=False, indent=2))
        print(run["status"], run["reason"], flush=True)
        if run["status"] != "succeeded":
            raise SystemExit(run["diagnostic"] or run["reason"] or run["status"])
        return version[0] if version else None

    for stage, request in [("story", "完成可独立审读的故事与角色交接。"),
                           ("script", "根据已采用故事写剧情阶段主稿，写清可见动作和反应。"),
                           ("plan", "根据已采用剧本起草镜头与生成分段，保留原句、可见反应、声源与剪点。")]:
        task = wb.create_task(eid, stage, request)
        vid = once(stage, task["runId"])
        if stage == "story":
            review = wb.submit_review(vid, "review", task_id=task["taskId"])
            once("story_review", review["runId"])
        gate = wb.adoption_gate(vid)
        log[-1]["adoptionGate"] = gate
        (directory/"四步验收.json").write_text(json.dumps(log, ensure_ascii=False, indent=2))
        if not gate["adoptable"]:
            raise SystemExit(f"{stage} 未达采用条件")
        wb.adopt(task["artifactId"], vid, None, "adopt-" + stage, and_continue=True)


def check_receipts(directory, output):
    from workbench import timeline, stages
    from workbench.storage import canonical, sha256_bytes
    rows = json.loads((directory / "四步验收.json").read_text())
    if [r["stage"] for r in rows] != ["story", "story_review", "script", "plan"]:
        raise ValueError("四步运行记录不齐")
    for record in rows:
        assert record["status"] == "succeeded"
        run_dir = directory / "data/runs" / record["run"]
        manifest = json.loads((run_dir / "input-manifest.json").read_text())
        result = json.loads((run_dir / "result.json").read_text())
        fingerprint = sha256_bytes(canonical(manifest))
        assert manifest["runId"] == result["runId"] == record["run"]
        assert fingerprint == record["input_hash"] == result["inputFingerprint"]
        assert manifest["provider"] == "codex" and manifest["requestedModel"] == "gpt-6.1-sol"
        for item in manifest["inputs"]:
            digest = item["sha256"]
            raw = (directory / "data/blobs/sha256" / digest[:2] / digest).read_bytes()
            assert not item.get("binary") and sha256_bytes(raw) == digest and len(raw) == item["bytes"]
        record.update(requestedModel=manifest["requestedModel"], effort=manifest["requestedEffort"],
                      inputs=[{k:i[k] for k in ("role","name","sha256","bytes")} for i in manifest["inputs"]])
        if record["stage"] == "story_review":
            record["review"] = result
            assert record["review"]["decision"] == "pass" and record["session_mode"] == "ephemeral"
            assert result["reviewedVersionId"] == record["adoptionGate"]["versionId"]
        else:
            assert result["kind"] == "draft" and result["baseVersionId"] is None
            applied = {c["field"]: stages.normalize(record["stage"], c["field"], c["value"]) for c in result["changes"]}
            assert record["content"] == applied
    script = rows[2]["content"]["scenes"]
    plan = rows[3]["content"]
    matched = sum(any(scene["id"] == shot["origin"]["scene"] and
                      any(isinstance(v, str) and shot["origin"]["line"] in v for v in scene.values())
                      for scene in script) for shot in plan["shots"])
    times = [s["duration_ms"] for s in timeline.layout(plan)["segments"]]
    assert matched == len(plan["shots"]) and all(5000 <= ms <= 15000 and ms % 1000 == 0 for ms in times)
    checks = dict(sceneCount=len(script), shotCount=len(plan["shots"]), exactOriginMatches=matched,
                  segmentSeconds=[ms//1000 for ms in times], plannedSeconds=sum(times)//1000)
    output.write_text(json.dumps(dict(subject="末班车站台雨伞", steps=rows, checks=checks),
                                 ensure_ascii=False, indent=2) + "\n")
    print(checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check-receipts", type=Path, help="仅离线读取现有回执并输出核验文件")
    args = parser.parse_args()
    if args.check_receipts:
        check_receipts(args.directory, args.check_receipts)
    else:
        main(args.directory)
