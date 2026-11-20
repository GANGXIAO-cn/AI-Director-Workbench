"""Regression tests for Batch 1 system rules compliance fixes."""
import json
from pathlib import Path

import pytest

from helpers import Env
from workbench.continuous import Halt
from workbench.db import connect, dumps, transaction
from workbench.errors import Rejected
from workbench.executors.base import ExecOutcome
from workbench.executors.fake import draft
from workbench.imageassess import coarse_results
from workbench.storycheck import check_story
from workbench import h3rework


def test_readonly_recheck_format_failure_never_dispatches_content_repair(tmp_path):
    """E23 regression: invalid JSON during late recheck must halt, never dispatch plan repair."""
    from test_continuous_finishing import finishing_fixture
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case["eid"]
    plan_before = len(env.art(eid, "plan")["versions"])
    wb._continuous_cursor(cid, stage="plan", phase="delivery")
    with transaction(wb.db_path) as db:
        db.execute(
            "INSERT INTO impact VALUES('imp-e23',?,?,?,?,'workflow:plan','素材变化影响核对','pending',?)",
            (case["pid"], eid, "palette_change", "asset:v1", wb.clock()),
        )
    bad = lambda req: dict(draft(req), kind="discussion", changes=[], directorRequest="", reply="这不是JSON")
    env.codex.script = [bad, bad]
    with pytest.raises(Halt):
        wb._continuous_late_recheck(cid)
    rec = wb._continuous_record(cid)
    assert rec["state"] == "waiting_external"
    assert "不派发内容返修" in (rec["reason"] or "")
    assert rec["cursor"].get("blocks")
    assert not rec["cursor"].get("nextRepair")
    assert len(env.art(eid, "plan")["versions"]) == plan_before


def test_prompt_batch_review_is_rejected(tmp_path):
    env = Env(tmp_path)
    case = env.full_episode(segments={"G01": [4000], "G02": [4000]}, deliver=False)
    vid = env.art(case["eid"], "prompts")["adopted_version_id"]
    with pytest.raises(Rejected, match="已取消整版批量送审"):
        env.wb.start_prompt_review_batch(vid, "cold_read", "batch-rejected")


def test_late_recheck_prompts_revises_only_affected_segment(tmp_path):
    from test_continuous_finishing import finishing_fixture
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case["eid"]
    old_vid = env.art(eid, "prompts")["adopted_version_id"]
    old_content = wb._continuous_version(old_vid)
    g02_before = old_content["segments"]["G02"]
    quote = old_content["segments"]["G01"]["txt"][:12]

    with transaction(wb.db_path) as db:
        db.execute(
            "INSERT INTO impact VALUES('imp-p1',?,?,?,?,'workflow:prompts','仅影响 G01 的变更','pending',?)",
            (case["pid"], eid, "workflow_change", "plan:v1", wb.clock()),
        )

    called_prompts = []
    def recheck_reply(req):
        return dict(
            draft(req),
            kind="discussion",
            changes=[],
            directorRequest="",
            reply=json.dumps({"needsRevision": True, "reason": "只改 G01 动作描述", "quote": quote, "segments": ["G01"]}, ensure_ascii=False),
        )

    def spy_revision(c, v, affected, instruction, *, notes_only=False):
        called_prompts.append((list(affected), notes_only))
        return v

    wb._continuous_prompt_revision = spy_revision
    wb._continuous_prompt_checks = lambda c, v: v
    wb._continuous_adopt = lambda c, stage, v: None
    env.codex.script = [recheck_reply]
    wb._continuous_late_recheck(cid)
    assert called_prompts == [(["G01"], False)]
    assert wb._continuous_version(old_vid)["segments"]["G02"] == g02_before


