"""Workflow acceptance at the service boundary (offline; no model quality claims)."""
import json
import sqlite3

import pytest

from workbench.db import SCHEMA, connect, initialize
from workbench.errors import Rejected
from workbench.service import Workbench
from workbench.executors.fake import FakeExecutor
from workbench.workflow import METHOD_REF, ORDER


def episode(tmp_path, *, locked=False, config="_template"):
    wb = Workbench(tmp_path / "data", {"codex": FakeExecutor("codex")})
    pid = wb.create_project(config, "统一流程验收")["projectId"]
    selection = {"provider": "codex", "model": "gpt-6.1-sol", "effort": "high"}
    args = dict(episode_id=None, project_id=pid, display_code="E01", executor_plan={"create": selection, "review": selection})
    if locked:
        source = "场01 外景 渡口 黄昏\n学徒提着蛋糕赶来。\n学徒：这是给你的。\n老人接过蛋糕，船离岸。"
        imported = wb.import_file(pid, "定稿.txt", source.encode(), "script_source")
        args.update(source_type="import", source_import_id=imported["importId"], source_intent="locked_screenplay")
    else:
        args.update(source_type="idea", idea_text="学徒在末班船离岸前交付蛋糕。")
    result = wb.start_episode(**args)
    return wb, pid, result["episodeId"]


def save_adopt(wb, eid, stage, content):
    aid = wb.open_stage(eid, stage)["artifactId"]
    art = next(a for a in wb.episode_view(eid)["artifacts"] if a["id"] == aid)
    vid = wb.save_manual(aid, art["adopted_version_id"], content, f"save-{stage}-{len(art['versions'])}")["versionId"]
    wb.adopt(aid, vid, art["adopted_version_id"], f"adopt-{vid}")
    return vid


def test_separate_stages_skip_and_actual_dependency_manifest(tmp_path):
    wb, _, eid = episode(tmp_path)
    with pytest.raises(Rejected, match="04 分场"):
        wb.open_stage(eid, "script")
    save_adopt(wb, eid, "story", {"synopsis": "学徒发现寿星走错码头，借摆渡人的铃声叫回老人，交付蛋糕。"})
    save_adopt(wb, eid, "characters", {"characters": [{"id": "apprentice", "name": "学徒", "identity": "蛋糕店学徒", "goal": "交付蛋糕", "behavior": "观察并行动"}]})
    with pytest.raises(Rejected, match="03 世界规则"):
        wb.open_stage(eid, "treatment")
    wb.set_optional_stage(eid, "world", True, "现实世界，无新增影响选择的规则")
    treatment = save_adopt(wb, eid, "treatment", {"scenes": [{"id": "SC01", "heading": "渡口 黄昏", "event": "叫回老人", "result": "交付蛋糕"}], "durationBudget": "20秒估计"})
    result = wb.create_task(eid, "script", "按已采用分场写完整剧本")
    manifest = wb.run_manifest(result["runId"])["manifest"]
    assert manifest["methodPack"]["id"] == METHOD_REF
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "milo-leo" not in serialized
    entries = wb.store.manifest_texts(manifest)
    assert any(e["name"] == "treatment:" + treatment for e in entries)
    assert any(e["role"] == "workflow_skip" and e["name"] == "world" for e in entries)
    assert len({a["stage"] for a in wb.episode_view(eid)["artifacts"]}) == 4


def test_locked_screenplay_has_same_downstream_without_fake_early_adoptions(tmp_path):
    wb, _, eid = episode(tmp_path, locked=True)
    view = wb.episode_view(eid)
    assert [a["stage"] for a in view["artifacts"]] == ["script"]
    original = view["sourceImport"]["sha256"]
    save_adopt(wb, eid, "script", {"scenes": [{"id": "SC01", "heading": "渡口 黄昏", "action": "学徒提着蛋糕赶来。\n学徒：这是给你的。\n老人接过蛋糕，船离岸。", "dialogue": "这是给你的。"}]})
    assert view["skippedStages"]["stress"].startswith("导演已锁定剧本")
    wb.open_stage(eid, "asset_design")
    run = wb.create_task(eid, "asset_design", "按定稿整理轻资产")
    entries = wb.store.manifest_texts(wb.run_manifest(run["runId"])["manifest"])
    assert any("定稿来源" in e["text"] for e in entries if e["role"] == "kickoff")
    assert wb.episode_view(eid)["sourceImport"]["sha256"] == original
    assert not any(a["stage"] == "story" for a in wb.episode_view(eid)["artifacts"])


