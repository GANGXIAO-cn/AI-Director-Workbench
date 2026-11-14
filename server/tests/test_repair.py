"""User-visible regressions from the 2026-11-08 production audit."""
import pytest

from helpers import Env
from workbench.errors import Rejected


def test_retry_uses_original_scope_images_and_closure(tmp_path):
    from workbench.executors.fake import quota_exhausted
    env = Env(tmp_path)
    wb = env.wb
    ep = env.full_episode(segments={"G01": [8000]})
    for purpose in ("cold_read", "comparison", "proposal"):
        env.claude.script = [quota_exhausted]
        if purpose == "proposal":
            run = wb.start_closure(ep["delivery"]["deliveryId"], "close", provider="claude")
        else:
            run = wb.start_prompt_review(ep["prompts"], "G01", purpose, "fail-" + purpose)
            env.drain(run["runId"])
        old = wb.run_manifest(run["runId"])["manifest"]
        replay = wb.retry_run(run["runId"], provider="codex", command_id="retry-" + purpose)
        new = wb.run_manifest(replay["runId"])["manifest"]
        assert {k:v for k,v in old.items() if k not in {"runId", "provider"}} == {
            k:v for k,v in new.items() if k not in {"runId", "provider"}}
        env.drain(replay["runId"])
        assert env.codex.calls[-1].kind == ("proposal" if purpose == "proposal" else "review")
        if purpose == "proposal":
            closure = wb.closure_view(run["closureId"])
            assert closure["state"] == "open" and closure["items"] and closure["run_id"] == replay["runId"]
        else:
            assert len(env.codex.calls[-1].images) == 1
            row = wb.prompt_review_status(ep["prompts"])["G01"]["coldRead" if purpose == "cold_read" else "comparison"]
            assert row["runId"] == replay["runId"] and row["state"] == "passed"


def test_script_budget_handles_split_scenes_missing_quotes_and_trial_scope(tmp_path):
    import json
    from pathlib import Path
    from workbench import timeline
    env = Env(tmp_path)
    ep = env.full_episode(segments={"G01": [3000, 4000], "G02": [6000], "G03": [8000]})
    ids = {s:env.art(ep["eid"], s)["adopted_version_id"] for s in ("script", "plan")}
    report = env.wb.script_plan_budget(ep["eid"], ids["script"], ids["plan"])
    assert report["total_ms"] == 21000 and report["scenesOutsideSelection"] == ["场01"]
    package = env.wb.deliver(ep["eid"], "trial", "one-segment", ["G02"])
    budget = json.loads((Path(package["path"]) / "分段与时长预算.json").read_text())
    assert budget["segments"] == report["segments"][1:2] and budget["total_ms"] == 6000
    assert budget["scriptVersion"] == ids["script"] and budget["planVersion"] == ids["plan"]
    plan = ep["plan"]
    script = {"scenes":[{"id":"场01", "action":"开门"}, {"id":"场02", "action":"关门"}]}
    for shot, origin in zip(plan["shots"], [dict(scene="场01",line="开门"), dict(scene="场02",line="关门"),
                                             dict(scene="场02",line="开门"), None]):
        shot["origin"] = origin
    report = timeline.script_budget(script, plan, timeline.layout(plan)["segments"])
    assert [(g["scene"],g["duration_ms"]) for g in report["segments"][0]["scenes"]] == [("场01",3000),("场02",4000)]
    assert report["segments"][1]["scenes"][0]["unverifiedQuotes"] == ["S03"]
    assert report["segments"][2]["scenes"][0]["known"] is False and not report["scenesOutsideSelection"]


