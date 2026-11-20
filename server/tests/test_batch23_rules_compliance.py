"""Regression tests for Batch 2 & 3 system rules compliance fixes."""
import json

from helpers import Env
from workbench.db import connect, dumps, transaction
from workbench.executors.base import ExecOutcome
from workbench.executors.fake import draft
from workbench import filmedit, productionmusic, videoqueue, videosegment, workflow


def test_supplement_conflict_judgment_uses_expert_not_writer(tmp_path):
    from test_continuous_finishing import finishing_fixture
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case["eid"]
    wb.save_episode_expert(eid, "antigravity")
    env.ag.script = [
        lambda req: dict(
            draft(req),
            kind="discussion",
            changes=[],
            directorRequest="",
            reply=json.dumps({"conflict": True, "keep": "existing", "reason": "沿用上游已登记设定"}, ensure_ascii=False),
        )
    ]
    before_codex = len(env.codex.calls)
    before_ag = len(env.ag.calls)
    res = wb._supplement_judgment(
        cid,
        "plan",
        {"id": "sup-1", "subject": "门锁", "statement": "门锁始终反锁"},
        {"subject": "门锁", "statement": "门锁本来开着"},
        {"shots": []},
    )
    assert res["conflict"] is True and res["keep"] == "existing"
    assert len(env.ag.calls) == before_ag + 1
    assert len(env.codex.calls) == before_codex


def test_video_critical_join_failure_preserves_expert_decision_options(tmp_path, monkeypatch):
    folder = tmp_path / "seg"
    (folder / "expert").mkdir(parents=True)
    video = folder / "result.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    from workbench.storage import file_sha256
    sha = file_sha256(video)
    (folder / "trial.json").write_text(json.dumps({"status": "downloaded", "sha256": sha}), encoding="utf-8")

    root = tmp_path / "delivery_root"
    src_dir = root / "交接来源"
    src_dir.mkdir(parents=True)
    script_path = src_dir / "剧本.json"
    board_path = src_dir / "制作方案.json"
    script_path.write_text(json.dumps({"shootingSegments": [{"id": "G01", "action": "开门"}]}), encoding="utf-8")
    board_path.write_text(json.dumps({"shots": [{"id": "S01", "segment": "G01", "action": "开门"}]}), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"files": {"交接来源/剧本.json": file_sha256(script_path), "交接来源/制作方案.json": file_sha256(board_path)}}),
        encoding="utf-8",
    )

    from workbench import videoreview
    monkeypatch.setattr(videosegment, "episode_expert", lambda *a, **kw: (None, {}, {}, root))
    monkeypatch.setattr(
        videoreview,
        "visual_review",
        lambda *a, **kw: {
            "evidence": {"sha256": sha},
            "visualReview": {
                "decision": "局部返工",
                "usableStart": 0.0,
                "usableEnd": 4.0,
                "join": "关键承接失败",
                "affectedShots": [1],
                "localChange": "修正开门手势",
                "reason": "与前段承接有偏差",
                "cutAdvice": "",
                "observations": [],
            },
        },
    )
    captured_options = []
    def fake_verdict(data, delivery, role, purpose, out, prompt, schema, *a, **kw):
        captured_options.append(schema["properties"]["action"]["enum"])
        return {"result": {"action": "采用并附剪点", "reason": "微调剪点即可承接，无需重拍"}}

    monkeypatch.setattr(videoreview, "media_verdict", fake_verdict)
    res = videosegment.review(tmp_path, "d1", "G01", folder, [], [], [])
    assert set(captured_options[0]) == {"采用", "采用并附剪点", "局部返工"}
    assert res["visualReview"]["decision"] == "采用并附剪点"


