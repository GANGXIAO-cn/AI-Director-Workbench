"""§10 端到端验收：each test is one row of the product design's acceptance table (FakeExecutor /
FakeImageProvider). Assertions read back DB rows, adoption pointers, input manifests and files on disk."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import zipfile

import pytest

from helpers import PUBLISH, SEGMENTS_86, Env
from workbench import timeline
from workbench.delivery import DEFAULT_PACKAGER
from workbench.executors.fake import clarify, draft, proposal, quota_exhausted, review
from workbench.images import png_bytes
from workbench.service import Conflict, Rejected


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def manifest_text(wb, run_id):
    m = wb.run_manifest(run_id)["manifest"]
    return m, "\n".join(e["text"] or "" for e in wb.store.manifest_texts(m))


# 01 ---------------------------------------------------------------------------------------------
def test_01_puggy_import_kickoff(env):
    wb = env.wb
    pid = wb.create_project("puggy")["projectId"]
    n06 = wb.create_episode(pid, "N06", leftover_facts=["厨房门上了锁"])["episodeId"]
    n07 = wb.create_episode(pid, "N07")["episodeId"]
    imp = wb.import_file(pid, "N07_洗车店.md", "MILO 给车洗澡。".encode(), "story_source", n07)["importId"]
    preview = wb.kickoff_preview(n07, "continue", [n06])      # N07 开工页先出现
    assert preview["episode"]["display_code"] == "N07" and preview["carriedFacts"] == ["厨房门上了锁"]
    wb.start_episode(n07, "import", {"create": {"provider": "codex"}, "review": {"provider": "claude"}},
                     source_import_id=imp, relation="continue", predecessor_ids=[n06])
    t = wb.create_task(n07, "story", "写故事")
    env.drain(t["runId"])
    view = wb.episode_view(n07)
    assert view["sourceImport"]["original_name"] == "N07_洗车店.md" and view["kickoff"]["carried_facts"] == ["厨房门上了锁"]
    assert view["artifacts"][0]["episode_id"] == n07 and view["artifacts"][0]["versions"]


# 02 ---------------------------------------------------------------------------------------------
def test_02_script_import_is_story_source(env):
    wb = env.wb
    pid = wb.create_project("_template", "雨巷")["projectId"]
    ep = wb.create_episode(pid, "E01")["episodeId"]
    imp = wb.import_file(pid, "雨巷.md", "场01 雨巷，阿岚撑伞。".encode(), "script_source", ep)["importId"]
    wb.start_episode(ep, "import", {"create": {"provider": "codex"}, "review": {"provider": "claude"}}, source_import_id=imp)
    assert wb.read_import(imp)["parsed_text"] == "场01 雨巷，阿岚撑伞。"
    t = wb.create_task(ep, "story", "整理故事")
    env.drain(t["runId"])
    with pytest.raises(Rejected):
        wb.create_task(ep, "script", "写剧本")             # 故事未过独审时停在故事阶段
    story = env.art(ep, "story")
    env.drain(wb.submit_review(story["versions"][0]["id"], "r")["runId"])
    res = wb.adopt(story["id"], story["versions"][0]["id"], None, "a", and_continue=True)
    assert res["next"]["stage"] == "characters"
    assert wb.create_task(ep, "characters", "写角色小传")["artifactId"] == res["next"]["artifactId"]
    env.prepare_stage(ep, "script")
    assert wb.create_task(ep, "script", "写剧本")["artifactId"] != res["next"]["artifactId"]


# 03 ---------------------------------------------------------------------------------------------
def test_03_new_project_first_episode(env):
    pid, eid = env.new_episode()
    preview = env.wb.kickoff_preview(eid)
    assert preview["settings"] == [] and not preview["canContinue"]
    t = env.wb.create_task(eid, "story", "起草")
    _, text = manifest_text(env.wb, t["runId"])
    assert "一只过分认真的柴犬第一次值夜班" in text


# 04 ---------------------------------------------------------------------------------------------
def make_pack(env, pid, eid=None, cuts=(9000, 12000, 18000), name="末班渡轮.mp4", data=b"film-v1"):
    env.wb.media_probe = lambda path: dict(duration_ms=24000, has_audio=False, width=1920, height=1080)
    env.wb.cut_detector = lambda path, a, b: list(cuts)
    src = env.wb.import_reference(pid, name, data)
    pack = env.wb.create_pack(src["sourceId"], eid, request="借检票亭里认人的节奏")
    return src, pack


def test_04_reference_film_to_script(env):
    wb = env.wb
    pid, eid = env.new_episode(name="末班渡轮", idea="末班渡轮上的检票员认出旧人")
    src, pack = make_pack(env, pid, eid)
    p = wb.pack_view(pack["packId"])
    assert [s["code"] for s in p["shots"]] == ["S01", "S02", "S03", "S04"]
    assert all("sound" in s["unknown_fields"] for s in p["shots"])            # 无音轨记入未知
    assert wb.edit_cuts(pack["packId"], "split", "S02", 10500)["affectedBeats"] == []
    wb.edit_cuts(pack["packId"], "merge", "S02")                               # 核对边界 S02：拆后又并回
    for s in wb.pack_view(pack["packId"])["shots"]:
        wb.edit_cuts(pack["packId"], "confirm", s["code"], fields=dict(camera="近景", motion="固定", narrative="揭示"))
    wb.add_beat(pack["packId"], "B03", ["S03"], "两半印痕合拢", "观众先认出关系")
    wb.bookmark(eid, pack["packId"], "S03", "同框揭示")
    wb.save_pack(pack["packId"])
    with env.db() as db:                                                       # 只保存参考包时无故事任务
        assert db.execute("SELECT count(*) FROM task").fetchone()[0] == 0
    env.claude.script = [lambda r: review(r, "return", [dict(anchor="第3段", reason="阿岚认出她缺少可见依据")])]
    t = wb.adapt_from_pack(eid, pack["packId"], "保留揭示顺序，把徽记换成船票", "adapt")
    env.drain(t["runId"])
    _, text = manifest_text(wb, t["runId"])
    assert "节拍 B03" in text and src["sourceId"] not in text and "sha256=" in text
    story = env.art(eid, "story")
    r1 = story["versions"][0]["id"]
    rv = wb.submit_review(r1, "rv1")
    env.drain(rv["runId"])
    _, rtext = manifest_text(wb, rv["runId"])
    assert "节拍 B03" in rtext                                                 # 独审读取固定版本的参考包
    with pytest.raises(Rejected):
        wb.create_task(eid, "script", "写剧本")                                # 退回后不能展开剧本
    msg = wb.post_message(t["taskId"], "把两半票推到灯下，再让阿岚松开关门的手", "fix", base_version_id=r1)
    env.drain(msg["runId"])
    r2 = env.art(eid, "story")["versions"][-1]["id"]
    env.drain(wb.submit_review(r2, "rv2")["runId"])
    wb.adopt(story["id"], r2, None, "adopt-r2", and_continue=True)
    v, _ = env.save_adopt(eid, "script", dict(scenes=[dict(id="场02", action="两半票在灯下合拢",
                                                         source=dict(storyVersion=r2, beat="B03", refShot="S03"))]))
    scene = env.art(eid, "script")["versions"][-1]["content"]["scenes"][0]
    assert scene["source"] == dict(storyVersion=r2, beat="B03", refShot="S03")


def saved_reference(env, pid):
    src, pack = make_pack(env, pid, cuts=())
    pack_id = pack["packId"]
    env.wb.edit_cuts(pack_id, "confirm", "S01", fields={"narrative": "检票员核对票根"})
    env.wb.save_pack(pack_id)
    return src, pack


def test_adaptation_command_is_atomic_project_scoped_and_replayable(env):
    wb = env.wb
    pid, eid = env.new_episode()
    _, other = env.new_episode(name="另一项目")
    src, pack = saved_reference(env, pid)
    args = (eid, pack["packId"], "按这镜的发现顺序写新故事", "adapt-once")
    with pytest.raises(Rejected, match="项目"):
        wb.adapt_from_pack(other, *args[1:])
    with pytest.raises(Rejected, match="项目"):
        wb.create_pack(src["sourceId"], other)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: wb.adapt_from_pack(*args), range(2)))
    assert a == b
    with env.db() as db:
        for table in ("task", "run", "task_pack"):
            assert db.execute("SELECT count(*) FROM "+table).fetchone()[0] == 1
    with pytest.raises(Conflict):
        wb.adapt_from_pack(eid, pack["packId"], "换题目", args[-1])
    wb.create_pack(src["sourceId"], eid, lineage=pack["lineage"])
    assert wb.adapt_from_pack(*args) == a
    with pytest.raises(Rejected, match="旧参考包"):
        wb.adapt_from_pack(*args[:-1], "new-command")


def test_adaptation_revisions_and_comparisons_keep_original_evidence(env):
    wb = env.wb
    pid, eid = env.new_episode()
    original = env.adopt_story(eid)
    _, pack = saved_reference(env, pid)
    created = wb.adapt_from_pack(eid, pack["packId"], "从这一镜改编", "start-adaptation")
    env.drain(created["runId"])
    story = env.art(eid, "story")
    first = story["versions"][-1]
    assert story["adopted_version_id"] == original and first["base_id"] is None
    assert first["content"]["adaptation"][0]["unit"] == "S01"
    def sources(run):
        return {i["role"]:i["sha256"] for i in wb.run_manifest(run)["manifest"]["inputs"]
                if i["role"] in {"adaptation_source","reference_pack"}}
    frozen = sources(created["runId"])
    assert set(frozen) == {"adaptation_source","reference_pack"}
    wb.add_beat(pack["packId"], "B09", ["S01"], "后来登记的新观察", "已补录")
    changed = wb.post_message(created["taskId"], "仅修改交接", "revise", base_version_id=first["id"])
    env.drain(changed["runId"])
    revised = env.art(eid,"story")["versions"][-1]
    assert revised["base_id"] == first["id"] and revised["content"]["adaptation"] == first["content"]["adaptation"]
    paired = wb.start_story_comparison(revised["id"], "both-authors")
    reviewed = wb.submit_review(revised["id"], "rv-adapt")
    for run in [changed["runId"], *paired["runIds"], reviewed["runId"]]:
        assert sources(run) == frozen
    malformed = [dict(first["content"]["adaptation"][0], unit="B09")]
    invalid = wb.save_manual(story["id"], first["id"], {"adaptation":malformed}, "bad-manual")["versionId"]
    assert any("未在本稿" in e for e in wb.adoption_gate(invalid)["formatErrors"])
    assert not wb.adoption_gate(invalid)["adoptable"]
    assert not wb.adoption_gate(original)["formatErrors"]


def test_model_adaptation_requires_actual_source_and_complete_fields(env):
    wb = env.wb
    pid, eid = env.new_episode()
    _, pack = saved_reference(env, pid)
    env.codex.script = [lambda req: draft(req,synopsis="他拼好票根。"),
        lambda req: draft(req,adaptation=json.dumps([dict(pack=pack["packId"], unit="S99",
            original="缺口", adapted="票根", reason="保留揭示顺序")]))]
    t = wb.adapt_from_pack(eid, pack["packId"], "从参考镜改编", "bad-output")
    env.drain(t["runId"])
    art = env.art(eid,"story")
    assert not art["versions"] and len(env.codex.calls) == 2
    assert art["tasks"][0]["runs"][0]["status"] == "failed"


def test_story_review_uses_manuscript_ancestry_and_updates_topic_when_structure_ready(env):
    wb = env.wb
    pid = wb.create_project("_template","待审稿状态核对")["projectId"]
    topic = wb.add_topic(pid,"B07","两张交接单")["topicId"]
    eid = wb.create_episode(pid,"E01")["episodeId"]
    wb.start_episode(eid,"topic",{"create":{"provider":"codex"},"review":{"provider":"claude"}},topic_id=topic)
    task = wb.create_task(eid,"story","写故事")
    env.drain(task["runId"])
    base = env.art(eid,"story")["versions"][0]["id"]
    damaged = {"synopsis":{"text":"旧数据"}, "cast":["值班员"], "handoff":4, "notes":{"待完善":True}}
    manual = wb.save_manual(task["artifactId"],base,damaged,"malformed")["versionId"]
    # A second task on the same artifact has a different reviewer.
    wb.create_task(eid,"story","另一个任务",executor={"review":{"provider":"codex"}})
    gate = env.art(eid,"story")["versions"][-1]["gate"]
    assert len(gate["formatErrors"]) == 4 and not gate["adoptable"]
    queued = wb.submit_review(manual,"review-invalid")
    assert queued["taskId"] == task["taskId"]
    assert wb.run_manifest(queued["runId"])["manifest"]["provider"] == "claude"
    def status():
        return next(t for t in wb.topics(pid) if t["id"] == topic)["status"]
    assert status() == "开发中"
    wb.submit_review(base,"review-ready")
    assert status() == "待审稿"


def test_legacy_adaptation_command_replays_only_its_pack_backed_run(env):
    wb = env.wb
    pid, eid = env.new_episode()
    _, pack = saved_reference(env,pid)
    command = "legacy-adaptation"
    old = wb.create_task(eid,"story","原有请求",command_id=command+":task")
    # An interrupted legacy transaction has a task receipt, but no pack-backed run.
    with pytest.raises(Conflict,match="旧改编命令"):
        wb.adapt_from_pack(eid,pack["packId"],"原有请求",command)
    with env.db() as db:
        db.execute("INSERT INTO task_pack VALUES(?,?)",(old["taskId"],pack["packId"]))
        original_run = wb._queue_run(db,old["taskId"],"create",None,"原有请求")
    result = wb.adapt_from_pack(eid,pack["packId"],"原有请求",command)
    assert result["runId"] == original_run and result["taskId"] == old["taskId"]
    assert wb.adapt_from_pack(eid,pack["packId"],"原有请求",command) == result
    with env.db() as db:
        assert db.execute("SELECT count(*) FROM run").fetchone()[0] == 2


# 05 ---------------------------------------------------------------------------------------------
def test_05_borrowed_and_original_shots(env):
    wb = env.wb
    pid, eid = env.new_episode(name="末班渡轮")
    _, pack = make_pack(env, pid, eid)
    for s in wb.pack_view(pack["packId"])["shots"]:
        wb.edit_cuts(pack["packId"], "confirm", s["code"], fields=dict(camera="近景", motion="固定", sound="未知",
                                                                       narrative="揭示"))
    wb.save_pack(pack["packId"])
    bm = wb.bookmark(eid, pack["packId"], "S03", "同框揭示，观众先认出关系")["bookmarkId"]
    env.adopt_story(eid)
    env.script(eid)
    base = dict(segments=[dict(id="G02", order=1, strategy="flex")], shots=[
        dict(id="K04", segment="G02", order=1, duration_ms=2000, action="递票", origin=dict(scene="场02", line="递票")),
        dict(id="K05", segment="G02", order=2, duration_ms=3500, action="票根合拢 斜拍近景",
             refs=[dict(bookmarkId=bm, mode="B", conditions="两手避开撕口")]),
        dict(id="K06", segment="G02", order=3, duration_ms=2500, action="松开绞盘", origin=dict(scene="场03", line="阿岚松开绞盘"))])
    assert wb.check_plan_refs(eid, base) == []
    plan_art = env.art(eid, "plan")
    oblique = wb.save_manual(plan_art["id"], None, base, "obl")["versionId"]
    top = json.loads(json.dumps(base))
    top["shots"][1]["action"] = "票根合拢 正俯拍"
    overhead = wb.save_manual(plan_art["id"], oblique, top, "top")["versionId"]
    with env.db() as db:                                                       # 返回时草稿并存，没有复核项
        assert db.execute("SELECT count(*) FROM impact").fetchone()[0] == 0
    wb.adopt(plan_art["id"], oblique, None, "adopt-obl")
    res = wb.adopt(plan_art["id"], overhead, oblique, "adopt-top")             # 确认后才标实际下游需复核
    assert {"prompts:G02"} <= {i["target"] for i in res["impacts"]}
    lay = timeline.layout(top)
    assert [(lay["shots"][k]["seg_start_ms"], lay["shots"][k]["seg_end_ms"]) for k in ("K04", "K05", "K06")] == \
        [(0, 2000), (2000, 5500), (5500, 8000)]
    trace = wb.trace_shot(eid, "K05")
    assert trace["refs"][0]["refShot"] == "S03" and trace["refs"][0]["mode"] == "B"
    assert wb.trace_shot(eid, "K06")["origin"]["scene"] == "场03"


# 06 ---------------------------------------------------------------------------------------------
def test_06_source_changes(env):
    wb = env.wb
    pid, eid = env.new_episode()
    src1, pack1 = make_pack(env, pid, eid, data=b"film-v1")
    for s in wb.pack_view(pack1["packId"])["shots"]:
        wb.edit_cuts(pack1["packId"], "confirm", s["code"], fields=dict(camera="c", motion="m", narrative="n"))
    wb.save_pack(pack1["packId"])
    src2 = wb.import_reference(pid, "末班渡轮.mp4", b"film-v2-different")       # 重定位同名不同内容
    assert src2["note"] and src2["sourceId"] != src1["sourceId"]
    pack2 = wb.create_pack(src2["sourceId"], eid, lineage=pack1["lineage"])
    assert pack2["version"] == 2
    with pytest.raises(Rejected, match="旧参考包"):
        wb.adapt_from_pack(eid, pack1["packId"], "用旧证据写新稿", "old")
    assert wb.pack_view(pack1["packId"])["source"]["sha256"] != wb.pack_view(pack2["packId"])["source"]["sha256"]


# 07 / 08 / 09 -----------------------------------------------------------------------------------
PROPOSALS = [dict(category="character", content="阿福认真过头、照章办事", suggestedStrength="required", rationale="续集动机"),
             dict(category="signature", content="鹦鹉模仿收银提示音，常常帮倒忙", suggestedStrength="variable", rationale="可制造误会"),
             dict(category="space", content="便利店通宵营业，夜班由阿福和鹦鹉值守", suggestedStrength="required", rationale="人手有限")]


def closure_for(env, items=PROPOSALS):
    r = env.full_episode()
    def prop(req):
        out = proposal(req, [dict(it, source=dict(objectId="story", versionId="v", unit="paragraph", anchor="第2段",
                                                  evidence="原句")) for it in items])
        return out
    env.codex.script = [prop]
    c = env.wb.start_closure(r["delivery"]["deliveryId"], "close")
    return r, c, env.wb.closure_view(c["closureId"])["items"]


def test_07_missing_write_reason(env):
    r, c, items = closure_for(env)
    decisions = [dict(itemId=it["id"], decision="write_setting", strength=s, reason=reason)
                 for it, s, reason in zip(items, ("required", "variable", "required"), ("夜班笑点来自流程", "", "人手有限"))]
    env.wb.decide_items(c["closureId"], decisions, "d1")
    with pytest.raises(Rejected) as exc:
        env.wb.confirm_closure(c["closureId"], "c1")
    assert exc.value.detail == dict(rows=[2])                                 # 指出鹦鹉条目缺理由
    decisions[1]["reason"] = "首集结尾留下假提示音"
    env.wb.decide_items(c["closureId"], decisions, "d2")
    out = env.wb.confirm_closure(c["closureId"], "c2")
    assert len(out["settings"]) == 3
    settings = env.wb.project_view(r["pid"])["settings"]
    assert len(settings) == 3 and all(s["source_ref"] for s in settings)
    with env.db() as db:
        assert {r_[0] for r_ in db.execute("SELECT user_reason FROM setting_entry")} == {"夜班笑点来自流程", "首集结尾留下假提示音", "人手有限"}


def test_08_three_destinations(env):
    r, c, items = closure_for(env)
    wb = env.wb
    rocket = env.image(r["pid"], r["eid"], name="纸箱火箭外观", category="prop")
    wb.decide_items(c["closureId"], [
        dict(itemId=items[0]["id"], decision="write_setting", strength="required", reason="动机"),
        dict(itemId=items[1]["id"], decision="write_setting", strength="variable", reason="可用"),
        dict(itemId=items[2]["id"], decision="ledger_only", assetVersionId=rocket["versionId"])], "d")
    assert wb.project_todos(r["pid"])[0]["kind"] == "closure"
    out = wb.confirm_closure(c["closureId"], "c")
    assert len(out["settings"]) == 2 and len(out["ledger"]) == 1 and len(out["reuse"]) == 1
    assert wb.library(r["pid"])[0]["status"] == "pending"
    assert not [t for t in wb.project_todos(r["pid"]) if t["kind"] == "closure"]      # 待办已清


def test_09_later(env):
    r, c, items = closure_for(env)
    wb = env.wb
    wb.decide_items(c["closureId"], [dict(itemId=items[0]["id"], decision="write_setting", strength="required",
                                          reason="动机")], "later")
    assert any(t["kind"] == "closure" for t in wb.project_todos(r["pid"]))
    again = wb.start_closure(r["delivery"]["deliveryId"], "reopen")
    assert again["reopened"] and wb.closure_view(again["closureId"])["items"][0]["decision"] == "write_setting"
    assert wb.project_view(r["pid"])["settings"] == []


# 10 ---------------------------------------------------------------------------------------------
def test_10_leave_after_delivery(env):
    r = env.full_episode()
    wb = env.wb
    assert wb.project_view(r["pid"])["episodes"][0]["state"] == "delivered"
    assert wb.project_todos(r["pid"]) == []                                   # 未启动整理时没有沉淀待办
    e2 = wb.create_episode(r["pid"], "E02")["episodeId"]
    assert wb.kickoff_preview(e2)["canContinue"]
    wb.start_episode(e2, "idea", {"create": {"provider": "codex"}, "review": {"provider": "claude"}}, idea_text="第二夜")


# 11 ---------------------------------------------------------------------------------------------
def test_11_continue_then_independent_then_continue(env):
    wb = env.wb
    pid, e1 = env.new_episode()
    wb.record_leftovers(e1, ["鹦鹉学会了收银提示音"], 2)
    e2 = wb.create_episode(pid, "E02")["episodeId"]
    a = wb.kickoff_preview(e2, "continue", [e1])
    b = wb.kickoff_preview(e2, "independent")
    c = wb.kickoff_preview(e2, "continue", [e1])
    assert a["carriedFacts"] == c["carriedFacts"] == ["鹦鹉学会了收银提示音"] and b["carriedFacts"] == []
    assert a["settings"] == b["settings"] == c["settings"]                    # 项目设定始终显示
    wb.start_episode(e2, "idea", {"create": {"provider": "codex"}, "review": {"provider": "claude"}}, idea_text="第二夜",
                     relation="continue", predecessor_ids=[e1])
    _, text = manifest_text(wb, wb.create_task(e2, "story", "写")["runId"])
    assert "鹦鹉学会了收银提示音" in text


# 12 ---------------------------------------------------------------------------------------------
def test_12_second_episode_required_violation(env):
    r, c, items = closure_for(env)
    wb = env.wb
    wb.decide_items(c["closureId"], [dict(itemId=it["id"], decision="write_setting", strength=s, reason="理由")
                                     for it, s in zip(items, ("required", "variable", "required"))], "d")
    keys = wb.confirm_closure(c["closureId"], "c")["settings"]
    e2 = wb.create_episode(r["pid"], "E02")["episodeId"]
    assert len(wb.kickoff_preview(e2)["settings"]) == 3                       # 第二集读取设定
    wb.start_episode(e2, "idea", {"create": {"provider": "codex"}, "review": {"provider": "claude"}}, idea_text="第二夜")
    t = wb.create_task(e2, "story", "写")
    env.drain(t["runId"])
    env.claude.script = [lambda req: review(req, "return", [
        dict(category="setting", settingId=keys[0], anchor="第2段", reason="阿福发现收银异常却随手走开"),
        dict(category="clarity", anchor="结尾", reason="结尾笑点有分歧")])]
    story = env.art(e2, "story")
    v = story["versions"][0]["id"]
    env.drain(wb.submit_review(v, "rv")["runId"])
    hard, soft = env.art(e2, "story")["versions"][0]["reviews"][0]["issues"]
    with pytest.raises(Rejected):
        wb.waive(hard["id"], v, "想放行", "w1")
    wb.waive(soft["id"], v, "笑点留给下一集", "w2")
    with pytest.raises(Rejected):
        wb.adopt(story["id"], v, None, "a")
    with pytest.raises(Rejected):
        wb.create_task(e2, "script", "写剧本")                                 # 放行后一项仍不能进入剧本


# 13 ---------------------------------------------------------------------------------------------
def test_13_spec_and_setting_change_separately(env):
    wb = env.wb
    pid = wb.create_project("puggy")["projectId"]
    eid = wb.create_episode(pid, "N05")["episodeId"]
    wb.start_episode(eid, "idea", {"create": {"provider": "codex"}, "review": {"provider": "claude"}}, idea_text="浣熊")
    wb.update_preset(pid, dict(aspectRatio="16:9"), "preset")
    assert wb.episode_view(eid)["kickoff"]["spec"]["aspectRatio"] == "9:16"  # 本集画幅不变
    res = wb.propose_setting_change(pid, "milo-active", "MILO 主动、有真本领，偶尔失手", "required", "补充失手", "chg")
    assert [a["code"] for a in res["affected"]] == ["N05"]                    # 一次影响提示
    wb.choose_episode_settings(eid, True, "follow")
    k = wb.episode_view(eid)["kickoff"]
    assert any(s["content"].endswith("偶尔失手") for s in k["settings_snapshot"])
    assert [i["target_ref"] for i in wb.impacts(eid)] == ["story-review"]


# 14 ---------------------------------------------------------------------------------------------
def test_14_recovery_and_repeated_submits(env):
    r, c, items = closure_for(env)
    wb = env.wb
    wb.decide_items(c["closureId"], [dict(itemId=items[0]["id"], decision="write_setting", strength="required", reason="x")],
                    "d")
    first = wb.confirm_closure(c["closureId"], "confirm-1")
    assert wb.confirm_closure(c["closureId"], "confirm-1") == first          # 重复点击
    assert wb.confirm_closure(c["closureId"], "confirm-2") == first          # 中断后重新打开再确认
    reopened = Env.__new__(Env)
    reopened.__dict__.update(env.__dict__)
    from workbench.service import Workbench
    wb2 = Workbench(wb.store.root, env.wb.executors, image_providers=env.wb.image_providers_map)
    assert len(wb2.project_view(r["pid"])["settings"]) == 1
    with env.db() as db:
        assert db.execute("SELECT count(*) FROM setting_entry").fetchone()[0] == 1


# 15 ---------------------------------------------------------------------------------------------
def test_15_script_conflict_and_combine(env):
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    r3 = env.script(eid)
    art = env.art(eid, "script")
    env.codex.script = [lambda req: draft(req, scenes=json.dumps([dict(id="场01", action="AI：阿福巡店"),
                                                                    dict(id="场02", action="AI：动作代替对白")]))]
    t = wb.create_task(eid, "script", "把相认的对白换成动作")
    r4 = wb.save_manual(art["id"], r3, dict(notes="导演手改的说明"), "manual")["versionId"]
    env.drain(t["runId"])
    ai = env.art(eid, "script")["versions"][-1]["id"]
    cmp_ = wb.compare(r4, ai)
    assert cmp_["commonBase"] == r3 and {"scenes", "notes"} <= set(cmp_["fields"])
    combo = wb.combine(art["id"], dict(scenes=ai, notes=r4), "combine")
    v = next(x for x in env.art(eid, "script")["versions"] if x["id"] == combo["versionId"])
    assert v["origin"] == "combined" and json.loads(v["note"])["sources"] == sorted([ai, r4])
    assert v["gate"]["review"] is None                                        # 组合稿待审
    assert len(env.art(eid, "script")["versions"]) == 4                       # 两份草稿均可打开


# 16 ---------------------------------------------------------------------------------------------
def test_16_shot_duration_linkage(env):
    wb = env.wb
    r = env.full_episode(deliver=False)
    plan_art = env.art(r["eid"], "plan")
    adopted = plan_art["adopted_version_id"]
    lay = timeline.layout(r["plan"])
    g03 = next(s for s in lay["segments"] if s["id"] == "G03")
    assert (g03["start_ms"], g03["end_ms"], lay["total_ms"]) == (26000, 39000, 86000)
    longer = json.loads(json.dumps(r["plan"]))
    first = next(s for s in longer["shots"] if s["segment"] == "G03")
    first["duration_ms"] += 1000
    v = wb.save_manual(plan_art["id"], adopted, longer, "longer")["versionId"]
    pre = wb.plan_preview(v)
    assert pre["totalBefore"] == 86000 and pre["totalAfter"] == 87000
    assert dict(segment="G04", startBefore=39000, startAfter=40000) in pre["shifted"]
    assert any(d["target"] == "提示词 G03" for d in pre["downstream"]) and any("对位" in d["target"] for d in pre["downstream"])
    # 取消：不采用，G04 仍从 39 秒开始
    assert timeline.layout(env.art(r["eid"], "plan")["versions"][0]["content"])["segments"][3]["start_ms"] == 39000
    wb.adopt(plan_art["id"], v, adopted, "adopt-longer")
    new_lay = timeline.layout(env.art(r["eid"], "plan")["versions"][-1]["content"])
    assert new_lay["segments"][3]["start_ms"] == 40000 and new_lay["total_ms"] == 87000
    assert "prompts:G03" in {i["target_ref"] for i in wb.impacts(r["eid"])}


# 17 ---------------------------------------------------------------------------------------------
def test_17_split_overlong_segment(env):
    wb = env.wb
    segs = {"G01": [6000, 7000], "G02": [8000, 9000]}                          # G02 = 17 s
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86(segs)
    v17, _ = env.save_adopt(eid, "plan", plan)
    problems, _ = wb.delivery_problems(eid, "trial", ["G01", "G02"])
    assert any("超过 15 秒" in p["problem"] for p in problems)                  # 首次被阻断
    split = wb.split_segment(v17, "G02", "S04", "split")
    pre = wb.plan_preview(split["versionId"])
    assert split["mapping"] == {"G02": ["G02a", "G02b"]} and pre["problems"] == []
    lay = pre["layout"]
    assert [(s["id"], s["start_ms"], s["end_ms"]) for s in lay["segments"]] == \
        [("G01", 0, 13000), ("G02a", 13000, 21000), ("G02b", 21000, 30000)]   # 时间连续
    plan_art = env.art(eid, "plan")
    res = wb.adopt(plan_art["id"], split["versionId"], v17, "adopt-split")
    targets = {i["target"] for i in res["impacts"]}
    assert {"prompts:G02a", "prompts:G02b"} <= targets and any(t.startswith("joint:") for t in targets)
    assert env.art(eid, "plan")["adopted_version_id"] == split["versionId"]   # 只有新方案有效


# 18 ---------------------------------------------------------------------------------------------
def test_18_space_and_cast(env):
    wb = env.wb
    r = env.full_episode(deliver=False)
    eid, pid = r["eid"], r["pid"]
    wb.add_space_item(eid, "桶高", "桶比柜台低一个爪子", "剧本场02", ["G04"])
    assert wb.project_todos(pid)[0]["title"].endswith("G04 桶高待确认")
    with pytest.raises(Rejected, match="G04"):
        wb.create_batch(eid, "fake-image", "generate", [dict(prompt="桶", segments=["G04"])], "64x64", budget_cents=100)
    ok = wb.create_batch(eid, "fake-image", "generate", [dict(prompt="LEO 身份图", segments=[],
                                                              target=dict(name="LEO", category="identity"))], "64x64",
                         budget_cents=100)                                    # 无关任务可运行
    assert ok["batchId"]
    grid = env.image(pid, eid, name="G04 九宫格", category="grid", grid=True)
    wb.set_cast(eid, "G04", "leo", "unclear", grid["versionId"], [7, 8, 9], note="只见背影")
    cell = wb.cast_matrix(eid)["rows"][3]["cells"]["leo"]
    assert cell["options"] == ["按身份参考修格", "改用 flex"] or cell["options"] == ["补身份参考", "改动作让身份可见"]
    # 改 flex 路径：段策略改 flex，实际视频输入符合策略（grid 段只能一张九宫格）
    entry = dict(refs=[dict(assetVersionId=grid["versionId"], label="图片1", kind="image", grid=True)],
                 txt="00:00.000-00:12.000 图片1")
    from workbench.stages import check_segment_prompt
    assert check_segment_prompt("G04", entry, 12000, "grid") == []
    two = dict(entry, refs=entry["refs"] + [dict(assetVersionId=r["img"]["versionId"], label="图片2", kind="image")])
    assert any("九宫格" in p["problem"] for p in check_segment_prompt("G04", two, 12000, "grid"))
    assert check_segment_prompt("G04", dict(two, txt="00:00.000-00:12.000 图片1 图片2"), 12000, "flex") == []
    audio_only = dict(txt="00:00.000-00:12.000 音频1", refs=[dict(assetVersionId="audio", label="音频1", kind="audio")])
    assert any("图片1–9" in issue["problem"] for issue in check_segment_prompt("G04", audio_only, 12000, "flex"))


# 19 ---------------------------------------------------------------------------------------------
def test_19_batch_partial_unknown(tmp_path):
    env = Env(tmp_path, image_script=["ok", "timeout"])
    env.images.remote_status = True  # queryable provider can still complete later
    wb = env.wb
    pid, eid = env.new_episode()
    items = [dict(prompt=f"状态图 {i}", target=dict(name=f"状态{i}", category="state")) for i in (1, 2)]
    b = wb.create_batch(eid, "fake-image", "generate", items, "64x64", budget_cents=100)
    with pytest.raises(Conflict):
        wb.create_batch(eid, "fake-image", "generate", items, "64x64", budget_cents=100)    # 同批次不重复
    wb.authorize_batch(b["batchId"], b["summaryHash"], "auth")
    view = wb.execute_batch(b["batchId"])
    states = [r["state"] for r in view["requests"]]
    assert states == ["succeeded", "unknown"] and view["state"] == "pending_reconcile"
    assert view["requests"][0]["sha256"]                                      # 已返回图可查
    from workbench.service import Workbench
    wb2 = Workbench(wb.store.root, wb.executors, image_providers=wb.image_providers_map)   # 关闭并恢复
    with pytest.raises(Rejected):
        wb2.execute_batch(b["batchId"])
    assert env.images.sent == 2                                               # 不会自动重发
    unknown = view["requests"][1]
    wb2.reconcile_request(unknown["id"], "not_executed")
    nid = wb2.resubmit_request(unknown["id"], "resub")["requestId"]
    final = wb2.execute_batch(b["batchId"])
    assert env.images.sent == 3 and next(r for r in final["requests"] if r["id"] == nid)["state"] == "succeeded"
    # regression (found in the browser walkthrough): a resubmitted request that is again unknown can be registered
    b2 = wb2.create_batch(eid, "fake-image", "generate", [dict(prompt="再来", target=dict(name="y", category="state"))], "64x64",
                          budget_cents=100)
    env.images.script = ["timeout", "timeout"]
    wb2.authorize_batch(b2["batchId"], b2["summaryHash"], "auth2")
    r1 = wb2.execute_batch(b2["batchId"])["requests"][0]
    wb2.reconcile_request(r1["id"], "not_executed")
    r2 = wb2.resubmit_request(r1["id"], "resub2")["requestId"]
    wb2.execute_batch(b2["batchId"])
    done = wb2.reconcile_request(r2, "completed", png_bytes(64, 64))
    assert done["state"] == "completed"
    assert final["state"] == "completed"


# 20 ---------------------------------------------------------------------------------------------
def test_20_candidate_local_redraw(env):
    wb = env.wb
    r = env.full_episode(deliver=False)
    eid, pid = r["eid"], r["pid"]
    a = env.image(pid, eid, name="G03 九宫格", category="grid", grid=True, rgb=(1, 1, 1))
    b_ = env.image(pid, eid, name="G03 九宫格 B", category="grid", grid=True, rgb=(2, 2, 2))
    wb.mark_image(eid, a["versionId"], "required")
    batch = wb.redraw_cells(eid, a["versionId"], [5], [b_["versionId"]], "修 A 第 5 格", "fake-image", "64x64", budget_cents=50)
    wb.authorize_batch(batch["batchId"], batch["summaryHash"], "auth")
    new_v = wb.execute_batch(batch["batchId"])["requests"][0]["asset_version_id"]
    cmp_ = wb.compare_cells(new_v)
    assert cmp_["base"] == a["versionId"] and cmp_["requested"] == [5]
    # Fake provider replaced the whole image; all nine returned cells really changed.
    assert cmp_["comparable"] and cmp_["changed"] == list(range(1, 10)) and cmp_["unchanged"] == []
    av = wb.asset_version(new_v)
    assert av["asset_id"] == a["assetId"] and av["version_no"] == 2          # B 仅用于绘图
    # 把 A 放进 G03 并采用，再采用新图 → 复审真实引用者
    pr_art = env.art(eid, "prompts")
    content = json.loads(json.dumps(pr_art["versions"][-1]["content"]))
    content["segments"]["G03"]["refs"][0]["assetVersionId"] = a["versionId"]
    v2 = wb.save_manual(pr_art["id"], pr_art["adopted_version_id"], content, "use-a")["versionId"]
    wb.adopt(pr_art["id"], v2, pr_art["adopted_version_id"], "adopt-a")
    pre = wb.adopt_image(eid, new_v, "adopt-img")
    assert pre["needsConfirm"] and pre["references"][0]["segment"] == "G03"  # 采用前 A 仍有效
    done = wb.adopt_image(eid, new_v, "adopt-img", confirm=True)
    assert [i["target"] for i in done["recheck"]] == ["prompts:G03"]
    assert not wb.adopt_image(eid, new_v, "second-tab", confirm=True, expected=pre["fingerprint"])["recheck"]
    marks = {v["id"]: v["marks"] for v in wb.episode_assets(eid)}
    assert "required" in marks[a["versionId"]] and b_["versionId"] in marks  # 补选图仍可见


# 21 ---------------------------------------------------------------------------------------------
def test_21_txt_refs_and_time(env):
    wb = env.wb
    r = env.full_episode(deliver=False)
    eid, pid = r["eid"], r["pid"]
    pr_art = env.art(eid, "prompts")
    content = json.loads(json.dumps(pr_art["versions"][-1]["content"]))
    content["segments"]["G03"]["txt"] = "00:00.000-00:09.000 图片1 阿福推门。"         # 13 秒段只写到 9 秒
    gap = wb.save_manual(pr_art["id"], pr_art["adopted_version_id"], content, "gap")["versionId"]
    errs = next(v for v in env.art(eid, "prompts")["versions"] if v["id"] == gap)["gate"]["formatErrors"]
    assert "G03：时间句缺少 9–13 秒" in errs                                    # 缺口阻断送审
    second = env.image(pid, eid, name="柜台", category="scene", rgb=(9, 9, 9))
    content["segments"]["G03"] = dict(content["segments"]["G03"], txt="00:00.000-00:13.000 图片1 在 图片2 前推门。",
                                      refs=[dict(assetVersionId=r["img"]["versionId"], label="图片1", kind="image", role="阿福身份"),
                                            dict(assetVersionId=second["versionId"], label="图片2", kind="image", role="柜台空间")])
    two = wb.save_manual(pr_art["id"], gap, content, "two")["versionId"]
    res = wb.reorder_refs(two, "G03", [second["versionId"], r["img"]["versionId"]], "reorder", confirm=True)
    after = next(v for v in env.art(eid, "prompts")["versions"] if v["id"] == res["versionId"])["content"]["segments"]["G03"]
    assert [(x["label"], x["role"]) for x in after["refs"]] == [("图片1", "柜台空间"), ("图片2", "阿福身份")]
    assert after["txt"] == "00:00.000-00:13.000 图片2 在 图片1 前推门。"        # 标签、用途和正文一致
    assert res["preview"]["mapping"] == {"图片1": "图片2", "图片2": "图片1"} and res["preview"]["txtDiff"]


# 22 ---------------------------------------------------------------------------------------------
def test_22_sound_and_dialogue(env):
    wb = env.wb
    r = env.full_episode(deliver=False)
    pr_art = env.art(r["eid"], "prompts")
    content = json.loads(json.dumps(pr_art["versions"][-1]["content"]))
    content["segments"]["G02"]["sound"] = dict(bgm=True, ambience=["冰柜低鸣"], dialogue=["Customer: Hello?"])
    v = wb.save_manual(pr_art["id"], pr_art["adopted_version_id"], content, "snd")["versionId"]
    first = wb.set_sound(v, "G02", dict(silent=True), "silent")
    assert first["needsConfirm"] and {c["item"] for c in first["conflicts"]} >= {"对白", "环境声"}   # 确认前提示冲突
    nobgm = wb.set_sound(v, "G02", dict(bgm=False), "nobgm")
    s = next(x for x in env.art(r["eid"], "prompts")["versions"] if x["id"] == nobgm["versionId"])["content"]["segments"]["G02"]["sound"]
    assert s["bgm"] is False and s["dialogue"] == ["Customer: Hello?"] and s["ambience"] == ["冰柜低鸣"]


# 23 ---------------------------------------------------------------------------------------------
def test_23_two_pass_review_and_partial_recheck(env):
    wb = env.wb
    r = env.full_episode(deliver=False)
    eid = r["eid"]
    pv = r["prompts"]
    cold_run = env.claude.calls[-2] if env.claude.calls[-2].purpose == "cold_read" else None
    cold = next(c for c in env.claude.calls if c.purpose == "cold_read")
    m, text = manifest_text(wb, cold.run_id)
    assert [e["role"] for e in m["inputs"]] == ["method", "method", "segment_txt", "reference_order", "image"]   # 冷读只见本段 TXT 与原图
    assert [e["name"] for e in m["inputs"][:2]] == ["visual-continuity-audit.md", "cold_read.md"]
    assert "便利店夜" in text and "一只过分认真" not in text and len(cold.images) == 1
    comp = next(c for c in env.claude.calls if c.purpose == "comparison")
    cm, _ = manifest_text(wb, comp.run_id)
    assert any(e["role"] == "method" and e["name"] == "comparison.md" for e in cm["inputs"])
    assert {"cold_read_observations", "adopted_story", "project_settings", "plan_segment"} <= {e["role"] for e in cm["inputs"]}
    pr_art = env.art(eid, "prompts")
    content = json.loads(json.dumps(pr_art["versions"][-1]["content"]))
    content["segments"]["G03"]["txt"] = content["segments"]["G03"]["txt"] + "\n阿福双手持杯。"
    content["segments"]["G05"]["txt"] = content["segments"]["G05"]["txt"] + "\n叠字：凌晨两点。"
    v2 = wb.save_manual(pr_art["id"], pv, content, "edit")["versionId"]
    st = wb.prompt_review_status(v2)
    need_cold = sorted(s for s, x in st.items() if x["coldRead"]["state"] == "needed")
    need_cmp = sorted(s for s, x in st.items() if x["comparison"]["state"] == "needed")
    assert need_cold == ["G03", "G05"]
    assert need_cmp == ["G02", "G03", "G04", "G05", "G06"]                     # 复审包含 G03、G05 及关联接点
    assert st["G01"]["ok"] and st["G07"]["ok"]                               # 未变项沿用结论（输入匹配）


# 24 ---------------------------------------------------------------------------------------------
def test_24_publish_pack_and_output(env):
    wb = env.wb
    r = env.full_episode()
    pub = wb.compose_publish(r["eid"])
    f = pub["fields"]
    assert set(f) == {"title", "body", "tags", "suno_title", "suno_style", "suno_exclude"}
    assert f["title"] == "NIGHT First Night Shift" and f["body"].splitlines() == ["Afu takes his first night shift.",
                                                                                 *PUBLISH["body_fixed_lines"]]
    assert pub["music"] == "备用"                                             # 无 BGM 仍标备用
    root = Path(r["delivery"]["path"])
    md = (root / "01_发布包.md").read_text()
    for value in f.values():
        assert value in md                                                    # 六个字段与源文件相符
    assert (root / "03_精剪交接.md").is_file() and (root / "episode.json").is_file()
    assert not (root / "补选区").exists() or all(p.suffix == ".png" for p in (root / "补选区").iterdir())
    assert "NIGHT First Night Shift" in (root / "00_图文对照.html").read_text()


# 25 ---------------------------------------------------------------------------------------------
@pytest.mark.skipif(not DEFAULT_PACKAGER.is_file(), reason="本机没有 H3 打包器")
def test_25_local_packager_and_generic_role(env):
    wb = env.wb
    segs = {"G01": [6000, 7000], "G02": [5000, 5000]}
    r = env.full_episode(segments=segs)
    out = wb.package(r["delivery"]["deliveryId"], ["G01"], "pkg")
    with zipfile.ZipFile(out["path"]) as z:
        project = json.loads(z.read("project.json"))
    assert project["schemaVersion"] == 1 and [s["id"] for s in project["segments"]] == ["G01"]
    assert all("fixedRole" not in a for a in project["assets"])               # 通用角色无需伪装成 fixedRole
    assert project["segments"][0]["params"]["ratio"] == "9:16" and project["segments"][0]["params"]["duration"] == 13
    root = Path(r["delivery"]["path"])
    (root / "prompts/G02.txt").write_text("00:00.000-00:10.000 图片7 未知标签")   # 未知标签
    with pytest.raises(Rejected, match="交付验包失败：prompts/G02.txt"):
        wb.package(r["delivery"]["deliveryId"], ["G02"], "pkg2")
    assert not wb.verify_delivery(r["delivery"]["deliveryId"])["ok"]
    big = env.full_episode(code="E02", project_id=r["pid"], segments=segs, deliver=False)
    huge = wb.register_asset(r["pid"], "超大图", png_bytes(64, 64) + b"\0" * (31 * 1024 * 1024), "scene", episode_id=big["eid"])
    pr_art = env.art(big["eid"], "prompts")
    content = json.loads(json.dumps(pr_art["versions"][-1]["content"]))
    content["segments"]["G01"]["refs"][0]["assetVersionId"] = huge["versionId"]
    v = wb.save_manual(pr_art["id"], pr_art["adopted_version_id"], content, "huge")["versionId"]
    wb.adopt(pr_art["id"], v, pr_art["adopted_version_id"], "adopt-huge")
    problems, _ = wb.delivery_problems(big["eid"], "trial", ["G01"])
    assert any("30 MiB" in p["problem"] for p in problems)                    # 超限文件逐项报错


# 26 ---------------------------------------------------------------------------------------------
@pytest.mark.skipif(not DEFAULT_PACKAGER.is_file(), reason="本机没有 H3 打包器")
def test_26_partial_delivery_g03(env):
    wb = env.wb
    r = env.full_episode(deliver=False)
    trial = wb.deliver(r["eid"], "trial", "trial", segments=["G03"])
    episode = json.loads((Path(trial["path"]) / "episode.json").read_text())
    assert [s["id"] for s in episode["segments"]] == ["G03"] and episode["segments"][0]["start"] == "00:00.000"
    assert wb.project_view(r["pid"])["episodes"][0]["state"] == "in_production"   # 试验不更新整集完成状态
    out = wb.package(trial["deliveryId"], ["G03"], "pkg")
    with zipfile.ZipFile(out["path"]) as z:
        names = z.namelist()
        project = json.loads(z.read("project.json"))
    assert project["scope"] == "segments"
    assert project["episodeId"] == "E01" and [s["id"] for s in project["segments"]] == ["G03"]
    assert sorted(names) == sorted(["project.json", *[a["path"] for a in project["assets"]]])
    assert wb.verify_delivery(trial["deliveryId"])["ok"]


# 27 ---------------------------------------------------------------------------------------------
def test_27_thread_readback(env):
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G02": [2000, 3500, 2500]})
    pv, _ = env.save_adopt(eid, "plan", plan)
    plan_art = env.art(eid, "plan")
    env.codex.script = [clarify, lambda req: draft(req, shots=json.dumps(
        [dict(s, action="正俯拍" if s["id"] == "S02" else s["action"]) for s in plan["shots"]]))]
    t = wb.create_task(eid, "plan", "K05 换俯拍", command_id="t-old")
    wb.abandon_task(t["taskId"], "noop") if False else None
    msg = wb.post_message(t["taskId"], "K05 换俯拍", "m1", selection=dict(shot="S02", refShot="S03"), base_version_id=pv)
    env.drain(t["runId"])
    env.codex.script = [clarify, lambda req: draft(req, shots=json.dumps(
        [dict(s, action="正俯拍" if s["id"] == "S02" else s["action"]) for s in plan["shots"]]))]
    env.drain(msg["runId"])
    task = wb.task_records(episode_id=eid, stage="plan")[0]
    assert task["state"] == "clarifying" and task["messages"][-1]["kind"] == "clarify"
    ans = wb.answer_clarification(t["taskId"], "只改动作", "ans")
    env.drain(ans["runId"])
    new_v = env.art(eid, "plan")["versions"][-1]
    wb.adopt(plan_art["id"], new_v["id"], pv, "adopt")
    old = next(x for x in wb.task_records(episode_id=eid, stage="plan") if x["id"] == t["taskId"])
    kinds = [m_["kind"] for m_ in old["messages"]]
    assert "clarify" in kinds and "answer" in kinds and "draft" in kinds and old["state"] == "closed"
    assert json.loads(next(m_ for m_ in old["messages"] if m_["kind"] == "text" and m_["selection"])["selection"])["shot"] == "S02"
    assert any(r_["base_version_id"] == pv for r_ in old["runs"])            # 来源版本
    t2 = wb.create_task(eid, "plan", "新任务")
    _, text = manifest_text(wb, t2["runId"])
    assert "正俯拍" in text                                                    # 新任务带入最新采用成果


# 28 ---------------------------------------------------------------------------------------------
def test_28_models_and_image_channel(env):
    wb = env.wb
    with pytest.raises(Rejected, match="不支持强度"):
        wb.validate_selection("antigravity", "gemini-3.8-flash-high", "high")    # 强度编码在型号里
    assert wb.validate_selection("claude", "sonnet", "xhigh")["status"] == "pending"
    pid, eid = env.new_episode()
    t = wb.create_task(eid, "story", "写")
    wb.set_task_executor(t["taskId"], "create", "claude", "sonnet", "high")
    msg = wb.post_message(t["taskId"], "改结尾", "m")
    m, _ = manifest_text(wb, msg["runId"])
    assert (m["provider"], m["requestedModel"], m["requestedEffort"]) == ("claude", "sonnet", "high")
    env.drain(msg["runId"])
    run = next(r for r in wb.task_records(episode_id=eid)[0]["runs"] if r["id"] == msg["runId"])
    assert run["requested_model"] == "sonnet" and run["reported_model"] is None   # 服务报告值未报告则留空
    items = [dict(prompt="图", target=dict(name="x", category="state"))]
    b1 = wb.create_batch(eid, "fake-image", "generate", items, "64x64", budget_cents=10)
    env.wb.image_providers_map["fake-image-2"] = type(env.images)("fake-image-2", price_cents=None)
    b2 = wb.create_batch(eid, "fake-image-2", "generate", items, "64x64", budget_cents=10)
    assert b1["summaryHash"] != b2["summaryHash"]                             # 换通道重新确认
    assert wb.authorize_batch(b2["batchId"], b2["summaryHash"], "a2")["state"] == "authorized"


# 29 ---------------------------------------------------------------------------------------------
def test_29_expert_stops(env):
    wb = env.wb
    pid, eid = env.new_episode()
    env.claude.script = [lambda r: review(r, "return", [dict(reason="动机不清")])] * 3
    auth = wb.authorize_expert(pid, [eid], "story", {}, stops=[])
    res = wb.expert_run(auth["authId"])
    assert res["reason"] == "rounds_exhausted" and res["usedRounds"] == 2
    task = wb.task_records(episode_id=eid)[0]
    wb.post_message(task["id"], "继续原线程，再改一次", "m")                    # 普通消息不重置
    assert wb.task_records(episode_id=eid)[0]["used_rounds"] == 2
    latest = env.art(eid, "story")["versions"][-1]["id"]
    with pytest.raises(Rejected, match="隔离"):
        wb.submit_review(latest, "ag", provider="antigravity")                # 未验证隔离时必需审核等待
    auth2 = wb.authorize_expert(pid, [eid], "story", dict(review=dict(provider="antigravity")), stops=[])
    assert "paid_batch" in auth2["stops"]                                     # 付费批次不可关的停点


# 30 ---------------------------------------------------------------------------------------------
def test_30_local_restore(tmp_path):
    env = Env(tmp_path, image_script=["timeout"])
    wb = env.wb
    pid, eid = env.new_episode()
    b = wb.create_batch(eid, "fake-image", "generate", [dict(prompt="p", target=dict(name="x", category="state"))], "64x64",
                        budget_cents=10)
    wb.authorize_batch(b["batchId"], b["summaryHash"], "a")
    wb.execute_batch(b["batchId"])
    backup = wb.backup()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(Rejected, match="不可写") as exc:
            wb.restore(backup["backupId"], locked / "restore")
        assert str(locked / "restore") in str(exc.value)                      # 错误指出目录
    finally:
        locked.chmod(0o700)
    assert wb.project_view(pid)["name"] == "夜班柴犬"                          # 恢复前版本可读
    ok = wb.restore(backup["backupId"], tmp_path / "restored")
    from workbench.service import Workbench
    wb2 = Workbench(Path(ok["restoredTo"]), wb.executors, image_providers=wb.image_providers_map)
    assert wb2.batch_view(b["batchId"])["requests"][0]["state"] == "failed"
    with pytest.raises(Rejected):
        wb2.execute_batch(b["batchId"])                                       # 不会自动重发原付费请求
    assert env.images.sent == 1


# 31 ---------------------------------------------------------------------------------------------
def test_31_knowledge_and_dedup(env):
    wb = env.wb
    r = env.full_episode()
    with env.db() as db:
        db.execute("INSERT INTO ledger VALUES('l1',?,?,?,?,?,?,?,?)", (r["pid"], r["eid"], None, "observation",
                                                                      "三次失败后警报赶跑对手", "", "E01 结尾", 0))
    res = wb.import_knowledge(r["pid"], [dict(name="机制.md", data="三次失败后警报赶跑对手的机制".encode(), kind="mechanism"),
                                         dict(name="机制副本.md", data="三次失败后警报赶跑对手的机制".encode(), kind="mechanism"),
                                         dict(name="坏.md", data=b"\xff\xfe"), dict(name="未分类.md", data="灵感".encode())])
    assert [x["status"] for x in res] == ["readable", "duplicate", "corrupt", "needs_category"]
    wb.add_topic(r["pid"], "T1", "阿福三次失败后用警报赶跑偷猫粮的浣熊")
    t2 = wb.add_topic(r["pid"], "T2", "阿福让鹦鹉的假提示音引走浣熊")["topicId"]
    topics = wb.topics(r["pid"])
    assert topics[0]["similar"] and topics[0]["similar"][0]["episode"] == "E01"   # 看得到相似位置与差异
    e2 = wb.create_episode(r["pid"], "E02")["episodeId"]
    wb.start_episode(e2, "topic", {"create": {"provider": "codex"}, "review": {"provider": "claude"}}, topic_id=t2)
    t = wb.create_task(e2, "story", "按选题写")
    kid = res[0]["id"]
    wb.cite(t["taskId"], kid, "原例：三次失败机制，本集改成假提示音")
    msg = wb.post_message(t["taskId"], "参考原例写", "m")
    _, text = manifest_text(wb, msg["runId"])
    assert "假提示音引走浣熊" in text and "原例" in text                         # 选择进入本集草稿
    assert next(x for x in wb.topics(r["pid"]) if x["id"] == t2)["status"] == "开发中"


# 32 ---------------------------------------------------------------------------------------------
def test_32_long_content_and_compare_api(env):
    """UI scenario; the service part: compare returns field-level inline diffs for long text."""
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    long = "\n".join(f"第{i}段：阿福巡店。" for i in range(200))
    art = env.art(eid, "script")
    a = wb.save_manual(art["id"], None, dict(scenes=[dict(id="场01", action=long)]), "a")["versionId"]
    b = wb.save_manual(art["id"], a, dict(scenes=[dict(id="场01", action=long.replace("第100段", "第一百段"))]), "b")["versionId"]
    diff = wb.compare(a, b)["fields"]["scenes"]
    assert any(d["op"] == "add" and "一百" in d["text"] for d in diff)


# 拉片问答（模拟执行器；真实读图未实测） -----------------------------------------------------------
def test_33_ask_about_cut(env):
    wb = env.wb
    pid, eid = env.new_episode(name="末班渡轮", idea="末班渡轮上的检票员认出旧人")
    _, pack = make_pack(env, pid, eid)
    shas = [wb.store.put_blob(b"frame-before"), wb.store.put_blob(b"frame-after")]
    wb.ref_frames = lambda pack_id, around_ms=None: dict(before=dict(ms=around_ms - 500, sha256=shas[0]),
                                                         after=dict(ms=around_ms + 500, sha256=shas[1]))
    with pytest.raises(Rejected):                                              # 不能读图的执行器直接拒绝
        wb.ask_cut(pack["packId"], 9000, "00:09 是切镜吗？", "antigravity")
    q = wb.ask_cut(pack["packId"], 9000, "00:09 是切镜吗？", "codex")
    req = env.codex.calls[-1]
    assert req.kind == "cut_explanation" and len(req.images) == 2 and "00:09 是切镜吗" in req.prompt
    a = wb.ref_questions(pack["packId"])[0]
    assert a["id"] == q["questionId"] and a["state"] == "answered" and a["judgement"] == "cut"
    assert [e["atMs"] for e in a["evidence"]] == [8500, 9500] and a["frames"]["before"]["sha256"] == shas[0]
    env.codex.script = [lambda r: dict(schemaVersion=1, kind="cut_explanation", runId="别的运行", inputFingerprint="a" * 64,
                                       atMs=9000, judgement="cut", reason="x", evidence=[dict(atMs=1, observation="y")])]
    wb.ask_cut(pack["packId"], 9000, "再问一次", "codex")
    assert wb.ref_questions(pack["packId"])[0]["state"] == "failed"           # 不属于本次提问的回包不入库


def test_comparison_reuse_follows_actual_review_material(env):
    wb = env.wb
    r = env.full_episode(deliver=False, segments={"G01": [5000], "G02": [5000]})
    eid, pv = r["eid"], r["prompts"]
    prompt_art = env.art(eid, "prompts")
    content = prompt_art["versions"][-1]["content"]
    content["segments"]["G02"]["refs"][0]["role"] = "只作画风"
    v = wb.save_manual(prompt_art["id"], pv, content, "role-change")["versionId"]
    status = wb.prompt_review_status(v)
    assert status["G01"]["ok"] and status["G02"]["coldRead"]["state"] == "needed"
    r["plan"]["shots"][0]["camera"] = "正俯拍"
    env.save_adopt(eid, "plan", r["plan"], "camera-change")
    status = wb.prompt_review_status(pv)
    assert status["G01"]["coldRead"]["state"] == "passed" and status["G01"]["comparison"]["state"] == "needed"
    assert status["G02"]["ok"]
    wb.propose_setting_change(r["pid"], "rule", "灯光只能从左侧进入", "required", "空间连续", "new-setting")
    wb.choose_episode_settings(eid, True, "follow-setting")
    status = wb.prompt_review_status(pv)
    assert all(x["coldRead"]["state"] == "passed" and x["comparison"]["state"] == "needed" for x in status.values())


def test_prompt_adoption_blocks_an_invented_asset_and_malformed_sound(env):
    wb = env.wb
    episode = env.full_episode(segments={"G01": [8000]}, deliver=False)
    art = env.art(episode["eid"], "prompts")
    content = art["versions"][-1]["content"]
    seg = content["segments"]["G01"]
    seg["refs"][0]["assetVersionId"] = "unknown-id"
    seg["sound"] = "微弱水声"
    vid = wb.save_manual(art["id"], episode["prompts"], content, "bad-reference")["versionId"]
    gate = wb.adoption_gate(vid)
    assert not gate["adoptable"]
    assert any("不在本集" in problem for problem in gate["formatErrors"])
    assert any("sound" in problem for problem in gate["formatErrors"])
    with pytest.raises(Rejected, match="不在本集"):
        wb.start_prompt_review(vid, "G01", "cold_read", "reject-invalid-input")
