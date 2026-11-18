from test_continuous_finishing import finishing_fixture
from test_continuous_wake import run_record
from workbench import gridcontinuity, promptdraft


def fixture(tmp_path):
    env, case, old, _ = finishing_fixture(tmp_path, scoped=True)
    plan = case['plan']
    for segment in plan['segments']:
        segment['strategy'] = 'grid'
    plan_id, _ = env.save_adopt(case['eid'], 'plan', plan, 'boards')
    env.wb._continuous_update(old, state='failed')
    cid = run_record(env, case['eid'])
    grids = env.wb._grid_artwork(cid, case['eid'], plan_id)
    return env, case, cid, plan_id, grids


def test_new_run_reuses_boards_even_when_reference_order_changes(tmp_path):
    env, case, old, plan_id, grids = fixture(tmp_path)
    wb, eid = env.wb, case['eid']
    count = env.images.sent
    for board in grids.values():
        board['inputs'].reverse()
    wb._continuous_cursor(old, gridArtwork=grids)
    wb._continuous_update(old, state='failed')
    cid = run_record(env, eid)
    resumed = wb._grid_artwork(cid, eid, plan_id)
    assert {k: v['versionId'] for k, v in resumed.items()} == {k: v['versionId'] for k, v in grids.items()}
    assert env.images.sent == count
    assert all(len(s['refs']) == 1 for s in promptdraft.draft(wb, eid, bindings_only=True)['segments'].values())


def test_legacy_duplicate_boards_are_reconciled_before_prompt_drafting(tmp_path):
    env, case, old, plan_id, grids = fixture(tmp_path)
    wb, eid = env.wb, case['eid']
    all_images = [g['versionId'] for g in grids.values()]
    # Reproduce E18: prior runs' generated boards still carry automatic scope.
    for n in range(2):
        for index, sid in enumerate(grids):
            image = env.image(case['pid'], eid, sid + ' old board', 'grid', grid=True, rgb=(n+40, index+2, 3))
            vid = image['versionId']; all_images.append(vid)
            shots = [s['id'] for s in case['plan']['shots'] if s['segment'] == sid]
            wb._continuous_bind(old, vid, shots, plan_id, 'grid:' + sid, str(n), '旧轮九宫格', None)
            wb.set_cast(eid, sid, '阿福', 'ok', vid, opening_confirmed=True, note='旧轮九宫格角色绑定')
    import pytest
    from workbench.errors import Rejected
    with pytest.raises(Rejected, match='多张九宫格'):
        promptdraft.draft(wb, eid, bindings_only=True)
    wb._continuous_update(old, state='failed')
    cid = run_record(env, eid)
    count = env.images.sent
    wb._grid_artwork(cid, eid, plan_id)
    assert env.images.sent == count
    segments = promptdraft.draft(wb, eid, bindings_only=True)['segments']
    for sid, board in grids.items():
        assert [r['assetVersionId'] for r in segments[sid]['refs']] == [board['versionId']]
    assert all(wb.asset_bytes(vid) for vid in all_images)
    # A repeated pass changes neither choices nor generation count.
    wb._grid_artwork(cid, eid, plan_id)
    assert env.images.sent == count


def test_releasing_old_grid_keeps_director_scope(tmp_path):
    env, case, cid, plan_id, grids = fixture(tmp_path)
    wb, eid = env.wb, case['eid']
    old = grids['G01']['versionId']
    wb.mark_image(eid, old, 'required', requirement=dict(targetSegments=['G02'], reason='导演要求保留'))
    wb._continuous_bind(cid, old, ['S01'], plan_id, 'grid:G01', 'auto', '系统附加', wb._continuous_image_rule(eid, old))
    gridcontinuity.reconcile(wb, cid, eid, plan_id, {'G02': grids['G02']})
    rule = wb._continuous_image_rule(eid, old)
    assert rule['targetSegments'] == ['G02']
    assert rule['selectedBy'] == 'user'
    assert 'grid:G01' not in rule['autoBindings']
    assert wb.asset_bytes(old)


def test_changed_storyboard_regenerates_only_affected_segment(tmp_path):
    env, case, old, plan_id, grids = fixture(tmp_path)
    wb, eid = env.wb, case['eid']
    case['plan']['segments'][0]['gridPrompt'] = '改为近景看清主角面部'
    plan_id, _ = env.save_adopt(eid, 'plan', case['plan'], 'changed-board')
    wb._continuous_update(old, state='failed')
    cid = run_record(env, eid)
    count = env.images.sent
    result = wb._grid_artwork(cid, eid, plan_id)
    assert env.images.sent == count + 1
    assert result['G01']['versionId'] != grids['G01']['versionId']
    assert result['G02']['versionId'] == grids['G02']['versionId']
    assert wb._continuous_image_rule(eid, grids['G01']['versionId']) is None
    assert wb.asset_bytes(grids['G01']['versionId'])


def test_prompt_repair_resolves_grid_bindings_before_request(tmp_path, monkeypatch):
    env, case, cid, plan_id, grids = fixture(tmp_path)
    wb = env.wb
    calls = []
    wb._continuous_cursor(cid, nextRepair=dict(stage='prompts', method='patch', reason='图片绑定冲突'))
    monkeypatch.setattr(wb, '_grid_artwork', lambda *args: calls.append('bindings'))
    monkeypatch.setattr(wb, '_continuous_create', lambda *args, **kwargs: calls.append('writing'))
    monkeypatch.setattr(wb, '_continuous_adopt', lambda *args: None)
    wb._expert_repair_step(cid)
    assert calls == ['bindings', 'writing']
