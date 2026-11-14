"""R2: accepted materials are inputs, not prompt-stage approval requests."""
from test_continuous_finishing import finishing_fixture
from workbench.continuous_images import usable
from workbench.materialcontext import binding_context


def test_prompt_finish_never_reassesses_or_writes_observations(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    def forbidden(*a,**kw):raise AssertionError('prompt assembly must not reassess materials')
    monkeypatch.setattr(wb,'assess_images',forbidden)
    monkeypatch.setattr(wb,'_continuous_reference_props',forbidden)
    monkeypatch.setattr(wb,'save_shot_observation',forbidden)
    assert wb._continuous_shot_observations(cid,vid)==vid
    assert not any('逐镜' in e or '观察' in e for e in wb.adoption_gate(vid)['formatErrors'])
    job=wb.start_prompt_review(vid,'G01','cold_read','r2-review')
    assert job['runId']


def test_material_missing_still_fails_assembly(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    content=wb._continuous_version(vid)
    content['segments']['G01']['refs'][0]['assetVersionId']='missing-image'
    with env.db() as db:errors=wb._prompt_reference_errors(db,case['eid'],content['segments'])
    assert any('不在本集' in e for e in errors)


def test_visible_roles_determine_cast_without_another_image_call(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    with env.db() as db:db.execute("UPDATE cast_cell SET status='unclear' WHERE episode_id=?",(case['eid'],))
    monkeypatch.setattr(wb,'assess_images',lambda *a,**k:(_ for _ in ()).throw(AssertionError('unexpected assessment')))
    wb._continuous_cast(cid)
    assert all(row['cells']['阿福']['status']=='ok' for row in wb.cast_matrix(case['eid'])['rows'])


def test_limited_minor_and_legacy_binding():
    assert usable(dict(verdict='limited',critical=[],minor=[dict(text='背景差异')]))
    assert not usable(dict(verdict='blocked',critical=[dict(type='anatomy')]))
    value=binding_context(dict(assetVersionId='v',role='镜头参考',shots=['s']))
    assert value['usage']=='legacy' and value['observation'] is None


def test_old_judgment_resume_keeps_evidence_and_candidate(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    receipts={'引用用途核对:G01:v':dict(result={'mode':'unresolved'})}
    wb._continuous_cursor(cid,phase='judgment',judgmentKind='引用用途核对',promptCandidate=vid,judgmentReceipts=receipts)
    wb._continuous_update(cid,state='blocked',reason='旧门槛')
    with env.db() as db:wb._claim_resume(db,cid)
    cursor=wb._continuous_record(cid)['cursor']
    assert cursor['promptCandidate']==vid and cursor['judgmentReceipts']==receipts
    assert cursor['retiredGates']['prompt-material-v1']['status']=='no_longer_required'
    wb._continuous_update(cid,state='blocked')
    with env.db() as db:
        wb._claim_resume(db,cid)
        logs=db.execute("SELECT detail FROM ai_decision WHERE continuous_id=? AND kind='decide_skip'",(cid,)).fetchall()
    assert len(logs)==1



def test_without_observation_adopt_review_export_and_verify(tmp_path):
    from pathlib import Path
    import json
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    eid=case['eid'];content=wb._continuous_version(vid)
    assert all(not r.get('visualReviewId') for e in content['segments'].values() for r in e['refs'])
    wb.adopt(env.art(eid,'prompts')['id'],vid,env.art(eid,'prompts')['adopted_version_id'],'r2-adopt')
    env.review_segments(vid,list(content['segments']))
    result=wb.deliver(eid,'trial','r2-delivery',['G01'])
    assert wb.verify_delivery(result['deliveryId'])['ok']
    page=(Path(result['path'])/'逐镜参考.html').read_text()
    assert '未登记历史观察' in page
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM shot_image_observation WHERE episode_id=?',(eid,)).fetchone()[0]==0
    assert all(wb.prompt_review_status(vid)[sid]['ok'] for sid in content['segments'])
    content['segments']['G01']['materialUses']=[{'usage':'legacy'}]
    new=wb.save_manual(env.art(eid,'prompts')['id'],vid,content,'metadata-only')['versionId']
    assert all(wb.prompt_review_status(new)[sid]['ok'] for sid in content['segments'])


def test_binding_structure_rejects_wrong_scope_without_image_review(tmp_path):
    from workbench import shotrefs
    import pytest
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb;eid=case['eid']
    with env.db() as db:
        packet=shotrefs.segment_packet(wb,db,eid,wb._adopted_plan(db,eid),wb._cast_matrix(db,eid),'G01',wb._continuous_version(vid)['segments']['G01'])
    assert shotrefs.check_binding_structure(packet['files'].__getitem__,packet['manifest'])['status']=='passed'
    packet['manifest']['segments'][0]['references'][0]['shots']=['unknown-shot']
    with pytest.raises(ValueError,match='作用镜头'):
        shotrefs.check_binding_structure(packet['files'].__getitem__,packet['manifest'])
