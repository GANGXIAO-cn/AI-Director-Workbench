import json

import pytest

from helpers import Env
from workbench.errors import Rejected


@pytest.fixture
def plan(tmp_path):
    env = Env(tmp_path)
    _, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    reason = env.wb.episode_view(eid)["skippedStages"]["acting"]
    env.wb.set_optional_stage(eid, "acting", False, expected_reason=reason)
    env.wb.open_stage(eid, "acting")
    env.save_adopt(eid, "acting", {"scenes": [dict(sceneId="场01", objective="检查", obstacle="抽屉卡住",
                                                   strategy="拉开", beats=["推回"])]})
    run = env.wb.create_task(eid, "plan", "完成镜头方案")["runId"]
    env.drain(run)
    art = env.art(eid, "plan")
    return env.wb, art, art["versions"][-1], run


def test_adopted_version_inputs(plan):
    wb, _, v, run = plan
    result = wb.version_inputs(v["id"])
    items = next(g["items"] for g in result["groups"] if g["key"] == "upstream")
    sources = {i["role"]: i["upstreamVersion"]["display_no"] for i in items if i["role"].startswith("adopted_")}
    assert sources == dict(adopted_script=1, adopted_asset_design=1, adopted_acting=1, adopted_characters=1)
    assert result["runs"] == [run]
    assert all("text" not in i for g in result["groups"] for i in g["items"])


def test_manual_inputs(plan):
    wb, art, v, run = plan
    manual = wb.save_manual(art["id"], v["id"], v["content"], "manual")["versionId"]
    result = wb.version_inputs(manual)
    assert result["derivedFrom"] == dict(id=v["id"], display_no=v["display_no"])
    assert result["runs"] == [run]


def test_input_membership(plan):
    wb, _, _, run = plan
    with pytest.raises(Rejected, match="不在"):
        wb.input_text(run, wb.store.put_blob(b"unrelated"))


def test_manifest_fingerprint(plan):
    wb, _, v, run = plan
    path = wb.store.run_dir(run) / "input-manifest.json"
    path.write_text(json.dumps(dict(wb.run_manifest(run)["manifest"], inputs=[])))
    with pytest.raises(Rejected, match="创作输入清单指纹不符"):
        wb.version_inputs(v["id"])