def test_h3_rework_with_bindings_still_blocks_untargeted_unrelated_shot(tmp_path):
    source = (
        "subject_definitions:\n"
        "- <Picture 1> hero\n"
        "detailed_description:\n"
        "[Shot 1] 00:00-00:03 Hero walks to door.\n"
        "[Shot 2] 00:03-00:06 Hero opens the door.\n"
        "[Shot 3] 00:06-00:09 Cat sleeps on sofa.\n"
        "overall_soundscape:\n"
        "Quiet room.\n"
    )
    class StubExec:
        def __init__(self, responses):
            self.responses = list(responses)
        def start(self, req):
            return ExecOutcome(ok=True, structured=self.responses.pop(0))

    import workbench.videosegment as vs
    orig_expert = vs.episode_expert

    # Attempt to touch untargeted Shot 3 (neither in changes nor using Picture 2) must fail
    bad_exec = StubExec([
        {"edits": [
            {"shot": 1, "before": "Hero walks to door.", "after": "Hero steps quickly to door with <Picture 2>."},
            {"shot": 3, "before": "Cat sleeps on sofa.", "after": "Cat jumps off sofa."},
        ]},
        {"edits": [
            {"shot": 1, "before": "Hero walks to door.", "after": "Hero steps quickly to door with <Picture 2>."},
            {"shot": 3, "before": "Cat sleeps on sofa.", "after": "Cat jumps off sofa."},
        ]},
    ])
    vs.episode_expert = lambda *a, **kw: (bad_exec, {"provider": "stub"}, {}, None)
    try:
        with pytest.raises(ValueError, match="只改评审点名的镜号"):
            h3rework.compile_revision(
                tmp_path, "d1", source,
                ["[Shot 1] 加快走向门的速度"],
                [{"slot": 2, "note": "承接前段手部状态", "imageHash": "abc"}],
                tmp_path / "r1",
            )
    finally:
        vs.episode_expert = orig_expert

    # Attempt to touch Shot 3 even with <Picture 2> when binding explicitly targets Shot 2 must fail
    wrong_binding_exec = StubExec([
        {"edits": [
            {"shot": 1, "before": "Hero walks to door.", "after": "Hero steps quickly to door."},
            {"shot": 3, "before": "Cat sleeps on sofa.", "after": "Cat jumps off sofa with <Picture 2>."},
        ]},
        {"edits": [
            {"shot": 1, "before": "Hero walks to door.", "after": "Hero steps quickly to door."},
            {"shot": 3, "before": "Cat sleeps on sofa.", "after": "Cat jumps off sofa with <Picture 2>."},
        ]},
    ])
    vs.episode_expert = lambda *a, **kw: (wrong_binding_exec, {"provider": "stub"}, {}, None)
    try:
        with pytest.raises(ValueError, match="只改评审点名的镜号"):
            h3rework.compile_revision(
                tmp_path, "d1", source,
                ["[Shot 1] 加快走向门的速度"],
                [{"slot": 2, "note": "[Shot 2] 承接前段手部状态", "imageHash": "abc"}],
                tmp_path / "r1b",
            )
    finally:
        vs.episode_expert = orig_expert

    # Touching Shot 1 (targeted) + Shot 2 (designated binding target carrying <Picture 2>) must pass
    good_exec = StubExec([
        {"edits": [
            {"shot": 1, "before": "Hero walks to door.", "after": "Hero steps quickly to door."},
            {"shot": 2, "before": "Hero opens the door.", "after": "Hero opens the door holding <Picture 2>."},
        ]},
    ])
    vs.episode_expert = lambda *a, **kw: (good_exec, {"provider": "stub"}, {}, None)
    try:
        revised = h3rework.compile_revision(
            tmp_path, "d1", source,
            ["[Shot 1] 加快走向门的速度"],
            [{"slot": 2, "note": "[Shot 2] 承接前段手部状态", "imageHash": "abc"}],
            tmp_path / "r2",
        )
        assert "Hero steps quickly to door." in revised
        assert "<Picture 2>" in revised
        assert "Cat sleeps on sofa." in revised
    finally:
        vs.episode_expert = orig_expert


def test_story_check_allows_synopsis_only_without_legacy_cast_and_handoff():
    assert check_story({"title": "夜班", "synopsis": "阿福值夜班并发现抽屉未关，最终妥善处理。"}) == []
    assert check_story({"synopsis": "阿福值夜班。", "cast": "", "handoff": ""}) == []
    assert any("Markdown 表格" in e for e in check_story({"synopsis": "阿福值夜班。", "cast": "- 阿福：主角"}))


