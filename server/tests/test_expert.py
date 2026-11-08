import pytest

from helpers import Env
from workbench.errors import Conflict, Rejected
from workbench.executors.fake import review


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_expert_honors_target_and_exact_checkpoint(env):
    wb = env.wb
    pid, eid = env.new_episode()
    args = dict(project_id=pid, episodes=[eid], stop_stage="script", division={"review":{"provider":"claude"}},
                stops=["confirm_story"], expected=wb.expert_preview(pid, [eid]), command_id="auth")
    aid = wb.authorize_expert(**args)["authId"]
    assert wb.authorize_expert(**args)["authId"] == aid
    with pytest.raises(Conflict):
        wb.authorize_expert(**dict(args, budget_cents=1))
    stopped = wb.expert_run(aid)
    assert stopped["state"] == "stopped" and stopped["checkpoint"] == stopped["versionId"]
    assert not env.art(eid, "story")["adopted_version_id"]
    assert wb.expert_run(aid, "stale")["state"] == "stopped"
    done = wb.expert_run(aid, stopped["token"])
    assert done["state"] == "done"
    assert [a["stage"] for a in done["log"] if a.get("completed")] == ["story","script"]
    calls = len(env.claude.calls), len(env.codex.calls)
    assert wb.expert_run(aid, stopped["token"])["state"] == "done"
    assert calls == (len(env.claude.calls), len(env.codex.calls))
    assert [a["stage"] for a in wb.episode_view(eid)["artifacts"]] == ["story","script"]
    assert wb.expert_history(pid, eid)[0]["id"] == aid


def test_expert_scope_preview_pause_overlap_and_changed_input(env):
    wb = env.wb
    pid, eid = env.new_episode()
    other, e2 = env.new_episode()
    with pytest.raises(Rejected):
        wb.authorize_expert(pid, [eid,e2], "story", {})
    start = wb.expert_preview(pid,[eid])
    env.adopt_story(eid)
    with pytest.raises(Conflict):
        wb.authorize_expert(pid,[eid],"story",{},expected=start)
    aid = wb.authorize_expert(pid,[eid],"script",{})["authId"]
    second = wb.authorize_expert(pid,[eid],"script",{})["authId"]
    assert wb.claim_expert(aid)
    assert not wb.claim_expert(aid)
    with pytest.raises(Conflict):
        wb.claim_expert(second)
    wb.pause_expert(aid)
    stopped = wb.advance_expert(aid)
    assert stopped["reason"] == "paused"
    wb.manual_expert(aid)
    assert wb.expert_view(aid)["state"] == "manual"
    wb.episode_spec_change(eid,"aspectRatio","16:9","spec",confirm=True)
    assert wb.expert_run(aid,stopped["token"])["reason"] == "settings_change"


def test_expert_budget_per_episode_and_unknown_not_resubmitted(env):
    wb = env.wb
    pid, eid = env.new_episode()
    _, other = env.new_episode(code="E02",project_id=pid)
    aid = wb.authorize_expert(pid,[eid,other],"story",{},budget_cents=10)["authId"]
    def batch(e, text):
        return wb.create_batch(e,"fake-image","generate",[dict(prompt=text,target=dict(name=text,category="state"))],
                               "64x64",budget_cents=6)
    b = batch(eid,"一")
    stop = wb.expert_run(aid)
    assert stop["reason"] == "paid_batch" and env.images.sent == 0
    second = batch(eid,"二")
    wb.authorize_batch(b["batchId"],b["summaryHash"],"pay1")
    with pytest.raises(Rejected,match="剩余金额"):
        wb.authorize_batch(second["batchId"],second["summaryHash"],"pay2")
    # Each named episode owns its own allowance.
    third = batch(other,"三")
    wb.authorize_batch(third["batchId"],third["summaryHash"],"pay3")
    env.images.script = ["timeout"]
    wb.execute_batch(b["batchId"])
    res = wb.expert_run(aid,stop["token"])
    assert res["reason"] == "unknown_request" and res["batchId"] == b["batchId"]
    assert wb.expert_run(aid,res["token"])["reason"] == "unknown_request" and env.images.sent == 1


