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
        return {"candidates":[dict(title=name, premise=name, mechanism="追赶", motivation="取回", difference="结局不同", reference_id="R1",runtime=dict(form="长篇",minSeconds=240,maxSeconds=240)) for name in names]}
    env.codex.script = [lambda req: cards("甲乙丙"), lambda req: cards("丁戊")]
    turn = env.wb.start_topic_research(pid, "生成3个选题", "codex", "five", candidate_count=5)
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
        return dict(title=title,premise=title,mechanism="机关",motivation="寻宝",difference="不同收尾",reference_id="R1",runtime=dict(form="长篇",minSeconds=240,maxSeconds=240))
    env.codex.script = [lambda req: {"candidates":[card(s) for s in [premise,"甲","乙","丙","丁"]]},
                        lambda req: {"candidates":[card("戊")]}]
    turn = wb.start_topic_research(pid,"做5个长篇选题，适合4分钟","codex","long")
    wb.execute_free_chat(turn["turnId"],turn["attemptId"])
    assert "已拍作品 · 研003" in env.codex.calls[0].prompt
    assert env.codex.calls[-1].schema["properties"]["candidates"]["minItems"] == 1
    assert [c["title"] for c in wb.topic_researches(pid)["runs"][0]["candidates"]] == list("甲乙丙丁戊")


def test_topic_runtime_survives_kickoff_and_legacy_topics(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid = wb.create_project("_template", "片长验证")["projectId"]
    old = wb.add_topic(pid, "旧01", "一张旧卡")["topicId"]
    run = wb.start_topic_research(pid, "自主选题", "codex", "research")
    wb.execute_free_chat(run["turnId"], run["attemptId"])
    card = wb.topic_researches(pid)["runs"][0]["candidates"][0]
    tid = wb.accept_research_topic(pid, run["turnId"], 0, "新01", "accept")["topicId"]
    topics = {t["id"]: t for t in wb.topics(pid)}
    assert topics[tid]["runtime"] == card["runtime"] == dict(form="短篇", minSeconds=60, maxSeconds=90)
    assert topics[old]["runtime"] is None
    eid = wb.create_episode(pid, "E01")["episodeId"]
    wb.start_episode(eid, "topic", dict(create=dict(provider="codex"), review=dict(provider="claude")), topic_id=tid)
    view = wb.episode_view(eid)
    assert view["topicRuntime"] == card["runtime"]
    with env.db() as db:
        assert "短篇 · 建议成片 60–90 秒" in wb._source_text(db, view["kickoff"])
    with pytest.raises(Rejected):
        wb.add_topic(pid, "无效", "时长倒置", runtime=dict(form="长篇", minSeconds=240, maxSeconds=180))
    assert len(wb.topics(pid)) == 2


def test_style_brief_and_explicit_count_retry(tmp_path):
    from workbench.topicresearch import requested_count

    assert requested_count("为我再设计5个大镖客风格的选题，都是短篇，没有女主人参与故事") == 5
    env = Env(tmp_path)
    wb = env.wb
    pid, _ = env.new_episode()
    run = wb.start_topic_research(pid, "短篇喜剧", "codex", "first", candidate_count=5)
    wb.stop_free_chat(pid, run["turnId"], run["attemptId"])
    retry = wb.retry_free_chat(pid, run["turnId"], run["attemptId"], "retry")
    wb.execute_free_chat(retry["turnId"], retry["attemptId"])
    assert len(wb.topic_researches(pid)["runs"][0]["candidates"]) == 5
    with pytest.raises(Rejected):
        wb.start_topic_research(pid, "短篇喜剧", "codex", "invalid", candidate_count=0)
