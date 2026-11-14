import json
import pytest

from helpers import Env
from workbench.errors import Conflict, Rejected
from workbench.executors.fake import draft


def fixture(tmp_path, env=None, stage_names=("world", "treatment")):
    env=env or Env(tmp_path)
    wb=env.wb
    _,eid=env.new_episode()
    env.adopt_story(eid)
    skip=wb.episode_view(eid)["skippedStages"]["world"]
    wb.set_optional_stage(eid,"world",False,expected_reason=skip)
    wb.open_stage(eid,"world")
    rules=[dict(id="r1",rule="柜台有锁",choiceImpact="先查门锁")]
    env.save_adopt(eid,"world",dict(rules=rules),"world")
    env.save_adopt(eid,"script",dict(scenes=[dict(id="场01",heading="夜",action="巡店员检查门锁。")]),"script")
    wb.open_stage(eid,"stress")
    issues=[dict(id="I01",stage=stage_names[0],evidence="柜台有锁",problem="钥匙来源不明",severity="blocking"),
            dict(id="I02",stage=stage_names[1],evidence="巡店检查",problem="开门顺序待核实",severity="blocking")]
    choices=[dict(issueId=i["id"],proposal="说明钥匙交接",repairScope="场01") for i in issues]
    report=dict(retelling="巡店后交接",scope="01–05",diagnosis="钥匙来源",issues=issues,options=choices)
    report_id,_=env.save_adopt(eid,"stress",report,"diagnosis")
    return env,eid,report_id


def discussion(req, answer):
    return dict(draft(req),kind="discussion",reply=json.dumps(answer,ensure_ascii=False),changes=[],directorRequest="")


@pytest.mark.parametrize('stage_names', [('world','treatment'), ('03世界观','04分场')])
def test_report_repair_followthrough(tmp_path, stage_names):
    env,eid,vid=fixture(tmp_path, stage_names=stage_names)
    wb=env.wb
    with pytest.raises(Rejected,match="I01.*I02"):
        wb.create_task(eid,"asset_design","按已采用的上游成果完成本阶段")
    start=wb.start_repair_plan(eid,"report","repair",vid,{"I01":0,"I02":0})
    env.codex.script=[lambda req:draft(req,rules=json.dumps([dict(id="r1",rule="柜台钥匙由值班员交接",choiceImpact="先检查门锁")]))]
    env.drain(start["runId"])
    plan=wb.stress_worklist(eid)["plans"][0]
    assert [s["stage"] for s in plan["steps"]]==["world","treatment","script","stress"]
    assert plan["steps"][0]["state"]=="adopt"
    world=env.art(eid,"world")
    counts=[len(env.art(eid,s)["versions"]) for s in ("treatment","script","stress")]
    progress=wb.adopt(world["id"],plan["steps"][0]["versionId"],world["adopted_version_id"],"apply")
    def compare(quote):
        return lambda req:discussion(req,[dict(id=i,note="核对了动作和次序",quote=quote) for i in ("I01","I02")])
    env.codex.script=[compare("巡店检查"),compare("巡店员检查门锁"),lambda req:discussion(req,[
        dict(id=i,note="已核对钥匙交接与检查顺序",status="已解决") for i in ("I01","I02")])]
    env.drain(progress["runId"])
    result=wb.stress_worklist(eid)
    assert result["plans"][0]["state"]=="done", result
    assert not result["blockers"]
    with env.db() as db:
        assert not db.execute("SELECT 1 FROM impact WHERE episode_id=? AND target_ref='workflow:stress' AND state='pending'",(eid,)).fetchone()
    assert counts==[len(env.art(eid,s)["versions"]) for s in ("treatment","script","stress")]
    assert wb.create_task(eid,"asset_design","按已采用的上游成果完成本阶段")["runId"]


def test_carry_requires_each_issue_and_early_never_supersedes(tmp_path):
    env,eid,vid=fixture(tmp_path)
    wb=env.wb
    wb.carry_stress_issue(eid,vid,"I01","","","without-note")
    assert [b["id"] for b in wb.stress_worklist(eid)["blockers"]]==["I02"]
    wb.carry_stress_issue(eid,vid,"I02","按导演意见保留","","with-note")
    assert not wb.stress_worklist(eid)["blockers"]
    run=wb.start_repair_plan(eid,"early","optional")["runId"]
    inputs=wb.run_manifest(run)["manifest"]["inputs"]
    assert not any(i["role"]=="adopted_script" for i in inputs)
    env.codex.script=[lambda req:discussion(req,dict(retelling="钥匙交接",scope="01–04 未含剧本",diagnosis="需核对顺序",issues=[],options=[]))]
    env.drain(run)
    assert wb.stress_worklist(eid)["plans"][0]["state"]=="done"
    assert env.art(eid,"stress")["adopted_version_id"]==vid


def test_superseded_repair_draft_cannot_be_adopted_after_restarting_step(tmp_path):
    env,eid,report=fixture(tmp_path)
    wb=env.wb
    start=wb.start_repair_plan(eid,"report","repair",report,{"I01":0})
    env.drain(start["runId"])
    step=wb.stress_worklist(eid)["plans"][0]["steps"][0]
    assert step["state"]=="adopt"
    replacement,_=env.save_adopt(eid,"world",dict(rules=[dict(id="r1",rule="钥匙放在柜台",choiceImpact="取钥匙")]),"replacement")
    wb.advance_repair_plan(start["planId"],"restart")
    with pytest.raises(Conflict,match="重新核对"):
        wb.adopt(env.art(eid,"world")["id"],step["versionId"],replacement,"stale")