def test_soundtrack_view_and_handoff_explicitly_mark_unlistened(tmp_path):
    q = videoqueue.VideoQueue(tmp_path)
    delivery = "del-bgm-1"
    music_dir = q.root / delivery / "music"
    music_dir.mkdir(parents=True)
    audio = music_dir / "soundtrack.mp3"
    audio.write_bytes(b"ID3-audio")
    seg_dir = q.directory(delivery, "G01", 0)
    (seg_dir / "expert").mkdir(parents=True)
    clip = seg_dir / "result.mp4"
    clip.write_bytes(b"mp4")
    (seg_dir / "expert" / "visual-review.json").write_text(
        json.dumps({"evidence": {"sha256": "abc"}, "visualReview": {"usableStart": 0, "usableEnd": 5, "cutAdvice": ""}}),
        encoding="utf-8",
    )
    from workbench import videobudget
    with q.db() as db:
        videobudget.register(q, db, delivery, ["G01"])
        db.execute(
            "INSERT INTO job(delivery,segment,state,round,adopted,changes,deps) VALUES(?,?,'已采用',0,?,'[]','[]')",
            (delivery, "G01", str(seg_dir.relative_to(q.data))),
        )
        db.execute(
            "INSERT INTO soundtrack(delivery,state,brief,audio) VALUES(?,'已就绪',?,?)",
            (delivery, json.dumps({"segments": ["G01"], "publish": {"title": "t"}}), str(audio.relative_to(q.data))),
        )
        row = db.execute("SELECT * FROM soundtrack WHERE delivery=?", (delivery,)).fetchone()
        productionmusic.prepare_handoff(q, db, row)

    view = q.view(delivery)
    assert view["music"]["audioReady"] is True
    assert view["music"]["listened"] is False
    assert "未试听" in view["music"]["action"]

    handoff = json.loads((q.data / view["music"]["handoff"]).read_bytes())
    assert handoff["bgmAudioReady"] is True
    assert handoff["bgmListened"] is False
    assert "未试听" in handoff["bgmStatus"]


def test_finecut_undo_last_edit_clears_blocked_state(tmp_path):
    env = Env(tmp_path)
    case = env.full_episode(segments={"G01": [8000]})
    wb, pid, delivery = env.wb, case["pid"], case["delivery"]["deliveryId"]
    q = videoqueue.VideoQueue(wb.store.root)
    handoff_dir = q.root / delivery / "finecut"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "inputs.json").write_text("{}", encoding="utf-8")
    (handoff_dir / "render.json").write_text(
        json.dumps({"state": "修改受阻", "error": "剪点超出范围", "blocked": "rev1"}, ensure_ascii=False),
        encoding="utf-8",
    )
    with q.db() as db:
        db.execute(
            "INSERT INTO soundtrack(delivery,state,brief,handoff) VALUES(?,'已就绪','{}',?)",
            (delivery, str((handoff_dir / "inputs.json").relative_to(q.data))),
        )
    with transaction(wb.db_path) as db:
        db.execute("INSERT INTO free_thread(id,project_id,title) VALUES('th1',?,'t')", (pid,))
        db.execute("INSERT INTO free_turn(id,thread_id,text,reply,provider,status,reason,attempt,input_hash,manifest_relpath) VALUES('t1','th1','坏指令','ok','codex','succeeded',NULL,'1','h','m')")
        db.execute("INSERT INTO finecut_edit(delivery,turn,edits) VALUES(?,'t1','[]')", (delivery,))
    filmedit.undo_last_edit(wb, q, delivery)
    with connect(wb.db_path) as db:
        assert filmedit.notes(db, delivery) == []
    assert filmedit.status(handoff_dir).get("state") != "修改受阻"


def test_stage_ruling_for_publish_includes_publish_basis_and_goals_cleaned(tmp_path):
    from test_continuous_finishing import finishing_fixture
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case["eid"]
    captured = {}

    def spy_bounded(c, kind, obj_id, instruction, material, validate, stage="plan", fallback=None):
        captured["material"] = material
        return {"action": "adopt", "reason": "核对通过"}, "hash"

    wb._continuous_bounded_json = spy_bounded
    pub_vid, _ = env.save_adopt(eid, "publish", {"title": "T", "body": "B", "tags": ["#a"]}, "pub-v1")
    pub_ver = next(v for v in env.art(eid, "publish")["versions"] if v["id"] == pub_vid)
    wb._stage_ruling(cid, "publish", pub_ver, {"blocking": []})
    assert "publishBasis" in captured["material"]
    assert "segments" in captured["material"]["publishBasis"]
    assert "待导演选择" not in workflow.CONTINUOUS_GOALS["stress"]

    # Enhancement catalog maps 08-* to plan instead of legacy acting
    catalog = wb.enhancement_catalog()
    ch08 = [item for item in catalog if item["id"].startswith("08-")]
    if ch08:
        assert all("plan" in item["stages"] and "acting" not in item["stages"] for item in ch08)


