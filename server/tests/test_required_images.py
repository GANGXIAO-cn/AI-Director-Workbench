import json
from pathlib import Path

import pytest

from helpers import Env
from workbench.errors import Conflict, Rejected


def test_material_status_checks_required_image_in_the_requested_segment(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    case = env.full_episode(segments={"G01": [8000], "G02": [8000]}, deliver=False)
    eid = case["eid"]
    first = case["img"]["versionId"]
    second = env.image(case["pid"], eid, name="第二段场景", rgb=(70, 80, 90))["versionId"]
    prompts = env.prompts_for(case["plan"], {"G01": first, "G02": second})
    env.save_adopt(eid, "prompts", prompts, "different-images")
    for target, complete in [("G02", False), ("G01", True)]:
        wb.mark_image(eid, first, "required", requirement=dict(reason="保留身份图", targetSegments=[target]))
        assert wb.episode_view(eid)["productionStatus"]["assets"] is complete


def test_episode_images_are_owned_by_the_upload_and_adoption_project(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    active = env.full_episode(segments={"G01":[8000]}, deliver=False)
    pid, eid = active["pid"], active["eid"]
    other_pid, foreign_episode = env.new_episode(name="另一个项目")
    _, sibling = env.new_episode(project_id=pid, code="E02")
    with pytest.raises(Rejected, match="不属于该项目"):
        env.image(other_pid, eid)
    foreign = env.image(other_pid, foreign_episode)
    local = active["img"]["versionId"]
    for target, version in [(eid,foreign["versionId"]), (foreign_episode,local), (sibling,local)]:
        for confirm in (False, True):
            with pytest.raises(Rejected):
                wb.adopt_image(target, version, "invalid", confirm=confirm)
        with pytest.raises(Rejected):
            wb.mark_image(target, version, "required")
    with env.db() as db:
        assert db.execute("SELECT count(*) FROM image_choice").fetchone()[0] == 0
        # Pre-fix records with mismatched ownership must stay out of new episode inputs.
        db.execute("UPDATE asset SET episode_id=? WHERE id=?", (eid, foreign["assetId"]))
        db.execute("INSERT INTO image_choice VALUES(?,?,?,?,?,?)",
                   ("old-mark",eid,foreign["versionId"],"required","[]",0))
    assert [r["id"] for r in wb.episode_assets(eid)] == [local]
    refs = env.prompts_for(active["plan"], foreign["versionId"])["segments"]
    with env.db() as db:
        assert any("不在本集" in e for e in wb._prompt_reference_errors(db, eid, refs))
    check, context = wb.delivery_problems(eid)
    assert not check and not context["allRequirements"]
    preview = wb.adopt_image(eid,local,"correct")
    assert [r["segment"] for r in preview["references"]] == ["G01"]
    assert preview["references"][0]["shots"] == [] and preview["references"][0]["promptVersionId"] == active["prompts"]
    changed = env.prompts_for(active["plan"],local)
    changed["segments"]["G01"]["txt"] += "\n镜头最后停在门口。"
    changed["segments"]["G01"]["refs"][0]["shots"] = ["S01"]
    env.save_adopt(eid,"prompts",changed,"new-citation")
    with pytest.raises(Conflict, match="引用已变化"):
        wb.adopt_image(eid,local,"correct",confirm=True,expected=preview["fingerprint"])
    updated = wb.adopt_image(eid,local,"correct")
    ref, = updated["references"]
    assert ref["shots"] == ["S01"] and ref["txt"] == changed["segments"]["G01"]["txt"]
    result = wb.adopt_image(eid,local,"correct",confirm=True,expected=updated["fingerprint"])
    assert result["versionId"] == local and not result["recheck"]
    assert wb.adopt_image(eid,local,"correct",confirm=True,expected=updated["fingerprint"]) == result


def test_selection_changes_while_exporting_and_legacy_marks(tmp_path, monkeypatch):
    env = Env(tmp_path)
    wb = env.wb
    sample = env.full_episode(segments={"G01":[8000]}, deliver=False)
    eid, vid = sample["eid"], sample["img"]["versionId"]
    write = wb._write_delivery

    def concurrent_mark(*args):
        files = write(*args)
        wb.mark_image(eid,vid,"required")  # old API has no provenance details
        return files

    with monkeypatch.context() as change:
        change.setattr(wb, "_write_delivery", concurrent_mark)
        with pytest.raises(Rejected, match="必用图发生变化"):
            wb.deliver(eid,"full","race")
    assert not wb.list_projects()[0]["deliveries"]
    legacy = next(a for a in wb.episode_assets(eid) if a["id"]==vid)["requirement"]
    assert legacy["selectedBy"]=="unrecorded" and not legacy["targetSegments"]
    env.review_segments(sample["prompts"], ["G01"], cid="legacy")
    delivery = wb.deliver(eid,"full","race")
    snapshot = json.loads((Path(delivery["path"])/"episode.json").read_text())
    assert snapshot["workbench"]["requiredImages"][0]["usedHere"]==["G01"]
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]


def test_required_image_in_creator_input_and_draft(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    case = env.full_episode(segments={"G01":[8000]},deliver=False)
    eid, pid = case["eid"], case["pid"]
    version = env.image(pid,eid,name="站台门",category="scene",rgb=(2,30,12))["versionId"]
    base = dict(reason="保留站台门",targetSegments=["G01"],fitness="limited",limitations="只参考门的位置")
    with pytest.raises(Rejected, match="目标段不存在"):
        wb.mark_image(eid,version,"required",requirement=dict(base,targetSegments=["G02"]))
    wb.mark_image(eid,version,"required",requirement=base)
    made = wb.draft_prompts(eid)["segments"]["G01"]
    assert version in [r["assetVersionId"] for r in made["refs"]]
    assert made["txt"].count("只参考门的位置")==1
    task = wb.create_task(eid,"prompts","依据站台门的实际用途起草")
    entries = wb.run_manifest(task["runId"])["manifest"]["inputs"]
    item, = [x for x in entries if x["role"]=="required_images"]
    frozen, = json.loads(wb.store.read_blob(item["sha256"]))
    assert frozen["versionId"]==version and frozen["targetSegments"]==["G01"]