def test_old_schema_migrates_without_losing_refs_or_history(tmp_path):
    path = tmp_path / "legacy.sqlite"
    old = SCHEMA.replace("'story','characters','world','treatment','script','stress','asset_design','acting','plan','prompts','publish'", "'story','script','plan','prompts','publish'").replace("'space','cast','world','stress','acting'", "'space','cast'")
    with sqlite3.connect(path) as db:
        db.executescript(old)
        db.execute("INSERT INTO project VALUES('p','项目','','_template','short-film@v7',0,'{}','{}',1,0)")
        db.execute("INSERT INTO episode(id,project_id,display_code,created_at) VALUES('e','p','E01',0)")
        db.execute("INSERT INTO artifact(id,project_id,episode_id,stage) VALUES('a','p','e','story')")
        db.execute("INSERT INTO version VALUES('v','a',1,NULL,'manual',NULL,'{}','digest','',0)")
        db.execute("UPDATE artifact SET adopted_version_id='v' WHERE id='a'")
    initialize(path)
    initialize(path)  # idempotent, no second backup or archive
    with connect(path) as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT adopted_version_id FROM artifact WHERE id='a'").fetchone()[0] == "v"
        assert db.execute("SELECT id FROM artifact_before_nine_stages").fetchone()[0] == "a"
        assert any(r[2] == "artifact" for r in db.execute("PRAGMA foreign_key_list(version)"))
        for stage in ORDER[1:]:
            db.execute("INSERT INTO artifact(id,project_id,episode_id,stage) VALUES(?,?,?,?)", (stage, "p", "e", stage))
    assert len(list((tmp_path / "backups").glob("*.sqlite"))) == 1


def test_draft_preserves_adopted_outputs_but_adoption_marks_real_dependencies(tmp_path):
    from helpers import Env
    env = Env(tmp_path)
    _, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    wb = env.wb
    art = env.art(eid, "characters")
    before = {a["stage"]:a["adopted_version_id"] for a in wb.episode_view(eid)["artifacts"]}
    content = dict(art["versions"][-1]["content"])
    content["characters"] = [dict(content["characters"][0], goal="先核对收据再完成交接")]
    vid = wb.save_manual(art["id"], art["adopted_version_id"], content, "revised-character")["versionId"]
    with connect(wb.db_path) as db:
        assert not db.execute("SELECT id FROM impact WHERE kind='workflow_change'").fetchall()
    assert before == {a["stage"]:a["adopted_version_id"] for a in wb.episode_view(eid)["artifacts"]}
    wb.adopt(art["id"], vid, art["adopted_version_id"], "adopt-character-revision")
    with connect(wb.db_path) as db:
        targets = {r[0] for r in db.execute("SELECT target_ref FROM impact WHERE kind='workflow_change' AND state='pending'")}
    assert targets == {"workflow:treatment", "workflow:script", "workflow:asset_design"}
    assert env.art(eid, "script")["adopted_version_id"] == before["script"]


