import json

import pytest

from helpers import Env
from workbench.decisions import applicable_decisions
from workbench.errors import Rejected
from workbench.executors.fake import draft


def test_keep_sync_scope_frozen_text_and_current_adoption_gate(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    env.adopt_story(eid)
    wb.save_step_config(eid, "characters", keep="  保留门锁  \n不得新增人物\n", goal="只在这里解释")
    wb.save_step_config(eid, "characters", keep="保留门锁", expected_revision=1)
    rows = wb.director_decisions(eid)
    assert [(d["code"],d["text"],d["state"]) for d in rows] == [
        ("D01","保留门锁","active"),("D02","不得新增人物","revoked")]
    with env.db() as db:
        assert applicable_decisions(db,eid,"story") == []
        for stage in ["script","prompts","review"]:
            assert [d["code"] for d in applicable_decisions(db,eid,stage)] == ["D01"]
        assert applicable_decisions(db,eid,"publish") == []
    env.codex.script = [lambda req:draft(req, scenes=json.dumps([dict(id="场01",heading="便利店",action="他检查门锁。")]))]
    task = wb.create_task(eid,"script","按已采用的上游成果完成本阶段")
    env.drain(task["runId"])
    frozen = wb.store.manifest_texts(wb.run_manifest(task["runId"])["manifest"])
    entry, = [i for i in frozen if i["role"]=="director_decisions"]
    assert json.loads(entry["text"])[0]["text"]=="保留门锁"
    art = env.art(eid,"script")
    base = art["versions"][-1]["id"]
    assert not wb.adoption_gate(base)["formatErrors"]
    assert env.art(eid,"script")["tasks"][-1]["runs"][-1]["format_attempts"] == 1
    incomplete = wb.save_manual(art["id"], base, {"decisionNotes":[]}, "missing-note")["versionId"]
    assert "决定 D01 没有落点说明" in wb.adoption_gate(incomplete)["formatErrors"]
    note = dict(decision="D01",where="场01",status="applied",quote="从未写过的动作")
    def save(note,key):
        return wb.save_manual(art["id"],base,dict(scenes=[dict(id="场01",heading="便利店",action="他检查门锁。")],decisionNotes=[note],notes=note["quote"]),key)["versionId"]
    assert "决定 D01 的引文不在本稿中" in wb.adoption_gate(save(note,"bad-quote"))["formatErrors"]
    vid=save(dict(note,quote="他检查门锁。"),"good-quote")
    wb.adopt(art["id"],vid,None,"adopt-script")
    assert wb.director_decisions(eid)[0]["placements"][0]["versionId"]==vid
    wb.revoke_decision(rows[0]["id"],"revoke")
    assert any(i["kind"]=="decision_change" and i["target_ref"]=="workflow:script" for i in wb.impacts(eid))
    assert wb.input_text(task["runId"],entry["sha256"])["text"] == entry["text"]


def test_message_decision_keeps_full_quote_and_is_optional(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    instruction = "按已采用的上游成果完成本阶段"
    task = wb.create_task(eid, "story", instruction)
    env.drain(task["runId"])
    vid = env.art(eid, "story")["versions"][-1]["id"]
    text = "现在拍多久？台词保留原话"
    wb.post_message(task["taskId"], text, "new-msg", base_version_id=vid)
    wb.adopt(task["artifactId"], vid, None, "before")
    assert wb.director_decisions(eid) == []
    messages = wb.decision_candidates(task["artifactId"])
    message = next(m for m in messages if m["text"] == text)
    for command in ("once", "again"):
        wb.record_message_decision(eid, message["messageId"], command)
    decision, = wb.director_decisions(eid)
    assert (decision["text"], decision["scope_kind"]) == (text, "episode")
    following = wb.create_task(eid, "characters", instruction)
    inputs = wb.store.manifest_texts(wb.run_manifest(following["runId"])["manifest"])
    assert any(i["role"] == "director_decisions" and text in i["text"] for i in inputs)


def test_comparison_scopes_decisions_and_revoke_blocks_delivery(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    sample=env.full_episode(segments={"G01":[8000],"G02":[8000]},deliver=False)
    eid,vid=sample["eid"],sample["prompts"]
    plan=sample["plan"]
    plan["shots"][0]["origin"]["scene"]="场01"
    env.save_adopt(eid,"plan",plan,"change-scene")
    all_stages=wb.add_decision(eid,"保留冰柜声","plan","episode",[],"all")
    one=wb.add_decision(eid,"本场检查门锁","plan","scenes",["场01"],"one")
    for seg,expected in [("G01",["D01","D02"]),("G02",["D01"])]:
        t=wb.start_prompt_review(vid,seg,"comparison","scope-"+seg)
        entries=wb.store.manifest_texts(wb.run_manifest(t["runId"])["manifest"])
        item,=[i for i in entries if i["role"]=="director_decisions"]
        assert [d["code"] for d in json.loads(item["text"])]==expected
    result=wb.replace_decision(one["id"],"检查门锁后停一拍","scenes",["场01"],"replace")
    assert result["code"]=="D03"
    wb.revoke_decision(all_stages["id"],"revoke")
    assert any("D01 已撤销" in p["problem"] for p in wb.delivery_problems(eid,"full")[0])


def test_comparison_checks_each_required_setting_of_puggy(tmp_path):
    env = Env(tmp_path)
    sample = env.full_episode(config="puggy", segments={"G01": [8000]}, deliver=False)
    wb = env.wb
    episode = wb.episode_view(sample["eid"])
    required = {s["key"] for s in episode["kickoff"]["settings_snapshot"] if s["strength"] == "required"}
    assert required
    prompt = env.art(sample["eid"], "prompts")["versions"][-1]
    comparison = next(review for review in prompt["reviews"] if review["pass"] == "comparison")
    covered = {o["objectId"] for o in comparison["observations"]}
    assert required | {"G01"} <= covered
    cold = next(review for review in prompt["reviews"] if review["pass"] == "cold_read")
    assert {o["objectId"] for o in cold["observations"]} == {"G01"}

def test_operational_messages_do_not_interrupt_adopt_and_continue(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    with env.db() as db:
        source = db.execute("SELECT idea_text FROM kickoff WHERE episode_id=?", (eid,)).fetchone()[0]
    task = wb.create_task(eid, "story", source)
    env.drain(task["runId"])
    art = env.art(eid, "story")
    vid = art["versions"][-1]["id"]
    for i, text in enumerate(("请基于 R01 修订所选的 3 条审查意见，逐条核对定位和依据，提交一份完整新稿。",
                              "回答我的问题，当前设定大概可以拍多长时长？", "全片要不要保留关门声？")):
        wb.post_message(task["taskId"], text, "operational-"+str(i), base_version_id=vid)
    assert len(wb.decision_candidates(art["id"])) >= 3
    result = wb.adopt(art["id"], vid, None, "continue", and_continue=True)
    assert result["next"]["stage"] == "characters"
