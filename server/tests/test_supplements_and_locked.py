import ast
import json
import re
from pathlib import Path

import pytest

from helpers import Env
from test_unified_workflow import episode
from workbench.errors import Rejected
from workbench.executors.fake import draft
from workbench.stages import locked_script_errors


def test_locked_original_partitions_and_leftovers():
    driver=(Path(__file__).resolve().parents[2]/"validation/workflows/unified_real.cjs").read_text()
    source=ast.literal_eval(re.search(r"const source = ('# 《最后一格电》.*?');",driver)[1])
    lines=source.splitlines()
    scenes=[dict(id=f"场{i:02}",heading=lines[i*2],action=lines[i*2+1]) for i in range(1,5)]
    errors,remainder=locked_script_errors(source,scenes)
    assert not errors and remainder.strip().startswith(lines[0]) and remainder.strip().endswith(lines[-1])
    assert [s["id"] for s in scenes if "从手袋拿出饭盒" in s["action"]]==["场03"]
    for changed in ([*reversed(scenes)], [dict(scenes[0],action=scenes[0]["action"].replace("黑屏","蓝屏"))],
                    [dict(scenes[0],omit="过程")], [dict(scenes[1],dialogue="从手袋拿出饭盒")]):
        assert locked_script_errors(source,changed)[0]


def test_locked_model_old_fields_are_blocked(tmp_path):
    wb,_,eid=episode(tmp_path,locked=True)
    invalid = lambda req:draft(req,scenes=json.dumps([dict(id="场01",heading="渡口 黄昏",action="学徒提着蛋糕赶来。",omit="可省略")]))
    wb.executors["codex"].script=[invalid, invalid]
    t=wb.create_task(eid,"script","按已采用的上游成果完成本阶段")
    wb.execute_run(t["runId"])
    art = wb.episode_view(eid)["artifacts"][0]
    assert not art["versions"]
    assert "定稿导入的场次不得包含模型补写字段：omit" in art["tasks"][-1]["runs"][-1]["diagnostic"]


