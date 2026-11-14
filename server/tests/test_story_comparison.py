from concurrent.futures import ThreadPoolExecutor

import pytest
from helpers import Env
from workbench.errors import Conflict, Rejected
from workbench.executors.fake import quota_exhausted


def seed(tmp_path, plan):
    env = Env(tmp_path)
    _, eid = env.new_episode(plan=plan)
    base = env.adopt_story(eid)
    return env, eid, base


def test_story_candidates_use_identical_frozen_material_and_exact_episode_selections(tmp_path):
    model = "fixture-same-model"
    env, eid, base = seed(tmp_path, {"create":{"provider":"codex","model":model,"effort":"low"},
                                     "review":{"provider":"codex","model":model,"effort":"high"}})
    wb = env.wb
    pid=wb.episode_owner(eid)["project_id"]
    original_task=env.art(eid,"story")["tasks"][0]
    book=wb.import_knowledge(pid,[dict(name="动作笔记",kind="method",data="先交代伞的故障。".encode())])[0]
    wb.cite(original_task["id"],book["id"],"让动作能从画面看懂")
    wb.save_step_config(eid,"story",keep="两个人最后共用一把伞")
    with ThreadPoolExecutor(2) as pool:
        jobs=list(pool.map(lambda cid:wb.start_story_comparison(base,cid), ["first-tab","second-tab"]))
    assert jobs[0] == jobs[1]
    with pytest.raises(Conflict, match="另一份创作要求"):
        wb.start_story_comparison(base,"different-goal",request="改变结局")
    left,right = jobs[0]["runIds"]
    manifests = [wb.run_manifest(rid)["manifest"] for rid in (left,right)]
    assert manifests[0]["taskId"] != manifests[1]["taskId"]
    assert [m["requestedEffort"] for m in manifests] == ["low","high"]
    assert all(m["provider"]=="codex" and m["requestedModel"]==model and m["baseVersionId"]==base for m in manifests)
    assert manifests[0]["inputs"] == manifests[1]["inputs"]
    assert manifests[0]["methodPack"] == manifests[1]["methodPack"]
    assert any(i["role"]=="step_requirements" for i in manifests[0]["inputs"])
    assert any(i["role"]=="citation" and i["name"]=="动作笔记" for i in manifests[0]["inputs"])
    wb.save_step_config(eid,"story",keep="两个人最后共用一把伞",goal="另一份新要求")
    begin=len(env.codex.calls)
    env.drain(right)
    env.drain(left)
    env.drain(left)
    calls=env.codex.calls[begin:]
    assert len(calls)==2 and [c.effort for c in calls]==["high","low"]
    assert all("两个人最后共用一把伞" in c.prompt and "另一份新要求" not in c.prompt for c in calls)
    assert calls[0].prompt.split("===== 固定字段")[0] == calls[1].prompt.split("===== 固定字段")[0]
    art=env.art(eid,"story")
    assert [c["side"] for c in art["comparisons"]] == ["A","B"]
    vids=[c["version_id"] for c in art["comparisons"]]
    assert len(set(vids))==2 and all(vids)
    assert art["adopted_version_id"]==base
    for vid in vids:
        assert wb.adoption_gate(vid)["adoptable"]
        assert not wb.adoption_gate(vid)["reviewRequired"]
    res=wb.combine(art["id"], {"synopsis":vids[0], "handoff":vids[1],"decisionNotes":vids[0]}, "combine")
    assert wb.adoption_gate(res["versionId"])["adoptable"]
    review_id=wb.submit_review(res["versionId"],"judge")["runId"]
    receipts=wb.version_sources(res["versionId"])["inputs"]
    cited=next(r for r in receipts if r["name"]=="动作笔记")
    assert set(cited["sourceRuns"])=={left,right}
    review=wb.run_manifest(review_id)["manifest"]
    assert [i["sha256"] for i in review["inputs"] if i["name"]=="动作笔记"]==[cited["sha256"]]
    env.drain(review_id)
    assert wb.adoption_gate(res["versionId"])["adoptable"]
    next_round=wb.start_story_comparison(base,"new-round",after_round=1)
    assert next_round["roundNo"]==2 and not set(next_round["runIds"]) & set(jobs[0]["runIds"])
    fresh=wb.run_manifest(next_round["runIds"][0])["manifest"]
    assert fresh["inputs"]!=manifests[0]["inputs"]
    assert [s["round_no"] for s in env.art(eid,"story")["comparisons"]]==[2,2,1,1]


def test_failed_slot_retries_original_snapshot_without_restarting_other_author(tmp_path):
    env,eid,base=seed(tmp_path,{"create":{"provider":"claude","model":"author-fixture","effort":"high"},"review":{"provider":"codex"}})
    wb=env.wb
    a,b=wb.start_story_comparison(base,"two")["runIds"]
    env.claude.script=[quota_exhausted]
    env.drain(a)
    env.drain(b)
    slots=env.art(eid,"story")["comparisons"]
    assert [s["status"] for s in slots]==["waiting_quota","succeeded"]
    assert slots[0]["version_id"] is None
    wb.set_quota_retry_policy("same_cli")
    with pytest.raises(Rejected, match="限流策略"):
        wb.retry_run(a,provider="codex",command_id="retry-left")
    assert not wb.pending_runs()
    wb.set_quota_retry_policy("manual_cli")
    r=wb.retry_run(a,provider="codex",command_id="retry-left")["runId"]
    retry_manifest=wb.run_manifest(r)["manifest"]
    assert retry_manifest["requestedModel"] is None and retry_manifest["requestedEffort"] is None
    assert wb.run_manifest(r)["manifest"]["inputs"]==wb.run_manifest(a)["manifest"]["inputs"]
    with pytest.raises(Conflict):
        wb.retry_run(a,command_id="obsolete")
    env.drain(r)
    slots2=env.art(eid,"story")["comparisons"]
    assert [s["run_id"] for s in slots2]==[r,b] and slots2[1]==slots[1]
    assert all(s["version_id"] for s in slots2)
    vid=env.script(eid)
    with pytest.raises(Rejected,match="故事稿"):
        wb.start_story_comparison(vid,"invalid")