def test_script_reads_review_used_for_adoption(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    version = env.adopt_story(eid)
    original_review = wb.adoption_gate(version)["review"]["id"]
    # A later review of the same text cannot retroactively replace the adoption receipt.
    env.drain(wb.submit_review(version, "review-again")["runId"])
    later_review = wb.adoption_gate(version)["review"]["id"]
    assert original_review != later_review
    run = wb.create_task(eid, "script", "展开故事")["runId"]
    entries = wb.store.manifest_texts(wb.run_manifest(run)["manifest"])
    upstream = next(e["text"] for e in entries if e["role"] == "adopted_story")
    assert version in upstream and original_review in upstream and later_review not in upstream
    assert "审查原结论：pass" in upstream
    script = env.script(eid)
    run = wb.create_task(eid, "plan", "逐镜展开")["runId"]
    entries = wb.store.manifest_texts(wb.run_manifest(run)["manifest"])
    upstream = next(e["text"] for e in entries if e["role"] == "adopted_script")
    assert script in upstream and "此次采用记录没有关联独审" in upstream


def test_script_sources_keep_the_story_adopted_at_queue_time(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    env.adopt_story(eid)

    def revise_story(text):
        art = env.art(eid, "story")
        base = art["adopted_version_id"]
        content = dict(art["versions"][-1]["content"], synopsis=text)
        vid = wb.save_manual(art["id"], base, content, "save-" + text)["versionId"]
        env.drain(wb.submit_review(vid, "review-" + text)["runId"])
        wb.adopt(art["id"], vid, base, "adopt-" + text)
        return vid

    original = "值班员把蓝票放入抽屉。"
    revised = "值班员把红票退给乘客。"
    r02 = revise_story(original)
    run = wb.create_task(eid, "script", "起草分场剧本")["runId"]
    revise_story(revised)
    env.drain(run)
    prompt = env.codex.calls[-1].prompt
    assert original in prompt and revised not in prompt
    assert all(word not in prompt.casefold() for word in ("puggy", "milo", "leo", "女主人"))
    script = env.art(eid, "script")
    draft = script["versions"][-1]
    manual = wb.save_manual(script["id"], draft["id"], dict(draft["content"], notes="导演补记"), "script-note")["versionId"]
    for vid in (draft["id"], manual):
        source, = [i for i in wb.version_sources(vid)["inputs"] if i["role"] == "adopted_story"]
        assert source["storyVersion"] == {"id": r02, "display_no": 2}
        assert source["sourceRuns"] == [run]
        assert original in source["text"] and revised not in source["text"]


def test_new_episode_is_created_only_when_kickoff_succeeds(tmp_path):
    wb = Env(tmp_path).wb
    pid = wb.create_project("_template","短片")["projectId"]
    options = dict(episode_id=None, project_id=pid, display_code="E01", source_type="idea",
                   idea_text="一只狗把倒影当成客人", executor_plan={"create":{"provider":"codex"},"review":{"provider":"claude"}})
    preview = wb.kickoff_preview(project_id=pid, display_code="E01")
    assert preview["episode"]["display_code"] == "E01" and not wb.project_view(pid)["episodes"]
    with pytest.raises(Rejected, match="整数秒"):
        wb.start_episode(**options,spec_overrides={"segmentMaxSeconds":20})
    assert not wb.project_view(pid)["episodes"]
    result = wb.start_episode(**options, command_id="confirm")
    assert wb.start_episode(**options,command_id="confirm") == result
    assert [e["id"] for e in wb.project_view(pid)["episodes"]] == [result["episodeId"]]


def test_duration_rules_check_selected_segments_and_full_length(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    env.save_adopt(eid,"plan",env.plan86({"G01":[10000],"G02":[11000]}))
    wb.episode_spec_change(eid,"segmentMaxSeconds","10","set-cap",confirm=True)
    wb.episode_spec_change(eid,"targetDuration","over_60","set-target",confirm=True)
    problems,_=wb.delivery_problems(eid,"full")
    assert any(p["segment"]=="G02" and "10 秒上限" in p["problem"] for p in problems)
    assert any("严格超过 60" in p["problem"] for p in problems)
    scoped,_=wb.delivery_problems(eid,"trial",["G01"])
    assert not any("上限" in p["problem"] or "严格超过 60" in p["problem"] for p in scoped)
    assert wb.episode_view(eid)["productionStatus"] == dict(materialsConfirmed=False,space=False,cast=False,assets=False,review=False,package=False)


def test_historical_pack_cannot_restore_parallel_workflow(tmp_path):
    env = Env(tmp_path)
    pid,eid = env.new_episode()
    with pytest.raises(Rejected):
        env.wb.adopt_pack(pid, "short-film@v1", "switch-v1", expected_ref="unified-film@draft")
    task=env.wb.create_task(eid,"story","按本集开工时的方法起草")
    header=env.wb.run_manifest(task["runId"])["manifest"]
    assert header["methodPack"]["id"]=="unified-film@draft"
    _, newer = env.new_episode(project_id=pid, code="E02")
    assert env.wb.stage_methods(newer, "script")["ref"] == "unified-film@draft"
    assert env.wb.stage_methods(newer, "script")["sections"]


def test_versioned_source_sync_and_old_episode_replay(tmp_path, monkeypatch):
    """A change in one pinned upstream section must affect just its consuming stages."""
    import json
    from pathlib import Path
    from workbench import methodpack
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/"validation/workflows"))
    from method_sync import prepare, TITLE, EXTRA, PINS
    if not all((Path.home()/".codex/skills"/repo/".git").exists() for repo in PINS):
        pytest.skip("fixed skill repositories unavailable")
    sandbox, report = prepare(tmp_path)
    [changed] = report["changes"]
    assert set(changed["sections"]) == {TITLE,"设备视点、观看窗口、声音与剪点"} and changed["stages"] == ["plan","script"]
    movement = {r["section"]:r for r in changed["ranges"]}
    assert movement[TITLE]["original"] == "22-34" and movement[TITLE]["lines"] == "24-37"
    assert movement["设备视点、观看窗口、声音与剪点"]["changed"]
    monkeypatch.setattr(methodpack,"METHODS",sandbox)
    env = Env(tmp_path)
    pid,old_eid = env.new_episode()
    old_task = env.wb.create_task(old_eid,"story","保留原版输入")["runId"]
    saved = env.wb.run_methods(old_task)
    delta = env.wb.compare_packs("short-film@v7","short-film@v8",pid)
    assert set(delta["stages"])=={"plan","script"}
    assert {s["key"] for s in delta["sections"] if s["kind"]=="edited"} == {TITLE,"设备视点、观看窗口、声音与剪点"}
    with pytest.raises(Rejected):
        env.wb.adopt_pack(pid, "short-film@v8", "upgrade", expected_ref="unified-film@draft")
    assert env.wb.run_methods(old_task)==saved
    _,new_eid = env.new_episode(project_id=pid, code="E02")
    for eid, is_new in [(old_eid, False), (new_eid, True)]:
        env.adopt_story(eid)
        task = env.wb.create_task(eid,"script","展开剧情阶段")
        manifest = env.wb.run_manifest(task["runId"])["manifest"]
        assert manifest["methodPack"]["id"] == "unified-film@draft"
        texts = env.wb.store.manifest_texts(manifest)
        assert not any(EXTRA in entry["text"] for entry in texts)
        assert any("film-screenplay" in entry.get("path", "")
                   for entry in manifest["methodPack"]["files"].values())
        assert env.wb.run_methods(task["runId"])["ref"]==manifest["methodPack"]["id"]


def test_publish_custom_fields_and_historical_snapshot(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    result = env.full_episode(deliver=False)
    pid, eid = result["pid"], result["eid"]
    original = wb.compose_publish(eid)
    assert original["fields"]["suno_style"]
    wb.set_publish_settings(pid, dict(additional_fields=[dict(key="short_caption",label="短说明",required=True)]))
    assert wb.compose_publish(eid) == original
    artifact = env.art(eid, "publish")
    base = artifact["adopted_version_id"]
    content = dict(title="After hours", body="A night shift.\nA bell rings.",tags=["night"])
    vid = wb.save_manual(artifact["id"],base,content,"missing-caption")["versionId"]
    assert any("短说明" in err for err in wb.adoption_gate(vid)["formatErrors"])
    content["extraFields"] = {"short_caption":"One bell."}
    vid = wb.save_manual(artifact["id"],vid,content,"add-caption")["versionId"]
    pub = wb.compose_publish(eid,vid)
    assert pub["fields"]==dict(title=content["title"],body=content["body"],tags="#night",short_caption="One bell.")
    wb.adopt(artifact["id"],vid,base,"new-publish")
    assert wb.compose_publish(eid)["fieldLabels"]["short_caption"]=="短说明"
    other = wb.create_project("_template","无附加字段")["projectId"]
    assert wb.publish_settings(other)["additional_fields"]==[]


def test_custom_palette_scope_and_frozen_provenance(tmp_path):
    from base64 import b64encode
    from workbench.images import png_bytes
    env = Env(tmp_path)
    wb = env.wb
    pid,eid = env.new_episode()
    pixels = png_bytes(4,4,(91,70,41))
    card = wb.add_palette("雨夜玻璃","玻璃上反光与暗部","#253649 #C6A465".split(),
                          "单侧光","随人移动","自摄",b64encode(pixels).decode())
    cid = card["id"]
    detail = wb.palette_detail(cid)
    assert detail["sources"]=="自摄" and len(detail["stills"])==1
    assert wb.palette_image(cid,detail["cover"])==pixels
    assert wb.browse_palettes(query="雨夜玻璃")["rows"][0]["id"]==cid
    assert not wb.episode_palettes(eid,"prompts")["cards"]
    wb.save_palettes(pid,[cid])
    wb.set_episode_palettes(eid,"assets","off")
    assert not wb.episode_palettes(eid,"assets")["cards"]
    assert wb.episode_palettes(eid,"prompts")["cards"][0]["id"]==cid
    wb.set_episode_palettes(eid,"assets","custom",[cid])
    wb.save_palettes(pid,[])
    assert not wb.episode_palettes(eid,"prompts")["cards"]
    assert wb.episode_palettes(eid,"assets")["cards"][0]["id"]==cid


def test_unified_core_cannot_be_disabled_and_run_methods_stay_frozen(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    before = wb.create_task(eid, "story", "写出故事")
    frozen = wb.run_methods(before["runId"])
    with pytest.raises(Rejected):
        wb.set_stage_methods(eid, "story", ["SKILL.md"])
    with pytest.raises(Rejected):
        wb.set_stage_methods(eid, "story", ["可选的故事开发辅助.md"])
    wb.set_stage_methods(eid, "story", [])
    assert wb.run_methods(before["runId"]) == frozen


def test_restoring_historical_publish_requires_adoption_and_keeps_settings(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    result = env.full_episode(deliver=False)
    eid = result["eid"]
    art = env.art(eid, "publish")
    base = art["adopted_version_id"]
    old = wb.compose_publish(eid)
    wb.set_publish_settings(result["pid"], dict(additional_fields=[]))
    v, _ = env.save_adopt(eid, "publish", dict(title="New copy", body="The bell rings.", tags=[]), "rewrite")
    restored = wb.restore_version(base, v, "restore-old")["versionId"]
    assert restored not in (base, v)
    assert wb.compose_publish(eid)["fields"]["title"] == "New copy"
    assert wb.compose_publish(eid, restored) == old
    wb.adopt(art["id"], restored, v, "use-restored")
    assert wb.compose_publish(eid) == old


def test_local_catalog_and_atomic_followup_model(tmp_path, monkeypatch):
    import json
    env = Env(tmp_path)
    wb = env.wb
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cache = dict(identity="private", models=[dict(slug="extra-model", display_name="Native", visibility="list",
                 context_window=12000, supported_reasoning_levels=[dict(effort="high")]),
                 dict(slug="hidden-model", visibility="hide")])
    (tmp_path / "models_cache.json").write_text(json.dumps(cache))
    public = wb.model_catalog()["text"]["codex"]
    assert [m["id"] for m in public["models"]] == [None, "extra-model"]
    assert "private" not in str(public) and public["models"][1]["context_window"] == 12000
    _, eid = env.new_episode()
    task = wb.create_task(eid, "story", "写一稿")
    selection = dict(provider="codex", model="extra-model", effort="high")
    edited = wb.post_message(task["taskId"], "续写", "update", create_executor=selection)
    assert wb.post_message(task["taskId"], "续写", "update", create_executor=selection) == edited
    m = wb.run_manifest(edited["runId"])["manifest"]
    assert (m["provider"], m["requestedModel"], m["requestedEffort"]) == ("codex", "extra-model", "high")
    assert wb.run_manifest(task["runId"])["manifest"]["requestedModel"] is None
    with pytest.raises(Rejected):
        wb.post_message(task["taskId"], "改写", "invalid", create_executor={**selection, "effort":"ultra"})


def test_prompt_author_reads_only_bound_originals_when_selected(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    image = env.image(pid, eid)
    env.image(pid, eid, name="未选图")
    plan = env.plan86({"G01":[8000]})
    plan["shots"][0]["startAssetId"] = image["versionId"]
    env.save_adopt(eid, "plan", plan)
    card = wb.add_palette(name="暮色",summary="冷色侧光",colors=["#506070"],source="本地测试样本")
    wb.save_palettes(pid,[card["id"]])
    for include in [False, True]:
        task = wb.create_task(eid, "prompts", "按段起草", prompt_images=include)
        frozen = wb.run_manifest(task["runId"])["manifest"]
        binaries = [i["sha256"] for i in frozen["inputs"] if i.get("binary")]
        assert binaries == ([image["sha256"]] if include else [])
        assert any(i["role"] == "prompt_scaffold" for i in frozen["inputs"])
        if include:
            from workbench.executors.fake import directed

            def notes_first(request):
                result = directed(request, "codex")
                target = result["changes"][0]["targetId"]
                result["changes"].insert(0, dict(targetId=target, field="decisionNotes", value="[]"))
                return result

            env.codex.script.append(notes_first)
        env.drain(task["runId"])
        version=env.art(eid,"prompts")["versions"][-1]
        assert version["content"]["segments"]["G01"]["refs"][0]["assetVersionId"] == image["versionId"]
        trace=wb.segment_palette_trace(version["id"],"G01")
        assert trace["direct"] and trace["cards"][0]["id"]==card["id"]
        assert "assets/color_palettes/palette.json" in trace["cards"][0]["fileHashes"]
    wb.set_episode_palettes(eid,"prompts","off")
    edited=wb.save_manual(task["artifactId"],version["id"],version["content"],"edit-with-reference")
    inherited=wb.segment_palette_trace(edited["versionId"],"G01")
    assert not inherited["direct"] and inherited["cards"]==trace["cards"]
    task = wb.create_task(eid, "prompts", "看图", executor={"create":{"provider":"antigravity"}}, prompt_images=True)
    entries = wb.store.manifest_texts(wb.run_manifest(task["runId"])["manifest"])
    assert not any(i.get("binary") for i in entries)
    assert any("本次未提供原图" in i["text"] for i in entries)


def test_project_library_scope_pins_and_product_scene_evidence(tmp_path):
    import json
    from io import BytesIO
    from PIL import Image
    env = Env(tmp_path)
    wb = env.wb
    plain, unbranded = env.new_episode()
    pid, eid = env.new_episode(config="puggy")
    library = wb.project_resources(pid, eid)
    assert len(library["products"]) == 16 and len(library["sections"]) == 33
    assert wb.project_resources(plain, unbranded) == {"products":[], "sections":[]}
    key = "user-priority-videos-51"
    with pytest.raises(Rejected):
        wb.set_project_sections(unbranded, "story", [key])
    selected = wb.set_project_sections(eid, "story", [key])
    task = wb.create_task(eid, "story", "改编一个动作")
    wb.set_project_sections(eid, "story", [])
    frozen = wb.run_manifest(task["runId"])["manifest"]
    entry = next(item for item in frozen["inputs"] if item["role"] == "citation")
    assert json.loads(wb.store.read_blob(entry["sha256"])) == selected[0]
    raw = wb.project_product_image(pid, "15", eid)
    ref = wb.use_project_product(eid, "15", "add-product")
    assert wb.store.read_blob(ref["originalSha256"]) == raw
    assert wb.use_project_product(eid, "15", "select-again") == ref
    with Image.open(BytesIO(raw)) as webp, Image.open(BytesIO(wb.asset_bytes(ref["versionId"]))) as png:
        assert webp.convert("RGBA").tobytes() == png.convert("RGBA").tobytes()
    env.adopt_story(eid); env.script(eid)
    plan = env.plan86({"G01":[8000]})
    env.save_adopt(eid, "plan", plan)
    scene = env.image(pid, eid, name="测试场面", category="state")
    shot = plan["shots"][0]["id"]
    with pytest.raises(Rejected, match="本集的场面图"):
        wb.save_product_placement(eid, "15", shot, "贴近椅背", ref["versionId"], "目录主图")
    wb.save_product_placement(eid, "15", shot, "椅背靠垫", scene["versionId"], "自动化测试的观察记录")
    assert wb.product_placements(eid)["verified"] == 0
    wb.mark_image(eid, scene["versionId"], "required", [shot])
    assert "未引用" in wb.product_placements(eid)["records"][0]["issue"]
    env.save_adopt(eid,"prompts",env.prompts_for(plan,scene["versionId"]))
    assert wb.product_placements(eid)["verified"] == 1
    wb.mark_image(eid, scene["versionId"], "required", enabled=False)
    assert any("商品入景覆盖" in p["problem"] for p in wb.delivery_problems(eid)[0])
    assert not any("商品入景覆盖" in p["problem"] for p in wb.delivery_problems(eid,"trial",["G01"])[0])
