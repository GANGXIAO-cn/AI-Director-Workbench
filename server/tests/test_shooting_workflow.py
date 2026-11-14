import json
import pytest
from workbench import stages, timeline
from workbench.shooting import writing_problems
from test_continuous_finishing import finishing_fixture


def test_new_script_survives_normalization_and_rendering_without_breaking_old_scripts():
    segments=[dict(id='seg-01',sceneId='scene-01',order=1,duration_ms=15000,action='拿起杯子，铃响',startState='杯在桌上',endState='藏爪')]
    assert 'shootingSegments' in stages.WRITABLE['script']
    assert stages.normalize('script','shootingSegments',json.dumps(segments)) == segments
    script=dict(scenes=[dict(id='scene-01',action='拿起杯子')],shootingSegments=segments)
    assert 'seg-01' in stages.render('script',script)
    assert not stages.check_content('script',script)
    assert not stages.check_content('script',dict(scenes=script['scenes']))
    assert not writing_problems('script',dict(scenes=script['scenes']))


def test_expert_writing_feedback_keeps_camera_continuity_and_whole_seconds():
    script=dict(shootingSegments=[dict(id='s1',duration_ms=15000)])
    plan=dict(segments=[dict(id='s1',order=1)],shots=[dict(id=f'w{i}',segment='s1',order=i,duration_ms=3000,continuation='same') for i in range(5)])
    assert not writing_problems('plan',plan,script)
    plan['shots'][0]['duration_ms']=2500
    assert any('1–3秒整秒' in e for e in writing_problems('plan',plan,script))
    assert any('预算' in e for e in writing_problems('plan',plan,script))
    assert not writing_problems('plan',plan,{})  # old drafts are not invalidated


def test_cold_read_has_no_upstream_material_conclusions(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True)
    run=env.wb.start_prompt_review(vid,'G01','cold_read','clean-cold')
    manifest=env.wb.run_manifest(run['runId'])['manifest']
    entries=env.wb.store.manifest_texts(manifest)
    assert not {'material_use_conclusions','accepted_image_issue','provisional_image_issue'} & {e['role'] for e in entries}
    assert not any('仅审核最终提示词的翻译' in (e.get('text') or '') for e in entries)
    assert any(e['role']=='segment_txt' for e in entries)


@pytest.mark.parametrize('duration',[0,999,1500,3001,4000])
def test_requested_window_bounds(duration):
    script=dict(shootingSegments=[dict(id='s1',duration_ms=5000)])
    plan=dict(segments=[dict(id='s1',order=1)],shots=[dict(id='w1',segment='s1',order=1,duration_ms=duration)])
    assert any('1–3秒整秒' in e for e in writing_problems('plan',plan,script))


def test_plan_inputs_include_real_identity_and_grid_availability(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True)
    from workbench.db import connect
    with connect(env.wb.db_path) as db:
        task=dict(id='test-plan-input',stage='plan',episode_id=case['eid'],project_id=db.execute('SELECT project_id FROM episode WHERE id=?',(case['eid'],)).fetchone()[0])
        entries=env.wb._extra_creation_inputs(db,task)
    inventory=json.loads(next(e['text'] for e in entries if e['role']=='plan_asset_inventory'))
    ids={v['id'] for v in inventory}
    assert case['img']['versionId'] in ids
    for v in inventory:
        assert v['grid']==env.wb.asset_version(v['id'])['grid']