def test_full_delivery_keeps_stage_sources_without_requiring_publish(tmp_path):
    from helpers import Env
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01":[8000]})
    env.save_adopt(eid, "plan", plan)
    image = env.image(pid, eid)
    prompts, _ = env.save_adopt(eid, "prompts", env.prompts_for(plan, image["versionId"]))
    env.review_segments(prompts, ["G01"])
    delivery = wb.deliver(eid, "full", "without-publish")
    from pathlib import Path
    root = Path(delivery["path"])
    manifest = json.loads((root / "episode.json").read_text())
    evidence = manifest["workbench"]["workflow"]
    assert evidence["method"] == METHOD_REF
    assert set(evidence["documents"]) == {"story", "characters", "treatment", "script", "asset_design", "plan", "prompts"}
    assert set(evidence["skipped"]) == {"world", "stress", "acting"}
    assert manifest["workbench"]["adopted"]["publish"] is None
    for stage, source in evidence["documents"].items():
        import hashlib
        data = (root / source["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == source["sha256"]
        assert source["version"] == env.art(eid, stage)["adopted_version_id"]


def test_comparison_only_reopens_changed_scene_and_neighbour(tmp_path):
    from helpers import Env
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({f"G{i:02}": [8000] for i in range(1, 6)})
    plan["shots"][2]["origin"]["scene"] = "场01"
    env.save_adopt(eid, "plan", plan)
    image = env.image(pid, eid)
    vid, _ = env.save_adopt(eid, "prompts", env.prompts_for(plan, image["versionId"]))
    env.review_segments(vid, [s["id"] for s in plan["segments"]])
    before = wb.prompt_review_status(vid)
    assert len(before) == 5 and all(s["ok"] for s in before.values())
    art = env.art(eid, "script")
    content = art["versions"][-1]["content"]
    content["scenes"][0]["action"] += "阿福用爪按住门锁。"
    env.save_adopt(eid, "script", content, "changed-scene-one")
    after = wb.prompt_review_status(vid)
    assert {s for s in after if not after[s]["ok"]} == {"G02", "G03", "G04"}
    assert all(s["coldRead"]["state"] == "passed" for s in after.values())
    for seg in ["G01", "G02", "G03"]:
        run = wb.start_prompt_review(vid, seg, "comparison", "inspect-"+seg)["runId"]
        inputs = wb.store.manifest_texts(wb.run_manifest(run)["manifest"])
        assert any(i["role"] == "requirements" for i in inputs)
        changed = [i["role"] for i in inputs if i.get("text") and "用爪按住门锁" in i["text"]]
        assert changed == {"G01": [], "G02": ["neighbour_script_scene_source"], "G03": ["script_scene_source"]}[seg]


def test_locked_prompt_draft_reads_available_full_sources(tmp_path):
    from helpers import Env
    wb, _, eid = episode(tmp_path, locked=True)
    save_adopt(wb, eid, "script", {"scenes": [{"id":"SC01", "heading":"渡口 黄昏", "action":"老人接过蛋糕，船离岸。"}]})
    assert wb.episode_view(eid)["skippedStages"]["stress"]
    save_adopt(wb, eid, "asset_design", {"strategy":"lightweight", "assets":[dict(id="cake",kind="prop",description="蛋糕",imagePrompt="生日蛋糕")]})
    wb.set_optional_stage(eid, "acting", True, "沿用原文")
    plan = Env(tmp_path / "fixture").plan86({"G01":[8000]})
    plan["shots"][0]["origin"] = dict(scene="SC01", line="老人接过蛋糕")
    save_adopt(wb, eid, "plan", plan)
    task = wb.create_task(eid, "prompts", "按上游稿起草")
    roles = {i["role"] for i in wb.run_manifest(task["runId"])["manifest"]["inputs"]}
    assert {"adopted_plan","adopted_script","adopted_asset_design","script_scene_source"} <= roles
    assert "adopted_characters" not in roles


def test_stress_restore_retelling_and_dialogue_isolation(tmp_path):
    wb, _, eid = episode(tmp_path, locked=True)
    skip = wb.episode_view(eid)["skippedStages"]["stress"]
    wb.set_optional_stage(eid, "stress", False, expected_reason=skip)
    save_adopt(wb, eid, "script", {"scenes":[{"id":"场01","heading":"渡口 黄昏","action":"学徒提着蛋糕赶来。"}]})
    aid = wb.open_stage(eid, "stress")["artifactId"]
    def artifact():
        return next(a for a in wb.episode_view(eid)["artifacts"] if a["id"] == aid)
    task = wb.create_task(eid, "stress", "请讨论当前稿", workspace_revision=artifact()["revision"], discussion_only=True)
    wb.execute_run(task["runId"])
    assert any(m["role"] == "assistant" and m["kind"] == "text" for m in artifact()["tasks"][0]["messages"])
    follow = wb.post_message(task["taskId"], "重新复述", "stress-follow", workspace_revision=artifact()["revision"], discussion_only=True)
    manifest = wb.run_manifest(follow["runId"])["manifest"]
    assert {"conversation","stage_status"}.isdisjoint(i["role"] for i in manifest["inputs"])
    assert any(i["role"] == "stage_contract" and i["name"] == "stress.txt" for i in manifest["inputs"])
    v = wb.save_manual(aid,None,dict(retelling=" ",scope="剧本",diagnosis="核对",issues=[],options=[]),"empty-retelling")
    assert not wb.adoption_gate(v["versionId"])["adoptable"]
    normal, _, normal_id = episode(tmp_path / "normal")
    assert "stress" not in normal.episode_view(normal_id)["skippedStages"]


def test_review_source_blocks_and_delivery_becomes_stale(tmp_path):
    from helpers import Env
    from workbench.executors.fake import review
    env = Env(tmp_path)
    wb = env.wb
    case = env.full_episode(segments={"G01":[8000]})
    eid, vid, did = case["eid"], case["prompts"], case["delivery"]["deliveryId"]
    script = env.art(eid, "script")
    new = dict(script["versions"][-1]["content"], notes="改动本集的一句说明")
    env.save_adopt(eid, "script", new, "revised-script")
    assert wb.delivery_view(did)["state"] == "needs_recheck"
    for operation in (lambda:wb.package_preview(did, ["G01"]), lambda:wb.package(did, ["G01"], "stale-package")):
        with pytest.raises(Rejected, match="重新核验"):
            operation()
    wb.save_step_config(eid, "prompts", keep="场末保留停顿")
    env.claude.script.append(lambda req:review(req, "return", [dict(category="source", reason="道具与原句冲突")]))
    env.drain(wb.start_prompt_review(vid, "G01", "comparison", "source-review")["runId"])
    result = wb.prompt_review_status(vid)["G01"]["comparison"]
    assert result["state"] == "blocked"
    issue, = result["issues"]
    assert issue["blocking"]
    with pytest.raises(Rejected, match="不能放行"):
        wb.waive_segment_issue(issue["id"], "保留", "waive-source")


def test_stress_unspecified_spec_produces_adoptable_diagnosis(tmp_path):
    wb, _, eid = episode(tmp_path, locked=True)
    reason = wb.episode_view(eid)["skippedStages"]["stress"]
    save_adopt(wb, eid, "script", {"scenes":[{"id":"场01","heading":"渡口 黄昏","action":"学徒提着蛋糕赶来。"}]})
    with pytest.raises(Rejected, match="已跳过"):
        wb.create_task(eid, "stress", "审查本集剧本")
    wb.set_optional_stage(eid, "stress", False, expected_reason=reason)
    task = wb.create_task(eid, "stress", "审查本集剧本")
    wb.execute_run(task["runId"])
    artifact = next(a for a in wb.episode_view(eid)["artifacts"] if a["stage"] == "stress")
    version = artifact["versions"][-1]
    content = version["content"]
    assert content["retelling"] and "待导演确认" in content["diagnosis"] and "全片时长" in content["diagnosis"]
    assert not content["issues"] and not content["options"]
    assert wb.adoption_gate(version["id"])["adoptable"]
    assert artifact["tasks"][-1]["used_rounds"] == 0


def test_grouped_labels_and_joint_palette_change(tmp_path):
    from helpers import Env
    env = Env(tmp_path)
    case = env.full_episode(segments={"G01":[8000]})
    wb, eid, did = env.wb, case["eid"], case["delivery"]["deliveryId"]
    labels = dict(wb.episode_view(eid)["workflow"]["stages"])
    assert labels["acting"] == "08 表演" and labels["plan"] == "09 镜头" and labels["prompts"] == "09 提示词"
    assert "九阶段成果/09 镜头.json" in wb.delivery_view(did)["files"]
    assert wb.verify_delivery(did)["ok"]
    cid = wb.add_palette("暮色", "蓝色侧光", ["#506070"])["id"]
    wb.set_episode_palettes(eid,"assets","custom",[cid],together=True)
    assert wb.episode_palettes(eid,"assets")["cards"] == wb.episode_palettes(eid,"prompts")["cards"]
    assert wb.delivery_view(did)["state"] == "needs_recheck"
    assert any("色卡" in p["problem"] for p in wb.delivery_problems(eid)[0])