def test_stress_two_pass_cold_read_isolation_and_review_executor(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode(plan={"create": {"provider": "codex"}, "review": {"provider": "antigravity"}})
    env.adopt_story(eid)
    env.script(eid)
    skipped = wb.episode_view(eid)["skippedStages"].get("stress")
    if skipped:
        wb.set_optional_stage(eid, "stress", False, expected_reason=skipped)

    # Manual create_task for stage='stress' must route to review executor ('ag'), not create ('codex')
    job = wb.create_task(eid, "stress", "审查本集剧本", command_id="manual-stress")
    env.drain(job["runId"])

    cold_calls = [c for c in env.ag.calls if c.purpose == "stress_cold_read"]
    diag_calls = [c for c in env.ag.calls if c.purpose == "create"]
    assert len(cold_calls) == 1
    assert len(diag_calls) == 1
    # Cold read prompt must contain ONLY pure script body, never kickoff / settings / notes / watchPurpose
    cold_prompt = cold_calls[0].prompt
    assert "adopted_script" in cold_prompt
    assert "kickoff" not in cold_prompt
    assert "project_settings" not in cold_prompt
    assert "adopted_story" not in cold_prompt
    assert "观看作用与保留过程" not in cold_prompt
    assert "作者备注" not in cold_prompt
    # Diagnosis pass must receive the cold-read retelling and preserve it in the saved version
    assert "cold_read_retelling" in diag_calls[0].prompt
    stress_art = env.art(eid, "stress")
    saved = stress_art["versions"][-1]["content"]
    assert saved["retelling"].startswith("冷读复述（仅依据剧本正文）")


def test_stress_repair_verify_uses_review_executor(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode(plan={"create": {"provider": "codex"}, "review": {"provider": "antigravity"}})
    env.adopt_story(eid)
    env.script(eid)
    skipped = wb.episode_view(eid)["skippedStages"].get("stress")
    if skipped:
        wb.set_optional_stage(eid, "stress", False, expected_reason=skipped)
    report = dict(
        retelling="冷读复述",
        scope="本集",
        diagnosis="一处动机不足",
        issues=[dict(id="I01", stage="script", severity="must", location="S01", summary="动机不足", evidence="原句", impact="削弱收尾")],
        options=[dict(issueId="I01", proposal="补一句交代动机", changesCore=False)],
        productionNotes=[],
    )
    rid, _ = env.save_adopt(eid, "stress", report, "stress-v1")
    plan_id = wb.start_repair_plan(eid, "report", "rp-1", rid, {"I01": 0})["planId"]
    # Mark revise step done so next step is verify
    with transaction(wb.db_path) as db:
        steps = json.loads(db.execute("SELECT steps FROM repair_plan WHERE id=?", (plan_id,)).fetchone()[0])
        for s in steps:
            if s["action"] != "verify":
                s["state"] = "done"
        db.execute("UPDATE repair_plan SET steps=? WHERE id=?", (dumps(steps), plan_id))
    run_id = wb.advance_repair_plan(plan_id, "run-verify")["runId"]
    with connect(wb.db_path) as db:
        provider = db.execute("SELECT provider FROM run WHERE id=?", (run_id,)).fetchone()[0]
    assert provider == "antigravity"


def test_07_observation_backfill_does_not_skip_independent_image_review_and_keeps_minor(tmp_path):
    # coarse_results preserves minor and observation while remaining compatible with minimal replies
    parsed = coarse_results(
        [
            {"versionId": "v1", "verdict": "usable", "observation": "角色穿蓝衣站在门厅", "minor": [{"where": "衣领", "text": "扣子略偏移"}]},
            {"versionId": "v2", "verdict": "usable"},
        ],
        ["v1", "v2"],
    )
    assert parsed[0]["observation"] == "角色穿蓝衣站在门厅"
    assert parsed[0]["minor"] == [{"where": "衣领", "text": "扣子略偏移"}]
    assert parsed[1]["minor"] == []

    # Verify observationOnly recordKind='observation' is not treated as independent review
    env = Env(tmp_path)
    case = env.full_episode(segments={"G01": [4000]}, deliver=False)
    wb, eid = env.wb, case["eid"]
    img_vid = case["img"]["versionId"]
    with transaction(wb.db_path) as db:
        db.execute(
            "INSERT INTO image_assessment VALUES('obs-1',?,NULL,?,'r-obs','usable',?,?)",
            (eid, img_vid, dumps(dict(versionId=img_vid, verdict="usable", observation="专家补观察", recordKind="observation")), wb.clock()),
        )
    known = {r["version_id"] for r in wb.image_assessments(eid) if r["body"].get("recordKind") != "observation"}
    assert img_vid not in known