def test_supplement_registry_and_conflict(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    _,eid=env.new_episode()
    env.adopt_story(eid)
    content=dict(scenes=[dict(id="场01",heading="夜",action="他拿出手机。")])
    item=dict(subject="手机.位置",statement="手机在左侧口袋",scenes=["场01"],reason="剧本未说明")
    env.save_adopt(eid,"script",dict(content,supplements=[item]))
    env.prepare_stage(eid,"plan")
    record,=wb.production_supplements(eid)
    assert record["state"]=="proposed"
    run=wb.create_task(eid,"plan","按已采用的上游成果完成本阶段")
    assert any(i["role"]=="production_supplements" for i in wb.run_manifest(run["runId"])["manifest"]["inputs"])
    assert any("未经导演确认" in i["problem"] for i in wb.delivery_problems(eid)[0])
    plan=env.plan86({"G01":[8000]})
    plan["shots"][0]["origin"]["scene"]="场01"
    plan["supplements"]=[dict(item,statement="手机在手袋中")]
    aid=run["artifactId"]
    vid=wb.save_manual(aid,None,plan,"different")["versionId"]
    assert any("S01" in e and "说法不同" in e for e in wb.adoption_gate(vid)["formatErrors"])
    wb.decide_supplement(record["id"],"replace","手机在手袋中","replace")
    wb.adopt(aid,vid,None,"apply")
    assert [r["state"] for r in wb.production_supplements(eid)]==["superseded","confirmed"]
    assert any(i["kind"]=="supplement_change" and i["target_ref"]=="workflow:script" for i in wb.impacts(eid))
    assert not any("未经导演确认" in i["problem"] for i in wb.delivery_problems(eid)[0])


def test_fixed_and_episode_roles_and_verified_state_inputs(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    pid,eid=env.new_episode(config="puggy")
    fixed=wb.use_fixed_role(eid,"milo","fixed")["versionId"]
    with pytest.raises(Rejected,match="项目标准"):
        wb.set_role_baseline(eid,"milo",fixed,"白色长毛猫","wrong")
    bound=wb.set_role_baseline(eid,"milo",fixed,command_id="correct")
    with env.db() as db:
        assert db.execute("SELECT appearance FROM episode_role_baseline WHERE role='milo'").fetchone()[0]=="characterStandard#/roles/milo/video_zh"
    assert wb.cast_matrix(eid)["roster"]["milo"]["appearance"]==bound["appearance"]
    env.adopt_story(eid)
    env.script(eid)
    design=dict(strategy="lightweight", assets=[
        dict(id="milo-wet",roleId="milo",stateId="wet",kind="state",description="卫衣袖口被雨水打湿",imagePrompt="湿袖口"),
        dict(id="visitor",roleId="visitor",kind="identity",description="访客穿灰外套",imagePrompt="灰外套")])
    env.save_adopt(eid,"asset_design",design,"states")
    local=env.image(pid,eid,"访客","identity",rgb=(1,2,3))["versionId"]
    assert wb.set_role_baseline(eid,"visitor",local,command_id="default")["appearance"]=="访客穿灰外套"
    assert wb.set_role_baseline(eid,"visitor",local,"访客穿蓝外套","local")["appearance"]=="访客穿蓝外套"
    state,=wb.cast_matrix(eid)["roleStates"]
    assert not state["confirmed"]
    img=env.image(pid,eid,"湿袖口","state")["versionId"]
    wb.confirm_role_state(eid,"milo","wet",state["designHash"],img,"袖口深色水迹","verify")
    env.save_adopt(eid,"plan",env.plan86({"G01":[8000]}),"test-plan")
    for stage in ("plan","prompts"):
        task=wb.create_task(eid,stage,"按已采用的上游成果完成本阶段")
        entries=wb.store.manifest_texts(wb.run_manifest(task["runId"])["manifest"])
        states,=[json.loads(i["text"]) for i in entries if i["role"]=="role_states"]
        assert states[0]["confirmed"] and states[0]["imageId"]==img
        assert states[0]["description"]=="卫衣袖口被雨水打湿"


@pytest.mark.parametrize("corrected", [True, False])
def test_asset_supplement_reason_checked_before_saving(tmp_path, corrected):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    env.adopt_story(eid)
    item = dict(subject="手机.位置", statement="手机在左侧口袋", scenes=["场01"], reason="拍摄时须保持连续")
    env.save_adopt(eid, "script", dict(scenes=[dict(id="场01", heading="夜", action="他拿出手机。")], supplements=[item]))
    wb.set_optional_stage(eid, "stress", True, "测试素材收稿")
    task = wb.create_task(eid, "asset_design", "整理道具")
    inputs = wb.store.manifest_texts(wb.run_manifest(task["runId"])["manifest"])
    record, = json.loads(next(i["text"] for i in inputs if i["role"]=="production_supplements"))
    assert record["reason"] == item["reason"]
    new_item = dict(item, subject="手机.颜色",statement="手机为黑色")
    incomplete = {k:v for k,v in new_item.items() if k!="reason"}
    def reply(req, supplement):
        return draft(req, strategy="lightweight", assets=json.dumps([dict(id="phone",kind="prop",description="手机",imagePrompt="黑色手机")]), supplements=json.dumps([supplement]))
    env.codex.script = [lambda req: reply(req, incomplete), lambda req: reply(req, new_item if corrected else item)]
    env.drain(task["runId"])
    versions = env.art(eid, "asset_design")["versions"]
    assert len(versions) == int(corrected)
    with env.db() as db:
        run = db.execute("SELECT * FROM run WHERE id=?", (task["runId"],)).fetchone()
        assert run["format_attempts"] == 1
        assert run["status"] == ("succeeded" if corrected else "failed")
