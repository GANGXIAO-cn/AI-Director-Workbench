import pytest

from helpers import Env
from workbench import enhancements
from workbench.errors import Conflict, Rejected


def test_catalog_and_scoped_method_snapshots(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    _, other = env.new_episode(config="puggy")
    methods = wb.enhancement_catalog()
    codes = [m["id"] for m in methods]
    assert len(set(codes)) == len(codes) == 71
    all_files = enhancements.entries(codes)
    assert len(all_files) == 50
    for item in all_files:
        assert all(word not in item["text"].casefold() for word in ("puggy", "milo", "fourthwall"))
    for invalid in (["../bad"], ["00-A", "00-A"], "00-A"):
        with pytest.raises(Rejected):
            wb.save_step_config(eid, "story", enhancements=invalid)
    chosen = ["00-A", "00-B"]
    first = wb.save_step_config(eid, "story", enhancements=chosen, expected_revision=0)
    with pytest.raises(Conflict):
        wb.save_step_config(eid, "story", enhancements=[], expected_revision=0)
    run = wb.create_task(eid, "story", "应用所选镜头方法")["runId"]
    env.drain(run)
    def snapshot(rid):
        return [item for item in wb.run_methods(rid)["sections"] if (item["source"] or {}).get("enhancementIds")]
    parts = snapshot(run)
    assert len(parts) == 3
    assert any(item["source"]["enhancementIds"] == chosen for item in parts)
    assert all(item["text"] in env.codex.calls[-1].prompt for item in parts)
    assert "PUGGY" not in env.codex.calls[-1].prompt
    wb.save_step_config(eid, "story", enhancements=[], expected_revision=first["revision"])
    assert wb.step_history(eid, "story")[1]["enhancements"] == chosen
    for ep in (eid, other):
        later = wb.create_task(ep, "story", "单独起草")["runId"]
        assert not snapshot(later)
    assert snapshot(run) == parts
    assert wb.step_config(eid, "script").get("enhancements", []) == []


def test_image_selection_freezes_in_batch_before_authorization(tmp_path):
    from unittest.mock import patch
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    wb.save_step_config(eid, "assets", enhancements=["07-A"])
    args = dict(episode_id=eid, provider="fake-image", operation="generate", size="64x64", budget_cents=4,
                items=[dict(prompt="人物四格", inputs=[], target=dict(name="身份卡", category="identity"))])
    first = wb.create_batch(**args)
    b = wb.batch_view(first["batchId"])
    expected = enhancements.entries(["07-A"])
    assert b["spec"]["enhancements"] == [e["methodSource"] for e in expected]
    assert all(e["text"] in b["spec"]["items"][0]["prompt"] for e in expected)
    wb.save_step_config(eid, "assets", enhancements=[])
    second = wb.create_batch(**args)
    assert wb.batch_view(second["batchId"])["spec"]["items"][0]["prompt"] == "人物四格"
    wb.authorize_batch(first["batchId"], b["summary_hash"], "test-authorization")
    with patch.object(env.images, "submit", wraps=env.images.submit) as submitted:
        wb.execute_batch(first["batchId"])
        assert submitted.call_args.args[0]["prompt"] == b["spec"]["items"][0]["prompt"]


@pytest.mark.parametrize("profile", ["_template", "puggy"])
def test_writing_stages_and_comparison_use_scoped_enhancements(tmp_path, profile):
    from workbench.executors.fake import quota_exhausted

    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode(config=profile)
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01":[8000]})
    env.save_adopt(eid, "plan", plan)
    asset = env.image(pid, eid)
    prompt_version, _ = env.save_adopt(eid, "prompts", env.prompts_for(plan, asset["versionId"]))
    choices = {"story":"01-A", "script":"04-B", "plan":"00-C", "prompts":"09-Q", "publish":"08-C"}
    for stage, code in choices.items():
        marker = "本步固定要求 " + stage
        wb.save_step_config(eid, stage, goal=marker, enhancements=[code])
        rid = wb.create_task(eid, stage, "按本步要求检查输入")["runId"]
        env.codex.script = [quota_exhausted]
        env.drain(rid)
        prompt = env.codex.calls[-1].prompt
        sections = [s for s in wb.run_methods(rid)["sections"] if (s["source"] or {}).get("enhancementIds")]
        assert sections and {tuple(s["source"]["enhancementIds"]) for s in sections} == {(code,)}
        assert all(s["text"] in prompt for s in sections)
        assert marker in prompt and enhancements.SCOPE in prompt
        if profile == "_template":
            assert all(term not in prompt.casefold() for term in ("puggy", "milo", "fourthwall"))
        if stage == "prompts":
            assert "H3" in prompt

    selected = enhancements.entries(["09-Q"])
    for name in ("cold_read", "comparison"):
        review = wb.start_prompt_review(prompt_version, "G01", name, "check-"+name)
        env.drain(review["runId"])
        prompt = env.claude.calls[-1].prompt
        assert all((s["text"] in prompt) == (name == "comparison") for s in selected)
        assert ("本步固定要求 prompts" in prompt) == (name == "comparison")
        if profile == "_template":
            assert all(term not in prompt.casefold() for term in ("puggy", "milo", "fourthwall"))
    assert wb.prompt_review_status(prompt_version)["G01"]["ok"]
    wb.save_step_config(eid, "prompts", goal="本步固定要求 prompts", enhancements=[])
    status = wb.prompt_review_status(prompt_version)["G01"]
    assert status["coldRead"]["state"] == "passed" and not status["ok"]
