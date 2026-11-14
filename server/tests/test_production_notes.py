"""PN-T1–T5: deterministic versioned assignments and candidate coverage."""
from workbench import productionnotes as pn


def basis():
    return dict(reportVersionId='report-new',notes=[dict(id='P01',note='保留门厅位置'),dict(id='P02',note='镜头中显示支撑关系')],stages={s:{} for s in pn.STAGES})


def response(key,owner,status='applied',**extra):
    return dict(id=key,reportVersionId='report-new',owner=owner,status=status,**extra)


def test_PN_T1_asset_design_must_respond_to_all_notes():
    content=dict(assets=[dict(id='room')],productionNoteResponses=[response('P01','asset_design',where='room')])
    errors=pn.check('asset_design',content,basis())
    assert errors==['制作提醒 P02 尚未回应（负责阶段：asset_design）']
    content['productionNoteResponses'].append(response('P02','plan','assigned',reason='镜头负责呈现支撑'))
    assert pn.check('asset_design',content,basis())==[]


def test_PN_T2_applied_location_must_exist_in_candidate():
    content=dict(assets=[dict(id='room')],productionNoteResponses=[response('P01','asset_design',where='missing'),response('P02','plan','assigned',reason='镜头负责')])
    assert any('where' in error for error in pn.check('asset_design',content,basis()))
    content['productionNoteResponses'][0].pop('where')
    content['productionNoteResponses'][0]['whereIds']=['room','old-room']
    assert not pn.check('asset_design',content,basis())


def test_PN_T3_asset_and_plan_applied_cover_final_candidate():
    b=basis();b['stages']['asset_design']=dict(assets=[dict(id='room')],productionNoteResponses=[response('P01','asset_design',where='room'),response('P02','plan','assigned',reason='镜头负责')])
    plan=dict(shots=[dict(id='S01')],productionNoteResponses=[response('P02','plan',where='S01')])
    assert not pn.check('plan',plan,b)
    b['stages']['plan']=plan
    assert not pn.check('prompts',dict(segments={'G01':{}}),b,coverage=True)


def test_PN_T4_unapplied_assignment_blocks_candidate_not_old_prompt():
    b=basis();b['notes']=[dict(id='P03',note='需要显示道具')]
    b['stages']['asset_design']=dict(productionNoteResponses=[response('P03','prompts','assigned',reason='最后落实')])
    b['stages']['prompts']=dict(segments={'G01':{}},productionNoteResponses=[response('P03','prompts',where='G01')])
    errors=pn.check('prompts',dict(segments={'G01':{}}),b,coverage=True)
    assert '制作提醒 P03 尚未落实（负责阶段：prompts）' in errors
    assert not pn.check('prompts',b['stages']['prompts'],b,coverage=True)


def test_PN_T5_old_report_responses_do_not_count_after_new_adoption():
    b=basis();old=[dict(response(key,'asset_design',where='room'),reportVersionId='report-old') for key in ('P01','P02')]
    b['stages']['asset_design']=dict(assets=[dict(id='room')],productionNoteResponses=old)
    errors=pn.check('asset_design',b['stages']['asset_design'],b)
    assert len([e for e in errors if '当前06' in e])==2
    assert len([e for e in pn.check('prompts',dict(segments={}),b,coverage=True) if '尚未落实' in e])==2


def test_PN_T1_continuous_adoption_rejects_missing_note_response(tmp_path):
    from test_image_assess import fixture
    env,eid,_=fixture(tmp_path)
    old=env.art(eid,'asset_design')['adopted_version_id']
    env.save_adopt(eid,'stress',dict(retelling='门铃在门厅',scope='本集',diagnosis='故事成立',issues=[],options=[],productionNotes=[dict(id='P01',note='保持门厅位置',sceneIds=['场01'])]),'report')
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    # Exercise the adoption boundary directly; the legacy fixture deliberately
    # skips stress, so its preceding orchestration is not part of this scenario.
    from workbench.continuous import Halt
    import pytest
    env.wb._continuous_update(result['id'],state='running')
    with pytest.raises(Halt):env.wb._continuous_adopt(result['id'],'asset_design',old)
    assert 'P01' in env.wb._continuous_record(result['id'])['reason']
    assert env.art(eid,'asset_design')['adopted_version_id']==old
    job=env.wb.create_task(eid,'asset_design','核对提醒输入',discussion_only=True,workspace_revision=env.art(eid,'asset_design')['revision'])
    manifest=env.wb.run_manifest(job['runId'])['manifest']
    assert any(entry['role']=='production_note_assignments' for entry in manifest['inputs'])


def test_unchanged_notes_reuse_prior_report_responses(tmp_path):
    from test_image_assess import fixture
    env,eid,_=fixture(tmp_path)
    report=dict(retelling='门铃在门厅',scope='本集',diagnosis='原诊断',issues=[],options=[],productionNotes=[dict(id='P01',note='保持门厅位置',sceneIds=['场01'])])
    old,_=env.save_adopt(eid,'stress',report,'note-original')
    new,_=env.save_adopt(eid,'stress',dict(report,diagnosis='仅同步诊断'),'note-same')
    with env.db() as db:b=pn.context(db,eid)
    assert old in b['reportVersionIds'] and new in b['reportVersionIds']
    content=dict(assets=[dict(id='room')],productionNoteResponses=[dict(id='P01',reportVersionId=old,owner='asset_design',status='applied',whereIds=['room'])])
    assert not pn.check('asset_design',content,b)
    env.save_adopt(eid,'stress',dict(report,productionNotes=[dict(id='P01',note='改到屋顶',sceneIds=['场01'])]),'note-changed')
    with env.db() as db:b=pn.context(db,eid)
    assert old not in b['reportVersionIds']
    assert pn.check('asset_design',content,b)
