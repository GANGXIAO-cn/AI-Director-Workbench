"""The methods frozen into runs, including the adopted-screenplay exception."""
from hashlib import sha256

from helpers import Env
from workbench import unifiedmethods
from test_unified_workflow import episode

LOCAL_METHODS = ("product_placement","topic", "story", "characters", "world", "treatment", "script", "stress", "story_review",
                 "asset_design", "acting", "plan", "prompts", "group", "cold_read", "comparison", "image_assess")


def test_local_story_methods_have_audited_sources():
    pack = unifiedmethods.load()
    for stage in LOCAL_METHODS:
        path = unifiedmethods.LOCAL / (stage + ".md")
        key = stage + "_contract"
        assert key in pack["stages"][stage] and pack["files"][key]["sha256"] == sha256(path.read_bytes()).hexdigest()
        assert f"文件为 {path.stat().st_size} 字节" in (unifiedmethods.LOCAL / "SOURCES.md").read_text()
    for path in unifiedmethods.LOCAL.glob("*.md"):
        if path.name == "SOURCES.md":
            continue
        assert path.stat().st_size <= (14336 if path.stem in ("plan", "prompts") else 12288 if path.stem == "asset_design" else 8192)
        assert not any(word in path.read_text() for word in ("MILO","LEO","LUCI","PUGGY","女主人","猫","橘","爪","Fourthwall","Suno","笑","喜剧","本领"))


def test_creation_and_review_freeze_story_methods(tmp_path):
    env = Env(tmp_path)
    pid, eid = env.new_episode()
    wb = env.wb

    def check(run, stage):
        inputs = wb.run_manifest(run)["manifest"]["inputs"]
        item, = [i for i in inputs if i["role"] == "method" and i["name"] == stage + ".md"]
        assert item["sha256"] == sha256((unifiedmethods.LOCAL / item["name"]).read_bytes()).hexdigest()

    wb.create_group(pid, "站台", [{"episodeId":eid}], "同一班末班车")
    for stage in ("story", "characters", "world", "treatment", "script", "stress"):
        task = wb.create_task(eid, stage, "按已采用的上游成果完成本阶段")
        check(task["runId"], stage)
        if stage == "story":
            check(task["runId"], "group")
        env.drain(task["runId"])
        art = env.art(eid, stage)
        vid = art["versions"][-1]["id"]
        if stage == "story":
            run = wb.submit_review(vid, "check-review")["runId"]
            check(run, "story_review")
            env.drain(run)
        wb.adopt(art["id"], vid, None, "accept-" + stage)

    locked, _, eid = episode(tmp_path / "locked", locked=True)
    task = locked.create_task(eid, "script", "切分剧本")
    inputs = locked.run_manifest(task["runId"])["manifest"]["inputs"]
    assert not any(i["name"] == "script.md" and i["role"] == "method" for i in inputs)


def test_post_plan_assets_and_solo_acting(tmp_path):
    from workbench.workflow import CONTRACTS, check
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01":[8000]}, {"G01":"grid"})
    plan_id, _ = env.save_adopt(eid, "plan", plan)
    assert wb.episode_view(eid)["pendingShotImages"] == ["G01 九宫格"]
    art = env.art(eid, "asset_design")
    content = art["versions"][-1]["content"]
    state = dict(id="wet", kind="state", roleId="afu", stateId="wet", description="雨衣湿透", imagePrompt="袖口滴水")
    env.save_adopt(eid, "asset_design", dict(content,assets=content["assets"]+[state]), "append-post-plan")
    task = wb.create_task(eid, "asset_design", "补做状态图")
    entries = wb.run_manifest(task["runId"])["manifest"]["inputs"]
    assert any(i["role"]=="asset_inventory" for i in entries)
    assert not any(i["role"]=="adopted_plan" for i in entries)
    batch = wb.create_batch(eid, "fake-image", "generate", [
        dict(prompt="湿雨衣的站台", segments=["G01"], target=dict(name="雨中站台", category="grid", grid=True))
    ], "64x64", budget_cents=100)
    wb.authorize_batch(batch["batchId"], batch["summaryHash"], "approve-grid")
    wb.execute_batch(batch["batchId"])
    assert wb.episode_view(eid)["pendingShotImages"] == ["afu · wet 状态图待出或待对图确认"]
    assert not check("acting", {"scenes":[dict(sceneId="场01", objective="拉起鱼竿", obstacle="鱼线被石头卡住",
                                            strategy="换角度松线", beats=["收线","转腕"], partner="无")]})
    wb.set_optional_stage(eid, "acting", False, expected_reason="此夹具直接使用剧本已写出的可见行动")
    for stage in ("asset_design", "acting", "plan", "prompts"):
        rid = task["runId"] if stage == "asset_design" else wb.create_task(eid, stage, "检查本阶段的制作方法")["runId"]
        if stage == "acting":
            wb.set_optional_stage(eid, "acting", True, "本例镜头直接沿用剧本行动")
        items = wb.run_manifest(rid)["manifest"]["inputs"]
        method, = [i for i in items if i["role"] == "method" and i["name"] == stage + ".md"]
        assert method["sha256"] == sha256((unifiedmethods.LOCAL / method["name"]).read_bytes()).hexdigest()
    assert "identityRequired" in CONTRACTS["characters"]
    assert all(key in CONTRACTS["treatment"] for key in ("timePoint","elapsedTime","rolePositions","stateChanges"))


