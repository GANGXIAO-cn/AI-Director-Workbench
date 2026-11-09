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
