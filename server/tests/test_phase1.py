"""Phase 1 acceptance tests (FakeExecutor only; no network). Assertions read back DB rows and files."""
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from workbench.app import create_app
from workbench.executors.fake import STORY, FakeExecutor, draft, quota_exhausted, review
from workbench.methodpack import load_pack
from workbench.service import Conflict, Rejected, Workbench

PUGGY_TERMS = re.compile(r"MILO|LEO|PUGGY|女主人|puggyroom|自媒体|胖橘猫|milo-active")
CARWASH = "# N07 洗车店\n\nMILO 发现洗车店的泡沫枪，决定给女主人的车来一次全自动洗车。\n".encode()


def plan(create="codex", rev="claude"):
    return {"create": {"provider": create}, "review": {"provider": rev}}


@pytest.fixture
def env(tmp_path):
    codex, claude = FakeExecutor("codex"), FakeExecutor("claude")
    wb = Workbench(tmp_path / "data", {"codex": codex, "claude": claude})
    return wb, codex, claude


def drain(wb, run_id):
    queue = [run_id]
    while queue:
        queue += wb.execute_run(queue.pop(0))


def db(wb):
    conn = sqlite3.connect(wb.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def manifest_text(wb, run_id):
    m = wb.run_manifest(run_id)["manifest"]
    return m, "\n".join(e["text"] for e in wb.store.manifest_texts(m))


def puggy_n07(wb):
    pid = wb.create_project("puggy")["projectId"]
    n06 = wb.create_episode(pid, "N06", "厨房", ["厨房门上了锁"])["episodeId"]
    n07 = wb.create_episode(pid, "N07", "洗车店")["episodeId"]
    imp = wb.import_file(pid, "N07_洗车店.md", CARWASH, "story_source", n07)["importId"]
    return pid, n06, n07, imp


def started_story(wb, n07, imp, n06, **kw):
    start = wb.start_episode(n07, "import", plan(**kw), source_import_id=imp, relation="continue",
                             predecessor_ids=[n06], command_id="kick-n07")
    task = wb.create_task(n07, "story", "按导入稿写本集故事", command_id="task-n07")
    drain(wb, task["runId"])
    return start, task


# ---------------------------------------------------------------- method pack split

def test_method_pack_is_versioned_and_generic():
    pack = load_pack("short-film@v1")
    assert pack["id"] == "short-film@v1"
    for text in pack["texts"].values():
        assert not PUGGY_TERMS.search(text)


def test_same_pack_puggy_and_shiba_inputs_do_not_leak(env):
    wb, codex, _ = env
    pid, n06, n07, imp = puggy_n07(wb)
    _, ptask = started_story(wb, n07, imp, n06)
    sid = wb.create_project("_template", "夜班柴犬")["projectId"]
    e1 = wb.create_episode(sid, "E01")["episodeId"]
    wb.start_episode(e1, "idea", plan(), idea_text="一只过分认真的柴犬第一次值夜班")
    stask = wb.create_task(e1, "story", "写第一稿")
    drain(wb, stask["runId"])
    sview = wb.episode_view(e1)
    v1 = sview["artifacts"][0]["versions"][0]["id"]
    rev = wb.submit_review(v1, "rv-shiba")
    drain(wb, rev["runId"])
    pm, ptext = manifest_text(wb, ptask["runId"])
    assert "MILO" in ptext and "厨房门上了锁" in ptext
    with db(wb) as conn:
        shiba_runs = [r[0] for r in conn.execute("SELECT run.id FROM run JOIN task ON task.id=run.task_id "
                                                 "WHERE task.project_id=?", (sid,))]
    assert len(shiba_runs) == 2
    for run_id in shiba_runs:
        m, text = manifest_text(wb, run_id)
        assert m["methodPack"]["id"] == pm["methodPack"]["id"] == "short-film@v1"
        assert m["methodPack"]["files"] == pm["methodPack"]["files"]
        assert not PUGGY_TERMS.search(text), run_id
        assert "本项目尚无设定条目" in text
    # the first shiba draft is traceable to the director's sentence
    _, first = manifest_text(wb, stask["runId"])
    assert "一只过分认真的柴犬第一次值夜班" in first


# ---------------------------------------------------------------- J49 → J50 → same-episode draft

def test_j49_import_kickoff_to_same_episode_draft(env):
    wb, codex, _ = env
    pid, n06, n07, imp = puggy_n07(wb)
    preview = wb.kickoff_preview(n07, "continue", [n06])
    assert preview["carriedFacts"] == ["厨房门上了锁"]
    assert preview["settings"] and preview["spec"]["aspectRatio"] == "9:16"
    assert [i["id"] for i in preview["imports"]] == [imp]
    start, task = started_story(wb, n07, imp, n06)
    assert start["carriedFacts"] == ["厨房门上了锁"]
    view = wb.episode_view(n07)
    assert view["kickoff"]["source_type"] == "import" and view["sourceImport"]["original_name"] == "N07_洗车店.md"
    assert view["sourceImport"]["parsed_text"] == CARWASH.decode().strip()   # original stays readable
    story = view["artifacts"][0]
    assert story["stage"] == "story" and story["episode_id"] == n07
    assert len(story["versions"]) == 1 and story["versions"][0]["origin"] == "ai"
    assert story["adopted_version_id"] is None      # import is a source, not an adopted/reviewed story
    _, text = manifest_text(wb, task["runId"])
    assert "厨房门上了锁" in text and "洗车店的泡沫枪" in text and "不当作已审稿" in text
    rec = wb.read_import(imp)
    assert rec["intact"] and (wb.store.root / rec["relpath"]).read_bytes() == CARWASH
    # duplicate kickoff: same command id -> same result; new command -> conflict
    assert wb.start_episode(n07, "import", plan(), source_import_id=imp, relation="continue",
                            predecessor_ids=[n06], command_id="kick-n07") == start
    with pytest.raises(Conflict):
        wb.start_episode(n07, "import", plan(), source_import_id=imp, relation="continue", predecessor_ids=[n06])


def test_independent_episode_does_not_carry_leftovers(env):
    wb, codex, _ = env
    pid, n06, n07, imp = puggy_n07(wb)
    assert wb.kickoff_preview(n07, "continue", [n06])["carriedFacts"] == ["厨房门上了锁"]
    assert wb.kickoff_preview(n07, "independent")["carriedFacts"] == []
    wb.start_episode(n07, "import", plan(), source_import_id=imp, relation="independent")
    task = wb.create_task(n07, "story", "写故事")
    _, text = manifest_text(wb, task["runId"])
    assert "厨房门上了锁" not in text


def test_first_episode_of_new_project_can_only_be_independent(env):
    wb, *_ = env
    sid = wb.create_project("_template", "夜班柴犬")["projectId"]
    e1 = wb.create_episode(sid, "E01")["episodeId"]
    preview = wb.kickoff_preview(e1)
    assert preview["settings"] == [] and preview["canContinue"] is False
    with pytest.raises(Rejected):
        wb.start_episode(e1, "idea", plan(), idea_text="柴犬值夜班", relation="continue", predecessor_ids=[])


def test_imported_script_is_story_source_and_script_waits_for_review(env):
    wb, *_ = env
    pid = wb.create_project("puggy")["projectId"]
    ep = wb.create_episode(pid, "N08", "雨巷")["episodeId"]
    imp = wb.import_file(pid, "雨巷_剧本.md", "场01 雨巷。MILO 撑伞。".encode(), "script_source", ep)["importId"]
    wb.start_episode(ep, "import", plan(), source_import_id=imp)
    task = wb.create_task(ep, "story", "从剧本整理故事")
    drain(wb, task["runId"])
    with pytest.raises(Rejected, match="独审"):
        wb.create_task(ep, "script", "写分场剧本")
    view = wb.episode_view(ep)
    story = view["artifacts"][0]
    vid = story["versions"][0]["id"]
    with pytest.raises(Rejected, match="必需独审未执行"):
        wb.adopt(story["id"], vid, None, "adopt-early")
    drain(wb, wb.submit_review(vid, "rv")["runId"])
    result = wb.adopt(story["id"], vid, None, "adopt-1", and_continue=True)
    assert result["next"]["stage"] == "script"
    script_task = wb.create_task(ep, "script", "写分场剧本")
    assert script_task["artifactId"] == result["next"]["artifactId"]
    _, text = manifest_text(wb, script_task["runId"])
    assert STORY["synopsis"] in text      # script reads the adopted story


def test_broken_import_keeps_diagnostic(env):
    wb, *_ = env
    pid = wb.create_project("puggy")["projectId"]
    bad = wb.import_file(pid, "坏文件.md", b"\xff\xfe\x00bad", "story_source")
    assert bad["parseStatus"] == "failed" and "UTF-8" in bad["diagnostic"]
    good = wb.import_file(pid, "好文件.md", "可以读".encode(), "story_source")
    assert good["parseStatus"] == "parsed"
    assert len(wb.project_view(pid)["imports"]) == 2


# ---------------------------------------------------------------- review, waiver, adoption

def violation_review(req):
    return review(req, "return", [
        dict(category="setting", settingId="milo-active", anchor="第2段", reason="MILO 发现异常却随手走开，与必守设定冲突"),
        dict(category="clarity", settingId=None, anchor="结尾", reason="笑点不清楚")])


def test_j59_required_violation_cannot_be_waived_ordinary_can(env):
    wb, codex, claude = env
    claude.script = [violation_review]
    pid, n06, n07, imp = puggy_n07(wb)
    _, task = started_story(wb, n07, imp, n06)
    story = wb.episode_view(n07)["artifacts"][0]
    v1 = story["versions"][0]["id"]
    drain(wb, wb.submit_review(v1, "rv1")["runId"])
    rv = wb.episode_view(n07)["artifacts"][0]["versions"][0]["reviews"][0]
    assert rv["isolation_level"] == "session" and rv["original_decision"] == "return"
    hard, soft = rv["issues"]
    assert hard["blocking"] == 1 and hard["setting_content"].startswith("MILO 精力旺盛")
    assert soft["blocking"] == 0
    with pytest.raises(Rejected, match="不能放行"):
        wb.waive(hard["id"], v1, "我觉得可以", "w-hard")
    with pytest.raises(Rejected, match="理由"):
        wb.waive(soft["id"], v1, "  ", "w-soft-empty")
    wb.waive(soft["id"], v1, "结尾笑点保留给下一集", "w-soft")
    gate = wb.adoption_gate(v1)
    assert gate["blocking"] == [hard["id"]] and gate["unresolved"] == [] and not gate["adoptable"]
    with pytest.raises(Rejected, match="必守"):
        wb.adopt(story["id"], v1, None, "adopt-v1")
    with db(wb) as conn:
        assert conn.execute("SELECT count(*) FROM waiver").fetchone()[0] == 1
        assert conn.execute("SELECT adopted_version_id FROM artifact WHERE id=?", (story["id"],)).fetchone()[0] is None
    # revise by user message, review again (default pass), adopt
    msg = wb.post_message(task["taskId"], "让 MILO 主动用本领处理收银异常", "m1", base_version_id=v1)
    drain(wb, msg["runId"])
    v2 = wb.episode_view(n07)["artifacts"][0]["versions"][1]
    assert v2["base_id"] == v1 and v2["gate"]["review"] is None          # old waiver does not carry over
    drain(wb, wb.submit_review(v2["id"], "rv2")["runId"])
    assert wb.adopt(story["id"], v2["id"], None, "adopt-v2")["adoptedVersionId"] == v2["id"]
    view = wb.episode_view(n07)["artifacts"][0]
    assert view["adopted_version_id"] == v2["id"]
    assert view["versions"][0]["reviews"][0]["original_decision"] == "return"   # original conclusion kept


def test_review_runs_in_new_isolated_session_with_manifest_only(env):
    wb, codex, claude = env
    pid, n06, n07, imp = puggy_n07(wb)
    _, task = started_story(wb, n07, imp, n06)
    wb.post_message(task["taskId"], "创作对话中的私下要求：别让审稿人知道", "chat-1")
    v1 = wb.episode_view(n07)["artifacts"][0]["versions"][0]["id"]
    rv = wb.submit_review(v1, "rv1")
    drain(wb, rv["runId"])
    req = claude.calls[-1]
    assert req.purpose == "review" and req.session_mode == "ephemeral"
    m, text = manifest_text(wb, rv["runId"])
    assert m["isolationLevel"] == "session" and m["sessionMode"] == "ephemeral"
    assert [e["role"] for e in m["inputs"]] == ["method", "method", "reviewed_version", "project_settings",
                                                "requirements"]
    assert "别让审稿人知道" not in req.prompt and "别让审稿人知道" not in text
    with db(wb) as conn:
        row = conn.execute("SELECT r.isolation_level, r.input_hash, run.input_hash FROM review r JOIN run "
                           "ON run.id=r.run_id").fetchone()
    assert row[0] == "session" and row[1] == row[2] == wb.run_manifest(rv["runId"])["fingerprint"]


def test_adoption_compares_versions(env):
    wb, *_ = env
    pid, n06, n07, imp = puggy_n07(wb)
    started_story(wb, n07, imp, n06)
    story = wb.episode_view(n07)["artifacts"][0]
    v1 = story["versions"][0]["id"]
    v2 = wb.save_manual(story["id"], v1, {"handoff": "目标时长 80 秒。"}, "manual-1")["versionId"]
    for v, cid in ((v1, "r1"), (v2, "r2")):
        drain(wb, wb.submit_review(v, cid)["runId"])
    first = wb.adopt(story["id"], v1, None, "tab-a")
    assert wb.adopt(story["id"], v1, None, "tab-a") == first          # repeated confirm reads same result
    with pytest.raises(Conflict) as exc:
        wb.adopt(story["id"], v2, None, "tab-b")                       # second tab still thinks nothing adopted
    assert exc.value.current == v1
    wb.adopt(story["id"], v2, v1, "tab-b-again")
    with db(wb) as conn:
        assert conn.execute("SELECT count(*) FROM adoption").fetchone()[0] == 2
        assert conn.execute("SELECT adopted_version_id FROM artifact WHERE id=?", (story["id"],)).fetchone()[0] == v2


def test_ai_draft_and_manual_draft_coexist(env):
    wb, *_ = env
    pid, n06, n07, imp = puggy_n07(wb)
    _, task = started_story(wb, n07, imp, n06)
    story = wb.episode_view(n07)["artifacts"][0]
    r1 = story["versions"][0]["id"]
    msg = wb.post_message(task["taskId"], "改结尾", "m-ai", base_version_id=r1)   # AI works from R1 ...
    r2 = wb.save_manual(story["id"], r1, {"synopsis": "导演手改的梗概。"}, "manual")["versionId"]   # ... user edits
    drain(wb, msg["runId"])
    versions = wb.episode_view(n07)["artifacts"][0]["versions"]
    assert [(v["display_no"], v["origin"], v["base_id"]) for v in versions] == \
        [(1, "ai", None), (2, "manual", r1), (3, "ai", r1)]
    assert versions[1]["content"]["synopsis"] == "导演手改的梗概。"


def test_hard_format_error_blocks_adoption_even_after_pass(env):
    wb, codex, _ = env
    codex.script = [lambda req: draft(req, synopsis="有梗概", cast="没有表格", handoff="无")]
    pid, n06, n07, imp = puggy_n07(wb)
    started_story(wb, n07, imp, n06)
    story = wb.episode_view(n07)["artifacts"][0]
    v1 = story["versions"][0]
    assert v1["gate"]["formatErrors"]
    drain(wb, wb.submit_review(v1["id"], "rv")["runId"])
    with pytest.raises(Rejected, match="结构错误"):
        wb.adopt(story["id"], v1["id"], None, "adopt")


# ---------------------------------------------------------------- rounds and messages

def test_rounds_count_only_auto_revisions_and_messages_do_not_reset(env):
    wb, codex, claude = env
    claude.script = [lambda r: review(r, "return", [dict(reason="动机不清", anchor="第1段")])] * 3
    pid, n06, n07, imp = puggy_n07(wb)
    wb.start_episode(n07, "import", plan(), source_import_id=imp, relation="continue", predecessor_ids=[n06])
    task = wb.create_task(n07, "story", "写故事", auto_revise=True)
    drain(wb, task["runId"])      # create -> review(return) -> revise#1 -> review -> revise#2 -> review -> stop
    with db(wb) as conn:
        t = conn.execute("SELECT used_rounds,state FROM task WHERE id=?", (task["taskId"],)).fetchone()
        runs = [tuple(r) for r in conn.execute("SELECT purpose,counted_round FROM run ORDER BY created_at")]
    assert tuple(t) == (2, "rounds_exhausted")
    assert runs == [("create", 0), ("review", 0), ("revise", 1), ("review", 0), ("revise", 1), ("review", 0)]
    first = wb.post_message(task["taskId"], "明确再改一次", "msg-1")
    again = wb.post_message(task["taskId"], "明确再改一次", "msg-1")     # duplicate delivery of same message
    assert again == first
    with pytest.raises(Conflict):
        wb.post_message(task["taskId"], "换了内容", "msg-1")
    with db(wb) as conn:
        assert conn.execute("SELECT count(*) FROM run WHERE purpose='revise' AND counted_round=0").fetchone()[0] == 1
        assert conn.execute("SELECT used_rounds FROM task WHERE id=?", (task["taskId"],)).fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM message WHERE command_id='msg-1'").fetchone()[0] == 0
    drain(wb, first["runId"])
    with db(wb) as conn:
        assert conn.execute("SELECT used_rounds FROM task WHERE id=?", (task["taskId"],)).fetchone()[0] == 2


# ---------------------------------------------------------------- result validation and failures

@pytest.mark.parametrize("bad", [
    lambda r: dict(draft(r), runId="other-run"),
    lambda r: dict(draft(r), inputFingerprint="0" * 64),
    lambda r: draft(r, adopted="越权字段"),
    lambda r: dict(draft(r), baseVersionId="R99"),
    lambda r: {"kind": "draft"},
])
def test_invalid_draft_results_are_not_stored(env, bad):
    wb, codex, _ = env
    codex.script = [bad, bad]
    pid, n06, n07, imp = puggy_n07(wb)
    started_story(wb, n07, imp, n06)
    with db(wb) as conn:
        assert conn.execute("SELECT count(*) FROM version").fetchone()[0] == 0
        status, attempts = conn.execute("SELECT status, format_attempts FROM run").fetchone()
    assert status == "failed" and attempts == 1     # one same-content re-format request, then manual retry


def test_forged_setting_id_and_missing_coverage_rejected(env):
    wb, codex, claude = env
    forged = lambda r: review(r, "return", [dict(category="setting", settingId="made-up", reason="x")])
    claude.script = [forged, forged]
    pid, n06, n07, imp = puggy_n07(wb)
    started_story(wb, n07, imp, n06)
    v1 = wb.episode_view(n07)["artifacts"][0]["versions"][0]["id"]
    drain(wb, wb.submit_review(v1, "rv")["runId"])
    with db(wb) as conn:
        assert conn.execute("SELECT count(*) FROM review").fetchone()[0] == 0
        assert "不存在的设定编号" in conn.execute("SELECT diagnostic FROM run WHERE purpose='review'").fetchone()[0]


def test_format_retry_recovers_once(env):
    wb, codex, _ = env
    codex.script = [lambda r: dict(draft(r), runId="x"), None]
    pid, n06, n07, imp = puggy_n07(wb)
    started_story(wb, n07, imp, n06)
    with db(wb) as conn:
        assert tuple(conn.execute("SELECT status, format_attempts FROM run").fetchone()) == ("succeeded", 1)
        assert conn.execute("SELECT used_rounds FROM task").fetchone()[0] == 0


def test_quota_stops_at_waiting_without_switching(env):
    wb, codex, claude = env
    codex.script = [quota_exhausted]
    pid, n06, n07, imp = puggy_n07(wb)
    _, task = started_story(wb, n07, imp, n06)
    with db(wb) as conn:
        run = conn.execute("SELECT id,status,quota_resets_at FROM run").fetchone()
        state = conn.execute("SELECT state FROM task").fetchone()[0]
    assert run["status"] == "waiting_quota" and state == "waiting_quota" and run["quota_resets_at"]
    assert len(codex.calls) == 1 and claude.calls == []          # no automatic switch
    retry = wb.retry_run(run["id"], provider="claude", command_id="retry-1")   # explicit user choice
    old_m, _ = manifest_text(wb, run["id"])
    new_m, _ = manifest_text(wb, retry["runId"])
    assert [e["sha256"] for e in old_m["inputs"]] == [e["sha256"] for e in new_m["inputs"]]
    drain(wb, retry["runId"])
    assert len(claude.calls) == 1 and wb.episode_view(n07)["artifacts"][0]["versions"]


def test_frozen_snapshot_detects_tampering(env):
    wb, *_ = env
    pid, n06, n07, imp = puggy_n07(wb)
    wb.start_episode(n07, "import", plan(), source_import_id=imp)
    task = wb.create_task(n07, "story", "写故事")
    manifest_path = wb.store.root / f"runs/{task['runId']}/input-manifest.json"
    data = json.loads(manifest_path.read_text())
    data["inputs"][0]["name"] = "changed.md"
    manifest_path.write_text(json.dumps(data))
    drain(wb, task["runId"])
    with db(wb) as conn:
        assert tuple(conn.execute("SELECT status, reason FROM run").fetchone()) == ("failed", "输入清单指纹不符")


# ---------------------------------------------------------------- HTTP layer

def test_http_guard_and_conflict(env):
    wb, *_ = env
    app = create_app(wb, inline_worker=True, token="t0k")
    client = TestClient(app)
    assert client.post("/api/projects", json={"configId": "puggy"}).status_code == 403
    assert client.post("/api/projects", json={"configId": "puggy"},
                       headers={"x-adw-token": "t0k", "origin": "https://evil.example"}).status_code == 403
    h = {"x-adw-token": client.get("/api/session").json()["token"]}
    pid = client.post("/api/projects", json={"configId": "puggy", "commandId": "p1"}, headers=h).json()["projectId"]
    eid = client.post(f"/api/projects/{pid}/episodes", json={"displayCode": "N07"}, headers=h).json()["episodeId"]
    imp = client.post(f"/api/projects/{pid}/imports", files={"file": ("N07_洗车店.md", CARWASH)},
                      data={"purpose": "story_source", "episodeId": eid}, headers=h).json()["importId"]
    client.post(f"/api/episodes/{eid}/start", json={"sourceType": "import", "sourceImportId": imp,
                                                      "executorPlan": plan()}, headers=h).raise_for_status()
    task = client.post(f"/api/episodes/{eid}/tasks", json={"stage": "story", "goal": "写"}, headers=h).json()
    story = client.get(f"/api/episodes/{eid}").json()["artifacts"][0]
    vid = story["versions"][0]["id"]
    client.post(f"/api/versions/{vid}/reviews", json={"commandId": "rv"}, headers=h).raise_for_status()
    ok = client.post(f"/api/artifacts/{story['id']}/adoptions",
                     json={"versionId": vid, "expectedAdoptedVersionId": None, "commandId": "a1"}, headers=h)
    assert ok.status_code == 200
    stale = client.post(f"/api/artifacts/{story['id']}/adoptions",
                        json={"versionId": vid, "expectedAdoptedVersionId": None, "commandId": "a2"}, headers=h)
    assert stale.status_code == 409 and stale.json()["current"] == vid
    assert client.get(f"/api/runs/{task['runId']}/manifest").json()["matches"] is True
