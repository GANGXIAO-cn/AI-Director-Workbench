"""实测问题回归：真实实测中发现的确定性缺陷。"""
import pytest
from workbench.storycheck import cast_records, check_story

BULLETS = """### 本集角色表

#### 1. MILO
- **角色ID**：milo
- **身份与作用**：主角
- **独立身份基准与实际数据**：
  - 尺寸与体型：普通家猫尺寸，明显肥胖
"""


def test_field_116_cast_error_states_exact_table_format():
    """实测 116：E08 第三稿把角色表写成分条列表；报错须说明表格格式，修正提示才能把模型带回表格。"""
    with pytest.raises(ValueError) as caught:
        cast_records(BULLETS)
    message = str(caught.value)
    for part in ("Markdown 表格", "分条列表", "「角色ID」「身份与作用」「独立身份基准」", "「需要」或「不需要」", "| milo |"):
        assert part in message
    assert any("Markdown 表格" in e for e in check_story(dict(cast=BULLETS)))
    table = "| 角色ID | 身份与作用 | 独立身份基准 | 识别特征 |\n|---|---|---|---|\n| milo | 主角 | 需要 | 黄色卫衣 |"
    assert cast_records(table) == {"milo": {"description": "主角", "identityRequired": True}}


def test_field_117_attention_lists_latest_blocked_and_waiting_runs(tmp_path):
    """实测 117：连续制作停下或等选择时，全局查询能列出该集，供侧栏、最近剧集和标签标题提示。"""
    from helpers import Env
    from workbench.db import connect
    env = Env(tmp_path)
    pid = env.wb.create_project('_template', '提示验收')['projectId']
    eids = [env.wb.create_episode(pid, code)['episodeId'] for code in ('E01', 'E02', 'E03')]
    with connect(env.wb.db_path) as db:
        rows = [('old', eids[0], 'cancelled', '旧原因', 1), ('new', eids[0], 'running', '', 2),
                ('b', eids[1], 'blocked', '本集角色表须含角色ID', 3), ('w', eids[2], 'waiting_choice', '请选择故事方向', 4)]
        for rid, eid, state, reason, stamp in rows:
            db.execute("INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,reason,created_at,updated_at) VALUES(?,?,'delivery',?,?,?,?,?)",
                       (rid, eid, state, '{"stage":"story","phase":"draft"}', reason, stamp, stamp))
        db.commit()
    items = env.wb.attention_episodes()
    assert [(i['displayCode'], i['state']) for i in items] == [('E03', 'waiting_choice'), ('E02', 'blocked')]
    assert items[1]['reason'] == '本集角色表须含角色ID' and items[1]['stage'] == 'story' and items[1]['projectName'] == '提示验收'


def _fixed_role_episode(tmp_path, preuse):
    from helpers import Env
    env = Env(tmp_path)
    pid, eid = env.new_episode(config="puggy")
    if preuse:
        env.wb.use_fixed_role(eid, "milo", "use-fixed")
    env.adopt_story(eid)
    env.script(eid)
    item = dict(id="char-milo", kind="character", title="MILO固定角色身份基准", roleId="milo", stateId="default", imagePrompt="",
                description="复用项目固定身份图", evidenceStatus="verified")
    env.save_adopt(eid, "asset_design", dict(strategy="lightweight", assets=[item]), "adopt-worksheet")
    return env, eid


def test_field_122_worksheet_adopts_registered_fixed_role_without_prior_use(tmp_path):
    """实测 122：开拍后没人手动点过“使用固定角色”，07 建表时也应直接采用项目登记的固定身份图。"""
    env, eid = _fixed_role_episode(tmp_path, preuse=False)
    row = env.wb.asset_worksheet(eid)["rows"][0]
    assert row["decision"] == "reuse" and row["adopted_version_id"]


def test_field_122_continuous_adopts_pending_fixed_role_instead_of_stopping(tmp_path):
    """实测 122：E08 固定角色条目停在“待决定”且无提示词，连续制作曾因“请填写出图提示词”停住；应直接采用登记原图。"""
    from workbench.db import transaction
    env, eid = _fixed_role_episode(tmp_path, preuse=False)
    with transaction(env.wb.db_path) as db:   # 复现修复前已建好的 E08 状态：待决定、未采用
        db.execute("UPDATE asset_need SET decision='pending',adopted_version_id=NULL,asset_id=NULL WHERE episode_id=?", (eid,))
        db.execute("INSERT OR REPLACE INTO episode_product_rule VALUES(?,?)", (eid, 0))
    result = env.wb.start_continuous(eid, target_stage="delivery", command_id="start")
    assert "出图提示词" not in (result["reason"] or ""), result["reason"]
    row = env.wb.asset_worksheet(eid)["rows"][0]
    assert row["adopted_version_id"] and row["decision"] == "reuse"
    assert env.wb.episode_batches(eid) == []


def test_field_124_project_overview_cover_oneline_stage_and_attention_todo(tmp_path):
    """实测 124：集卡封面、一句话故事、AI 当前阶段，以及停下的连续制作列入项目待办；状态条记录本步开始时刻。"""
    from workbench.db import transaction
    env, eid = _fixed_role_episode(tmp_path, preuse=False)
    pid = env.wb.episode_view(eid)["episode"]["project_id"]
    with transaction(env.wb.db_path) as db:
        db.execute("INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,reason,created_at,updated_at) VALUES('c1',?,'delivery','blocked',?,?,1,1)",
                   (eid, '{"stage":"assets","phase":"images"}', '素材「门」没有出图提示词，AI 无法为它出图。'))
    overview = env.wb.project_overview(pid)
    ep = next(e for e in overview["episodes"] if e["id"] == eid)
    assert ep["aiStage"] == "assets" and ep["cover"] is None and isinstance(ep["oneline"], str)
    todo = next(t for t in overview["todos"] if t["kind"] == "continuous")
    assert todo["episodeId"] == eid and todo["state"] == "blocked" and todo["stage"] == "assets"
    env.wb._continuous_cursor("c1", stage="script")
    cursor = env.wb._continuous_record("c1")["cursor"]
    assert cursor["stageStartedAt"] and cursor["stage"] == "script"