def test_09_fix_mode_keeps_segment_and_seg2_note_targets_seg2(tmp_path):
    from test_continuous_finishing import finishing_fixture
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case["eid"]
    old_vid = env.art(eid, "prompts")["adopted_version_id"]
    content = wb._continuous_version(old_vid)
    content["segments"]["G02"]["notes"] = "G02专属备注标记-XYZ"
    with transaction(wb.db_path) as db:
        db.execute("UPDATE version SET content=? WHERE id=?", (dumps(content), old_vid))

    # Case 1: only G02 note is quoted -> must target G02 with notes_only=True, never G01
    with transaction(wb.db_path) as db:
        db.execute(
            "INSERT INTO impact VALUES('imp-note2',?,?,?,?,'workflow:prompts','仅影响 G02 备注','pending',?)",
            (case["pid"], eid, "workflow_change", "plan:v1", wb.clock()),
        )
    dispatched = []
    wb._continuous_prompt_revision = lambda c, v, affected, inst, *, notes_only=False: (dispatched.append((list(affected), notes_only)) or v)
    wb._continuous_prompt_checks = lambda c, v: v
    wb._continuous_adopt = lambda c, stage, v: None
    env.codex.script = [
        lambda req: dict(
            draft(req),
            kind="discussion",
            changes=[],
            directorRequest="",
            reply=json.dumps({"needsRevision": True, "reason": "修正 G02 备注", "quote": "G02专属备注标记-XYZ", "segments": []}, ensure_ascii=False),
        )
    ]
    wb._continuous_late_recheck(cid)
    assert dispatched == [(["G02"], True)]

    # Case 2: mode='fix' with empty scope segments -> must preserve G02 in task['segments']
    with transaction(wb.db_path) as db:
        db.execute("DELETE FROM impact WHERE id='imp-note2'")
        db.execute("UPDATE continuous_run SET mode='fix', scope=? WHERE id=?", (dumps([{"segments": [], "scenes": [], "needKeys": []}]), cid))
        db.execute(
            "INSERT INTO impact VALUES('imp-fix2',?,?,?,?,'workflow:prompts','交付后仅影响 G02','pending',?)",
            (case["pid"], eid, "workflow_change", "plan:v2", wb.clock()),
        )
    wb._continuous_cursor(cid, fixText="修复第2段")
    fix_tasks = []
    wb._fix_stage = lambda c, stg, task: fix_tasks.append((stg, task["segments"]))
    env.codex.script = [
        lambda req: dict(
            draft(req),
            kind="discussion",
            changes=[],
            directorRequest="",
            reply=json.dumps({"needsRevision": True, "reason": "仅影响 G02", "quote": "G02专属备注标记-XYZ", "segments": ["G02"]}, ensure_ascii=False),
        )
    ]
    wb._continuous_late_recheck(cid)
    assert fix_tasks == [("prompts", ["G02"])]


def test_06_cold_read_strips_author_notes_and_watch_purpose(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode(plan={"create": {"provider": "codex"}, "review": {"provider": "antigravity"}})
    env.adopt_story(eid)
    script_content = {
        "scenes": [{
            "id": "场01",
            "heading": "夜·店里",
            "action": "阿福推回抽屉。",
            "dialogue": "无",
            "watchPurpose": "SECRET_WATCH_INTENT_MARKER",
            "omit": "SECRET_OMIT_MARKER",
        }],
        "notes": "SECRET_AUTHOR_NOTE_MARKER",
    }
    env.save_adopt(eid, "script", script_content, "script-with-markers")
    skipped = wb.episode_view(eid)["skippedStages"].get("stress")
    if skipped:
        wb.set_optional_stage(eid, "stress", False, expected_reason=skipped)
    job = wb.create_task(eid, "stress", "审查本集剧本", command_id="stress-strip-check")
    env.drain(job["runId"])
    cold_calls = [c for c in env.ag.calls if c.purpose == "stress_cold_read"]
    assert len(cold_calls) == 1
    p = cold_calls[0].prompt
    assert "阿福推回抽屉" in p
    assert "SECRET_WATCH_INTENT_MARKER" not in p
    assert "SECRET_AUTHOR_NOTE_MARKER" not in p
    assert "SECRET_OMIT_MARKER" not in p
