import json
from pathlib import Path

import pytest

from helpers import Env
from workbench import rhythm, shotrefs
from workbench.errors import Rejected


def test_model_windows_merge_with_existing_board_and_reject_incomplete_scope(tmp_path):
    plan = Env(tmp_path).plan86({"G01": [2000, 3000], "G02": [4000]})
    original = rhythm.plan_rows(plan)
    original[-1]["镜头S"] = original[0]["镜头S"]
    plan["rhythmTable"] = rhythm.markdown(original)
    assert not shotrefs.plan_enabled(plan)
    plan["shots"][0].update(visibleRoles=["阿福"], propStates={"crate": "空"})
    assert shotrefs.plan_enabled(plan)
    with pytest.raises(ValueError, match="S02"):
        shotrefs.board_rows(plan)
    for shot in plan["shots"][1:]:
        shot.update(visibleRoles=[], propStates={})
    rows = shotrefs.board_rows(plan)
    assert [(r[shotrefs.ROLES], r[shotrefs.PROPS]) for r in rows] == [("阿福", "crate=空"), ("无", "无"), ("无", "无")]
    assert rows[-1]["镜头S"] == rows[0]["镜头S"]
    rows[0][shotrefs.ROLES] = "无"
    rows[0][shotrefs.PROPS] = "crate=满"
    plan["rhythmTable"] = rhythm.markdown(rows)
    with pytest.raises(ValueError, match="结构化镜头"):
        shotrefs.board_rows(plan)
    synced = shotrefs.apply_table_edit(plan)
    assert synced["shots"][0]["visibleRoles"] == []
    assert synced["shots"][0]["propStates"] == {"crate": "满"}
    assert shotrefs.board_rows(synced) == rows
    plan["shots"][0]["visibleRoles"] = ["阿福", "阿福"]
    with pytest.raises(ValueError, match="不重复"):
        shotrefs.board_rows(plan)
    assert not shotrefs.enabled({"segments": [None, {"references": None}]})


def test_continuation_scope_is_segment_local_and_observations_are_versioned(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    ep = env.full_episode(segments={"G01": [3000, 2000, 3000], "G02": [6000]}, deliver=False)
    eid, face, plan = ep["eid"], ep["img"]["versionId"], ep["plan"]
    for shot in plan["shots"]:
        shot.update(visibleRoles=[], propStates={})
    plan["shots"][2].update(continuation="same", visibleRoles=["阿福"])
    env.save_adopt(eid, "plan", plan, "model-scopes")
    wb.set_role_baseline(eid, "阿福", face, "阿福", "baseline")
    wb.set_cast(eid, "G01", "阿福", "ok", face)
    wb.set_cast(eid, "G02", "阿福", "absent")
    scene = env.image(ep["pid"], eid, name="空景", category="scene", rgb=(200, 210, 230))["versionId"]
    art = env.art(eid, "prompts")
    prompts = env.prompts_for(plan, {"G01": face, "G02": scene}, {"G02": "【开场】便利店夜。\n00:00.000-00:06.000 图片1 中空店亮着灯。"})
    prompts["segments"]["G02"]["refs"][0]["role"] = "空店外观"
    base = wb.save_manual(art["id"], ep["prompts"], prompts, "native-prompts")["versionId"]
    assert wb.shot_reference_view(base, "G01")["suggestions"] == {"阿福": ["S02"]}
    def observe(version, segment, image, window, note, cid, sha=None):
        return wb.save_shot_observation(version, segment, image, sha or wb.asset_version(image)["sha256"],
                                        [window], note, {}, [], command_id=cid)["versionId"]
    with pytest.raises(Rejected, match="原图不一致"):
        observe(base, "G01", face, "S03", "第一张测试色块", "bad", sha="wrong")
    face_checked = observe(base, "G01", face, "S03", "第一张测试色块", "face")
    assert face_checked == observe(base, "G01", face, "S03", "第一张测试色块", "face")
    both = observe(face_checked, "G02", scene, "S04", "第二张测试色块", "scene")
    assert not wb.adoption_gate(both)["formatErrors"]
    wb.adopt(art["id"], both, ep["prompts"], "adopt-both")
    env.review_segments(both, ["G01", "G02"])
    trial = wb.deliver(eid, "trial", "subset", ["G01"])
    package = json.loads((Path(trial["path"]) / "episode.json").read_bytes())
    assert set(package["workbench"]["shotReferencePackets"]) == {"G01"}
    assert package["segments"][0]["references"][0]["shots"] == ["S02"]
    assert wb.verify_delivery(trial["deliveryId"])["ok"]
    changed = observe(both, "G01", face, "S03", "重新核对第一张色块", "updated")
    status = wb.prompt_review_status(changed)
    assert status["G01"]["comparison"]["state"] == "needed"
    assert status["G02"]["comparison"]["state"] == "passed"
    assert status["G01"]["coldRead"]["state"] == "passed"
