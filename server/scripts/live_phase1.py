"""Live phase-1 run: create -> session-isolated review -> adopt, through the real service.

Uses the signed-in text account of the chosen CLI (counts against its usage). Synthetic input only;
no image or other paid generation. Data goes to a fresh directory that is printed at the end.

  python -B server/scripts/live_phase1.py codex   # PUGGY: import N07 洗车店, continue N06
  python -B server/scripts/live_phase1.py claude  # 夜班柴犬: new project, idea only
"""
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.executors import ClaudeExecutor, CodexExecutor   # noqa: E402
from workbench.service import Workbench                          # noqa: E402

CARWASH = ("# N07 洗车店（合成测试稿）\n\nMILO 在洗车店看见自动泡沫枪，决定趁女主人排队时自己给家里的红色汽车洗车。"
           "LEO 想先把车里的猫粮袋拿回家。泡沫越喷越多，最后整辆车变成一座泡沫小山，女主人回来只看到两只猫的耳朵。\n")


def drain(wb, run_id, log):
    queue = [run_id]
    while queue:
        rid = queue.pop(0)
        t0 = time.time()
        queue += wb.execute_run(rid)
        log.append(dict(run=rid, seconds=round(time.time() - t0, 1)))


def summary(wb, episode_id):
    view = wb.episode_view(episode_id)
    story = view["artifacts"][0]
    runs = [r for t in story["tasks"] for r in t["runs"]]
    return story, runs


def main(provider):
    data = Path(tempfile.mkdtemp(prefix=f"adw-live-{provider}-"))
    executors = {"codex": CodexExecutor(), "claude": ClaudeExecutor()}
    wb = Workbench(data, executors)
    probe = executors[provider].probe()
    plan = {"create": {"provider": provider}, "review": {"provider": provider}}
    log = []
    if provider == "codex":
        pid = wb.create_project("puggy")["projectId"]
        n06 = wb.create_episode(pid, "N06", "厨房", ["厨房门上了锁"])["episodeId"]
        ep = wb.create_episode(pid, "N07", "洗车店")["episodeId"]
        imp = wb.import_file(pid, "N07_洗车店.md", CARWASH.encode(), "story_source", ep)["importId"]
        wb.start_episode(ep, "import", plan, source_import_id=imp, relation="continue", predecessor_ids=[n06],
                         command_id="live-kick")
        goal = "按导入稿写出本集故事稿。承接前集遗留事实。"
    else:
        pid = wb.create_project("_template", "夜班柴犬")["projectId"]
        ep = wb.create_episode(pid, "E01", "第一次值夜班")["episodeId"]
        wb.start_episode(ep, "idea", plan, idea_text="一只过分认真的柴犬第一次值夜班", command_id="live-kick")
        goal = "从这个想法写出第一份故事稿，目标约 70 秒，无对白也能看懂。"
    task = wb.create_task(ep, "story", goal, command_id="live-task")
    drain(wb, task["runId"], log)
    story, runs = summary(wb, ep)
    out = dict(provider=provider, probe=probe, data_dir=str(data), steps=[])
    if not story["versions"]:
        out["stopped"] = runs[-1]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    version = story["versions"][-1]
    for attempt in range(2):
        rv = wb.submit_review(version["id"], f"live-review-{attempt}")
        drain(wb, rv["runId"], log)
        story, runs = summary(wb, ep)
        version = next(v for v in story["versions"] if v["id"] == version["id"])
        run = next(r for r in runs if r["id"] == rv["runId"])
        step = dict(review_run=rv["runId"], status=run["status"], session=run["session_id"],
                    session_mode=run["session_mode"], isolation=run["isolation_level"], manifest=run["input_hash"],
                    diagnostic=run["diagnostic"])
        out["steps"].append(step)
        if run["status"] != "succeeded":
            break
        review = version["reviews"][-1]
        step.update(decision=review["original_decision"],
                    issues=[dict(blocking=i["blocking"], category=i["category"], setting=i["setting_id"],
                                 anchor=i["loc"]["anchor"], reason=i["reason"]) for i in review["issues"]])
        if version["gate"]["blocking"] or (attempt == 0 and review["issues"]):
            # revise once from the review's issues (user-style message, not an auto round)
            text = "按独立审查意见修订：\n" + "\n".join(f"- {i['loc']['anchor']}：{i['reason']}" for i in review["issues"])
            msg = wb.post_message(task["taskId"], text, f"live-msg-{attempt}", base_version_id=version["id"])
            drain(wb, msg["runId"], log)
            story, runs = summary(wb, ep)
            if story["versions"][-1]["id"] == version["id"]:
                break
            version = story["versions"][-1]
            continue
        for issue in review["issues"]:
            # simulated user decision, recorded as such
            wb.waive(issue["id"], version["id"], "实测脚本模拟用户：普通意见留待导演复读，按理由放行",
                     f"live-waive-{issue['id']}")
        break
    gate = wb.adoption_gate(version["id"])
    out["gate"] = gate
    if gate["adoptable"]:
        out["adoption"] = wb.adopt(story["id"], version["id"], story["adopted_version_id"], "live-adopt")
    story, runs = summary(wb, ep)
    out["adopted_version"] = story["adopted_version_id"]
    out["versions"] = [dict(no=v["display_no"], origin=v["origin"], base=v["base_id"], id=v["id"]) for v in story["versions"]]
    out["runs"] = [dict(id=r["id"], purpose=r["purpose"], status=r["status"], session=r["session_id"],
                        mode=r["session_mode"], isolation=r["isolation_level"], fingerprint=r["input_hash"],
                        reason=r["reason"], format_attempts=r["format_attempts"], resets=r["quota_resets_at"])
                   for r in runs]
    out["timing"] = log
    out["adopted_synopsis"] = next((v["content"]["synopsis"] for v in story["versions"]
                                    if v["id"] == story["adopted_version_id"]), None)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if story["adopted_version_id"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
