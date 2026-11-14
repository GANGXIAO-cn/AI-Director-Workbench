import json
from copy import deepcopy

import pytest

from helpers import Env
from workbench.errors import Rejected
from workbench import rhythm


def test_adoption_and_scoped_delivery_check_real_prompt_against_plan(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01": [3000, 5000], "G02": [8000]})
    asset = env.image(pid, eid, category="state")["versionId"]
    for shot in plan["shots"]:
        shot["startAssetId"] = asset
    plan["shots"][1].update(continuation="same", holdReason="等手移开", txtAnchor="动作 2")
    pv, _ = env.save_adopt(eid, "plan", plan)
    content = wb.draft_prompts(eid)
    for i, (old, new, error) in enumerate([
        ("同镜延续，", "切镜，", "表文接续标记"),
        ("00:03.000", "00:04.000", "联合表与正文时间不同"),
    ]):
        edited = deepcopy(content)
        entry = edited["segments"]["G01"]
        assert old in entry["txt"]
        entry["txt"] = entry["txt"].replace(old, new)
        changed, _ = env.save_adopt(eid, "prompts", edited, "auxiliary"+str(i))
        assert wb.rhythm_review(changed)['report']['unparsed_segments']
    content['segments']['G01']['txt'] = content['segments']['G01']['txt'].replace('动作 2', '另一只手')
    version, _ = env.save_adopt(eid, "prompts", content)
    report = wb.rhythm_review(version)
    assert report["report"]["shot_seconds"] == {"G01":[8], "G02":[8]}
    assert not any(w["id"] == "长镜/G01/S01" for w in report["report"]["warnings"])
    raw = wb.rhythm_review(pv)["markdown"].replace("0–3","0–2")
    with pytest.raises(Rejected, match="动作窗或接续方式"):
        env.save_adopt(eid, "plan", {"rhythmTable":raw}, "invalid_table")
    env.review_segments(version, ["G01"])
    delivery = wb.deliver(eid, "trial", "export", ["G01"])
    dest = wb.store.root / wb.delivery_view(delivery["deliveryId"])["relpath"]
    manifest = json.loads((dest/"episode.json").read_text())
    assert manifest["shot_rhythm"]["version"] == 2
    assert "G02" not in (dest/manifest["shot_rhythm"]["plan"]).read_text()
    assert json.loads((dest/"镜头节奏核对.json").read_text())["segment_cut_counts"] == {"G01":0}
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]
    edited = deepcopy(content["segments"])
    edited["G02"]["txt"] = "故障"
    assert rhythm.check(plan, edited, ["G01"])["report"]["segment_shot_counts"] == {"G01":1}
    edited["G02"].pop("prompt_format")
    assert rhythm.check(plan, edited)["legacy"] == ["G02"]
    edited["G02"] = env.prompts_for(plan, asset)["segments"]["G02"]
    mixed, _ = env.save_adopt(eid, "prompts", {"segments":edited}, "mixed-prompts")
    env.review_segments(mixed, ["G01","G02"])
    delivery = wb.deliver(eid, "trial", "mixed-export", ["G01", "G02"])
    dest = wb.store.root / wb.delivery_view(delivery["deliveryId"])["relpath"]
    manifest = json.loads((dest/"episode.json").read_text())
    assert "shot_rhythm" not in manifest
    assert manifest["workbench"]["partialShotRhythm"]["segments"] == ["G01"]
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]


def test_segment_first_window_uses_opening_marker_after_split(tmp_path):
    env=Env(tmp_path)
    plan=env.plan86({'G01':[8000], 'G02':[8000]})
    plan['shots'][1]['continuation']='same'
    rows=rhythm.plan_rows(plan)
    assert rows[1]['接续方式']=='起镜'


def test_flex_body_bracket_annotation_is_not_a_section():
    from workbench import readable
    text=readable.render({title:'【主观视点】普通正文。' for title in readable.FLEX})
    assert len(readable.sections(text,'flex'))==len(readable.FLEX)
    with pytest.raises(ValueError,match='栏目缺失'):
        readable.sections(text.replace('【开场】',''),'flex')