def test_expert_delivers_once_after_confirmation(env):
    wb = env.wb
    ep = env.full_episode(deliver=False,segments={"G01":[8000]})
    aid = wb.authorize_expert(ep["pid"],[ep["eid"]],"delivery",{},stops=["confirm_delivery"])["authId"]
    stop = wb.expert_run(aid)
    assert stop["stage"] == "delivery" and stop["reason"] == "confirm_point"
    with env.db() as db:
        assert db.execute("SELECT count(*) FROM delivery").fetchone()[0] == 0
    done = wb.expert_run(aid,stop["token"])
    did = done["log"][-1]["deliveryId"]
    assert wb.verify_delivery(did)["ok"]
    assert wb.expert_run(aid,stop["token"])["log"] == done["log"]


def test_expert_two_reviewers_disagree(env):
    wb = env.wb
    pid, eid = env.new_episode()
    job = wb.create_task(eid,"story","写故事")
    env.drain(job["runId"])
    vid = env.art(eid,"story")["versions"][0]["id"]
    env.drain(wb.submit_review(vid,"first",provider="codex")["runId"])
    env.claude.script = [lambda r: review(r,"return",[dict(reason="动机不清")])]
    env.drain(wb.submit_review(vid,"second",provider="claude")["runId"])
    aid = wb.authorize_expert(pid,[eid],"story",{})["authId"]
    assert wb.expert_run(aid)["reason"] == "model_conflict"
    assert not env.art(eid,"story")["adopted_version_id"]


def test_from_adopted_script_through_plan_and_two_new_reviews(env):
    import json
    from workbench.executors.fake import draft
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01": [8000]})
    prompts = env.prompts_for(plan, env.image(pid,eid)["versionId"])
    env.codex.script = [lambda r: draft(r, shots=json.dumps(plan["shots"]), segments=json.dumps(plan["segments"])),
                        lambda r: draft(r, segments=json.dumps(prompts["segments"]))]
    aid = wb.authorize_expert(pid,[eid],"prompts",{},stops=["confirm_plan"])["authId"]
    stop = wb.expert_run(aid)
    assert stop["reason"] == "confirm_point" and stop["stage"] == "plan"
    art = env.art(eid,"plan")
    wb.adopt(art["id"],stop["versionId"],None,"director-confirms-plan-in-workspace")
    result = wb.expert_run(aid,stop["token"])
    assert result["state"] == "done", result.get("detail")
    vid = env.art(eid,"prompts")["adopted_version_id"]
    assert wb.prompt_review_status(vid)["G01"]["ok"]
    assert not any(a["stage"] == "publish" for a in wb.episode_view(eid)["artifacts"])


def test_service_restart_holds_expert_scope(env):
    from workbench.service import Workbench
    wb = env.wb
    pid,eid = env.new_episode()
    aid = wb.authorize_expert(pid,[eid],"story",{})["authId"]
    wb.claim_expert(aid)
    restarted = Workbench(wb.store.root,wb.executors)
    stopped = restarted.expert_view(aid)
    assert stopped["reason"] == "interrupted"
    assert restarted.expert_run(aid)["state"] == "stopped"
    assert restarted.expert_run(aid,stopped["token"])["state"] == "done"


def test_expert_delivery_manifest_compared_to_database(env):
    import json
    from pathlib import Path
    ep = env.full_episode(segments={"G01":[8000]})
    d = ep["delivery"]
    root = Path(d["path"])
    manifest = root / "manifest.json"
    modified = json.loads(manifest.read_text())
    modified["files"].pop("episode.json")
    manifest.write_text(json.dumps(modified))
    result = env.wb.verify_delivery(d["deliveryId"])
    assert not result["ok"] and result["errors"][0]["file"] == "manifest.json"
