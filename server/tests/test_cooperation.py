import pytest

from helpers import Env
from workbench.errors import Conflict, NotFound, Rejected


def test_skip_is_scoped_and_cannot_waive_delivery(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    case = env.full_episode(segments={"G01":[8000]}, deliver=False)
    eid = case["eid"]
    _, sibling = env.new_episode(project_id=case["pid"], code="E02")
    missing = env.image(case["pid"], eid, name="未绑定身份图", rgb=(70,80,90))
    wb.mark_image(eid,missing["versionId"],"required",requirement=dict(reason="出镜角色",targetSegments=["G01"]))
    before = wb.delivery_problems(eid)[0]
    assert before
    for stage in ("space","cast"):
        wb.set_optional_stage(eid, stage, True, "导演采用现有资料")
        with pytest.raises(Conflict):
            wb.set_optional_stage(eid, stage, False)
    assert wb.episode_view(sibling)["skippedStages"] == {}
    assert wb.delivery_problems(eid)[0] == before
    with pytest.raises(Rejected):
        wb.deliver(eid,"full","skip-is-no-waiver")
    wb.set_optional_stage(eid,"space",False,expected_reason="导演采用现有资料")
    assert wb.episode_view(eid)["skippedStages"] == {"cast":"导演采用现有资料"}
    with pytest.raises(Rejected):
        wb.set_optional_stage(eid,"assets",True,"忽略")
    with pytest.raises(Rejected):
        wb.set_optional_stage(eid,"space",True,"  ")


def test_trial_freezes_stage_and_user_materials_without_adopting(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    _, sibling = env.new_episode(project_id=pid,code="E02")
    art = env.art(eid,"story")
    env.drain(wb.create_task(eid,"story","先试一版")["runId"])
    draft = env.art(eid,"story")["versions"][-1]["id"]
    wb.save_step_config(eid,"script",enhancements=["05-A"])
    args = dict(project_id=pid,episode_id=eid,stage="script",provider="codex",source_version=draft)
    prev = None
    tid = None
    for mode,word in [("beginner","最短"),("advanced","跳步"),("expert","基础讲解")]:
        current = wb.episode_view(eid)["cooperationMode"]
        wb.set_cooperation_mode(eid,mode,current)
        sent = wb.send_free_chat(**args,text="先试写最后一场",previous=prev,thread_id=tid)
        prev,tid = sent["turnId"],sent["threadId"]
        wb.execute_free_chat(prev,sent["attemptId"])
        reply = wb.free_chat(pid,episode_id=eid,stage="script")["turns"][-1]
        assert reply["status"]=="succeeded",reply
        assert reply["basis"]["label"]=="未采用" and reply["basis"]["versionId"]==draft
        prompt = env.codex.calls[-1].prompt
        assert word in prompt and "05-A" in prompt and "trial_source" in prompt
        assert not any(name in prompt.upper() for name in ("PUGGY","MILO","FOURTHWALL"))
    assert env.art(eid,"story")["adopted_version_id"] is None
    assert not any(a["stage"]=="script" for a in wb.episode_view(eid)["artifacts"])
    assert wb.free_chat(pid)["threads"] == [] and wb.free_chat(pid,episode_id=sibling,stage="script")["turns"] == []
    with pytest.raises(Rejected):
        wb.send_free_chat(pid,"跨工作区","codex",tid,prev)
    with pytest.raises(NotFound):
        wb.send_free_chat(**dict(args,episode_id=sibling),text="引用另一集")
    with pytest.raises(Rejected):
        wb.create_task(eid,"script","正式起草",trial_turn_id=prev)
    env.drain(wb.submit_review(draft,"review-original")["runId"])
    wb.adopt(art["id"],draft,None,"author-adopts",and_continue=True)
    env.drain(wb.create_task(eid,"script","参考这份试写",trial_turn_id=prev)["runId"])
    assert prev in env.codex.calls[-1].prompt and reply["reply"] in env.codex.calls[-1].prompt
    script = env.art(eid,"script")
    assert script["adopted_version_id"] is None
    refs = wb.version_sources(script["versions"][-1]["id"])["inputs"]
    assert any(r["role"]=="trial_reference" and reply["reply"] in r["text"] for r in refs)
    from workbench.service import Workbench
    folder = tmp_path / "copied-project"
    wb.export_project_folder(pid,str(folder))
    restored = Workbench(folder,{})
    assert restored.free_chat(pid,episode_id=eid,stage="script")==wb.free_chat(pid,episode_id=eid,stage="script")
    assert restored.episode_view(eid)["cooperationMode"]=="expert"


@pytest.mark.parametrize("profile", ["_template","puggy"])
def test_trial_methods_at_every_stage_before_adoption(tmp_path,profile):
    from workbench.enhancements import entries
    env = Env(tmp_path)
    wb = env.wb
    pid,eid = env.new_episode(config=profile)
    for stage,code in [("story","01-B"),("script","05-A"),("plan","00-A"),("prompts","05-C"),("publish","04-D")]:
        wb.save_step_config(eid,stage,enhancements=[code])
        result = wb.send_free_chat(pid,"先试一段","codex",episode_id=eid,stage=stage)
        wb.execute_free_chat(result["turnId"],result["attemptId"])
        turn, = wb.free_chat(pid,episode_id=eid,stage=stage)["turns"]
        assert turn["status"]=="succeeded",turn
        prompt = env.codex.calls[-1].prompt
        assert all(item["text"] in prompt for item in entries([code]))
        if profile=="_template":
            assert not any(word in prompt.upper() for word in ("PUGGY","MILO","FOURTHWALL"))
    assert all(not a["versions"] for a in wb.episode_view(eid)["artifacts"])
