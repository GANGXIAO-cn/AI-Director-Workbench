from workbench.errors import Rejected
import pytest


from helpers import Env


def test_archive_changes_research_inputs_and_retry(tmp_path):
    env=Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    tid = wb.add_topic(pid, "T01", "测试分集独有的玻璃茶壶")["topicId"]
    wb.add_topic(pid, "T02", "正式候选的自行车")
    with env.db() as db:
        db.execute("UPDATE topic SET episode_id=? WHERE id=?", (eid, tid))

    def sources(result):
        manifest, _ = wb.store.load_manifest("runs/"+result["turnId"]+"/input-manifest.json")
        return [e["name"] for e in manifest["inputs"] if e["role"]=="research_source"]

    first = wb.start_topic_research(pid, "创作", "codex", "start")
    assert "正式候选 · T01" in sources(first)
    wb.stop_free_chat(pid, first["turnId"], first["attemptId"])
    wb.set_archived("episode", [eid], True)
    again = wb.retry_free_chat(pid, first["turnId"], first["attemptId"], "retry")
    assert sources(again) == ["正式候选 · T02", "导演的研究方向"]
    assert wb.similar_topics(pid,"测试分集独有的玻璃茶壶")==[]
    assert wb.project_view(pid)["episodes"]==[]
    assert wb.project_view(pid)["reservedEpisodeCodes"]==["E01"]
    wb.set_archived("project",[pid],True)
    assert not wb.list_projects()
    with pytest.raises(Rejected):
        wb.start_topic_research(pid,"测试","codex","archived")
    wb.set_archived("project",[pid],False)
    assert not wb.project_view(pid)["episodes"]
    wb.set_archived("episode",[eid],False)
    assert wb.similar_topics(pid,"测试分集独有的玻璃茶壶")[0]["code"]=="T01"
    wb.set_topic_intent(tid,"弃用")
    assert not wb.similar_topics(pid,"测试分集独有的玻璃茶壶")


def test_short_research_reply_fills_only_missing_cards(tmp_path):
    import json
    env = Env(tmp_path)
    pid, _ = env.new_episode()
    def cards(names):
        return {"candidates":[dict(title=name, premise=name, mechanism="追赶", motivation="取回", difference="结局不同", reference_id="R1") for name in names]}
    env.codex.script = [lambda req: cards("甲乙丙"), lambda req: cards("丁戊")]
    turn = env.wb.start_topic_research(pid, "生成5个选题", "codex", "five")
    env.wb.execute_free_chat(turn["turnId"], turn["attemptId"])
    with env.db() as db:
        result = db.execute("SELECT status,reply,reason FROM free_turn WHERE id=?", (turn["turnId"],)).fetchone()
    assert result["status"] == "succeeded", result["reason"]
    assert [card["title"] for card in json.loads(result["reply"])] == list("甲乙丙丁戊")
    assert env.codex.calls[-1].schema["properties"]["candidates"]["minItems"] == 2


def test_delivered_topic_needs_manual_restore(tmp_path):
    from workbench.errors import Conflict
    env=Env(tmp_path)
    wb=env.wb
    pid,eid=env.new_episode()
    tid=wb.add_topic(pid,"研002","营救小毛驴")["topicId"]
    wb.set_topic_intent(tid,"想拍")
    with env.db() as db:
        db.execute("UPDATE topic SET episode_id=?,status='已制作' WHERE id=?",(eid,tid))
    card=wb.topics(pid)[0]
    assert card["intent"]=="已交付" and card["episode_id"]==eid
    with env.db() as db, pytest.raises(Conflict):
        wb._topic_on_kickoff(db,eid,tid)
    new=wb.add_topic(pid,"",card["premise"],origin="restore:"+tid)
    assert wb.add_topic(pid,"",card["premise"],origin="restore:"+tid)==new
    cards={t['id']:t for t in wb.topics(pid)}
    assert cards[new['topicId']]['intent']=='想拍' and cards[new['topicId']]['episode_id'] is None
    assert cards[tid]['episode_id']==eid and cards[tid]['status']=='已制作'


def test_expert_delivery_is_filmed_and_blocks_repeat(tmp_path):
    import json
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    premise = "钟楼停摆，MILO拉响大钟，最后倒挂。"
    tid = wb.add_topic(pid, "研003", premise)["topicId"]
    with env.db() as db:
        db.execute("UPDATE topic SET episode_id=?,status='已制作' WHERE id=?", (eid, tid))
        db.execute("INSERT INTO delivery VALUES('full',?,'full','[]','hash','path','deliverable','{}','cmd',1)", (eid,))
        db.execute("INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,created_at,updated_at) VALUES ('expert',?,'delivery','done',?,1,2)", (eid,json.dumps(dict(deliveryId="full"))))
    assert wb.topics(pid)[0]["intent"] == "已拍"
    def card(title):
        return dict(title=title,premise=title,mechanism="机关",motivation="寻宝",difference="不同收尾",reference_id="R1")
    env.codex.script = [lambda req: {"candidates":[card(s) for s in [premise,"甲","乙","丙","丁"]]},
                        lambda req: {"candidates":[card("戊")]}]
    turn = wb.start_topic_research(pid,"做5个长篇选题，适合4分钟","codex","long")
    wb.execute_free_chat(turn["turnId"],turn["attemptId"])
    assert "已拍作品 · 研003" in env.codex.calls[0].prompt
    assert env.codex.calls[-1].schema["properties"]["candidates"]["minItems"] == 1
    assert [c["title"] for c in wb.topic_researches(pid)["runs"][0]["candidates"]] == list("甲乙丙丁戊")
