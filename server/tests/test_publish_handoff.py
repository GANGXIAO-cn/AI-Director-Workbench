import json
from pathlib import Path

import pytest

from helpers import Env
from workbench import deliverypage, publishing
from workbench.errors import Rejected


def test_published_music_and_handoff_use_frozen_sources(tmp_path):
    env=Env(tmp_path)
    episode=env.full_episode(segments={"G01":[8000],"G02":[7000]}, deliver=False)
    wb=env.wb
    eid=episode["eid"]
    artifact=env.art(eid,"publish")
    original=artifact["adopted_version_id"]
    settings=wb.compose_publish(eid,original)
    assert settings["basisFrozen"] and settings["musicState"]=="backup"
    assert [(r["segment"],r["at"]) for r in settings["cues"][:2]] == [
        ("G01","00:00.000–00:08.000"),("G02","00:08.000–00:15.000")]
    assert settings["cues"][-1]["action"].startswith("备用对位")
    assert settings["platforms"]["facebook"]["description"]==settings["platforms"]["youtube"]["description"]

    wb.save_step_config(eid,"plan",keep="完整保留阿福拉回抽屉时的表情")
    wb.create_group(episode["pid"],"夜班故事组",
                    [{"episodeId":eid,"chapter":"关门","ending":"抽屉回位","nextHook":"交接钥匙"}],shared_limits="柜台和道具位置承接",command_id="group")
    wb.mark_image(eid,episode["img"]["versionId"],"required",["S01"],requirement=dict(
        targetSegments=["G01"],reason="看清毛色和耳缘",fitness="limited",limitations="侧面仅作轮廓参考"))
    for stage in ("plan","prompts"):
        art=env.art(eid,stage)
        content=art["versions"][-1]["content"]
        where,quote=("S01",content["shots"][0]["action"]) if stage=="plan" else ("G01","阿福推回抽屉")
        content["decisionNotes"]=[dict(decision="D01",status="applied",where=where,quote=quote)]
        vid,_=env.save_adopt(eid,stage,content,"acknowledge-keep-"+stage)
    env.review_segments(vid,["G01","G02"],"requirements")
    delivery=wb.deliver(eid,"full","full-handoff")
    root=Path(delivery["path"])
    data=json.loads((root/"episode.json").read_bytes())
    hand=(root/"03_精剪交接.md").read_text()
    for phrase in ("完整保留阿福拉回抽屉时的表情","柜台和道具位置承接","看清毛色和耳缘","侧面仅作轮廓参考",
                   "采用梗概","接收说明","备用对位",original):
        assert phrase in hand
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]
    files=json.loads((root/"manifest.json").read_text())["files"]
    (root/"03_精剪交接.md").write_text(hand.replace("抽屉回位","跺脚"))
    with pytest.raises(ValueError, match="精剪交接"):
        deliverypage.verify(root,data,files)
    wb.save_step_config(eid,"plan",keep="改为看清阿福的前爪")
    assert data["workbench"]["finecut"]["directions"]["keeps"][0]["text"]=="完整保留阿福拉回抽屉时的表情"

    earlier_plan=episode["plan"]
    earlier_plan["shots"][0]["duration_ms"]=9000
    earlier_plan["decisionNotes"]=[dict(decision="D02",status="applied",where="S01",quote=earlier_plan["shots"][0]["action"])]
    env.save_adopt(eid,"plan",earlier_plan,"changed-clock")
    assert wb.compose_publish(eid,original)==settings
    assert any(p["stage"]=="publish" and "时窗" in p["problem"] for p in wb.delivery_problems(eid)[0])


def test_music_coverage_and_older_unscoped_cues():
    segments=[dict(segment="G01",at="00:00.000–00:08.000"),dict(segment="G02",at="00:08.000–00:15.000")]
    cue=dict(segment="G02",at="00:08.000",action="关门时硬停")
    with pytest.raises(Rejected,match="G01"):
        publishing.cue_rows([cue],"enabled",segments,True)
    old=dict(scene="结尾",at="00:10.000",action="渐弱")
    assert publishing.cue_rows([old],"enabled",segments,False)==[old]
    with pytest.raises(Rejected,match="使用状态"):
        publishing.cue_rows([],"unknown",segments,False)
    with pytest.raises(Rejected,match="段号"):
        publishing.cue_rows([dict(cue,segment="G09")],"enabled",segments,True)

    with pytest.raises(Rejected,match="00:08.000"):
        publishing.cue_rows([dict(cue,at="2–4秒")],"backup",segments,False)
    with pytest.raises(Rejected,match="起止时间"):
        publishing.cue_rows([dict(cue,at="开头过一会儿")],"enabled",segments,False)
    assert publishing.cue_rows([dict(cue,at="8.5–15秒")],"enabled",segments,False)[0]["action"] == "关门时硬停"
    assert publishing.cue_rows([old],"backup",segments,True,frozen=False)==[old]


def test_delayed_publish_run_and_restore_use_the_submitted_clock(tmp_path):
    env=Env(tmp_path)
    ep=env.full_episode(segments={"G01":[8000]},deliver=False)
    wb=env.wb
    eid=ep["eid"]
    art=env.art(eid,"publish")
    original=art["adopted_version_id"]
    run=wb.create_task(eid,"publish","沿用采用方案填写发布包")["runId"]
    frozen=wb.run_manifest(run)["manifest"]
    item=next(e for e in frozen["inputs"] if e["role"]=="publish_basis")
    sent=json.loads(wb.store.read_blob(item["sha256"]))
    ep["plan"]["shots"][0]["duration_ms"]=9000
    env.save_adopt(eid,"plan",ep["plan"],"different-clock")
    env.drain(run)
    with env.db() as db:
        generated = db.execute("SELECT id FROM version WHERE source_run_id=?", (run,)).fetchone()["id"]
    assert wb.compose_publish(eid,generated)["cueBasis"] == sent
    restored=wb.restore_version(original,original,"restore-before-change")["versionId"]
    assert wb.compose_publish(eid,restored)["cueBasis"] == sent
    fresh=wb.save_manual(art["id"],restored,dict(body="A different ending."),"manual")["versionId"]
    assert wb.compose_publish(eid,fresh)["cueBasis"]["segments"][0]["at"].endswith("00:09.000")


def test_legacy_project_settings_use_the_same_validation_on_save_and_read(tmp_path):
    from helpers import PUBLISH
    from workbench.db import transaction

    wb = Env(tmp_path).wb
    pid = wb.create_project("puggy", "项目发布字段")["projectId"]
    root = tmp_path / "profile"
    root.mkdir()
    (root / "publish.json").write_text(json.dumps(dict(PUBLISH, hook_emoji="🎬")))
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",
                   (json.dumps(dict(assetRoot=str(root), publishSettings="publish.json", publishCopyFormat="instrumental_v1")), pid))
    key = "publish:"+pid
    wb.put_setting(key, publishing.settings(PUBLISH))
    before = wb.publish_settings(pid)
    assert before["hook_emoji"] == "🎬" and before["copy_format"] == "instrumental_v1"
    with pytest.raises(Rejected, match="音乐字段"):
        wb.set_publish_settings(pid, dict(PUBLISH, additional_fields=[]))
    assert wb.publish_settings(pid) == before
    wb.set_publish_settings(pid, dict(PUBLISH, title_prefix="New "))
    assert wb.get_setting(key) == wb.publish_settings(pid)
    generic = wb.create_project("_template", "通用项目")["projectId"]
    wb.set_publish_settings(generic, dict(additional_fields=[]))
    assert wb.publish_settings(generic)["additional_fields"] == []