def test_project_method_sync_is_pinned_and_deduplicated(tmp_path):
    from workbench.methodpack import CONFIGS
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode(config="puggy")
    assert sum(not item["synced"] for item in wb.custom_methods(pid)["configured"]) == 5
    earlier = wb.create_task(eid, "story", "按已采用的上游成果完成本阶段")["runId"]
    env.drain(earlier)
    wb.sync_project_methods(pid, "initial-sync")
    assert all(item["synced"] for item in wb.custom_methods(pid)["configured"])
    stages = ("story", "script", "asset_design", "acting", "prompts")
    for stage in stages:
        info = wb.custom_methods(pid, stage, eid)
        assert not info["selected"] and info["projectSelected"]
        wb.adopt_project_stage_methods(pid, eid, stage, [], info["projectSelected"], "apply-" + stage)
    assert not any(i["role"] == "project_method" for i in wb.run_manifest(earlier)["manifest"]["inputs"])
    expected = {}
    for stage in stages:
        vid, = wb.custom_methods(pid, stage, eid)["selected"]
        expected[stage] = vid
        raw = (CONFIGS / "puggy-methods" / (stage + ".md")).read_bytes()
        assert wb.custom_method_text(pid, vid)["sha256"] == sha256(raw).hexdigest()
        assert len(raw) <= (14336 if stage == "prompts" else 12288 if stage == "asset_design" else 8192)
    wb.set_custom_methods(pid, "story", [], [expected["story"]])
    assert wb.custom_methods(pid, "story", eid)["selected"] == [expected["story"]]
    for command in ("sync-once", "sync-again"):
        result = wb.sync_project_methods(pid, command)
        assert {item["stage"]: item["versionId"] for item in result["files"]} == expected
    methods = wb.custom_methods(pid)["methods"]
    assert len(methods) == 5 and all(len(m["versions"]) == 1 for m in methods)
    blank, blank_eid = env.new_episode()
    assert not wb.custom_methods(blank)["configuredFiles"]
    for project, ep in ((pid, eid), (blank, blank_eid)):
        env.adopt_story(ep)
        env.script(ep)
        env.save_adopt(ep, "plan", env.plan86({"G01":[8000]}))
        env.image(project, ep)
        for stage in stages:
            skipped = wb.episode_view(ep)["skippedStages"].get(stage)
            if skipped:
                wb.set_optional_stage(ep, stage, False, expected_reason=skipped)
            rid = wb.create_task(ep, stage, "检查已采用的项目方法")["runId"]
            if skipped:
                wb.set_optional_stage(ep, stage, True, reason=skipped)
            frozen = wb.store.manifest_texts(wb.run_manifest(rid)["manifest"])
            items = [i for i in frozen if i["role"] == "project_method"]
            if project == pid:
                assert [i["text"] for i in items] == [wb.custom_method_text(pid, expected[stage])["text"]]
            else:
                assert not items and all("MILO" not in (i["text"] or "") for i in frozen)


def test_fixed_character_standard_reaches_early_stage_runs(tmp_path):
    env = Env(tmp_path)
    _, eid = env.new_episode(config="puggy")
    instruction = "按已采用的上游成果完成本阶段"
    for stage in ("story", "characters", "world", "treatment"):
        run = env.wb.create_task(eid, stage, instruction)["runId"]
        inputs = env.wb.run_manifest(run)["manifest"]["inputs"]
        standard, = [entry for entry in inputs if entry["role"] == "characterStandard"]
        assert standard["sha256"]
        env.drain(run)
        art = env.art(eid, stage)
        version = art["versions"][-1]["id"]
        if stage == "story":
            env.drain(env.wb.submit_review(version, "review-story")["runId"])
        env.wb.adopt(art["id"], version, None, "adopt-" + stage)


def test_H_image_assess_director_approved_method_and_source_bytes():
    path=unifiedmethods.LOCAL/'image_assess.md'
    raw=path.read_bytes();text=raw.decode()
    assert len(raw)==6399
    assert text.startswith('# 图片评估方法\n\n## 在工作台中的用法\n')
    assert 'accepted_image_issue' in text
    assert all(value in text for value in ('critical.type=identity','critical.type=anatomy','critical.type=usage','minor'))
    assert '荒诞前提' not in text
    assert '实际图片、输入顺序与TXT职责是否一致' not in text
    assert '镜头声音描述是否完整继承' not in text
    sources=(unifiedmethods.LOCAL/'SOURCES.md').read_text()
    assert '导演 2026-11-11 确认接入' in sources
    assert f'image_assess.md 文件为 {len(raw)} 字节' in sources
    pack=unifiedmethods.load()
    assert pack['files']['image_assess_contract']['sha256']==sha256(raw).hexdigest()
    assert pack['texts']['image_assess_contract']==text
