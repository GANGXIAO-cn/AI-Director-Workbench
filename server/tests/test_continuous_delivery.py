"""J: real delivery/package receipts, isolated files and no external executors."""
import pytest
from helpers import Env
from workbench.continuous import Halt


def fixture(tmp_path):
    env=Env(tmp_path);case=env.full_episode(deliver=False);wb=env.wb
    from test_continuous_wake import run_record
    return env,case,run_record(env,case['eid'])


def test_delivery_completes_without_preview_confirmation(tmp_path):
    env,case,cid=fixture(tmp_path);wb=env.wb
    assert wb._continuous_delivery(cid)
    current=wb._continuous_record(cid)
    assert current['state']=='done' and current['choice'] is None
    did=current['cursor']['deliveryId']
    assert wb.verify_delivery(did)['ok']
    with env.db() as db:assert db.execute('SELECT count(*) FROM package').fetchone()[0]==0
    assert not wb.delivery_view(did)['packages']


def test_expert_delivery_only_packages_on_download(tmp_path):
    from fastapi.testclient import TestClient
    from workbench.app import create_app
    env,case,cid=fixture(tmp_path);wb=env.wb
    wb.set_cooperation_mode(case['eid'],'expert','advanced')
    assert wb._continuous_delivery(cid)
    current=wb._continuous_record(cid);did=current['cursor']['deliveryId']
    assert current['state']=='done' and not current['cursor'].get('packageId')
    assert not wb.delivery_view(did)['packages']
    assert wb.verify_delivery(did)['ok']
    with TestClient(create_app(wb,inline_worker=True,token='test')) as client:
        result=wb.package(did,wb.delivery_view(did)['segments'],'explicit-download')
        entry,=wb.delivery_view(did)['packages']
        assert entry['id']==result['packageId']
        response=client.get('/api/package-download/'+entry['id'])
        assert response.status_code==200 and response.headers['content-disposition'].startswith('attachment;')
        assert response.content==wb.package_file(entry['id']).read_bytes()
        assert len(wb.delivery_view(did)['packages'])==1


def test_completed_finishing_resume_does_not_repeat_plan_review(tmp_path,monkeypatch):
    env,case,cid=fixture(tmp_path);wb=env.wb;eid=case['eid']
    wb._continuous_cursor(cid,finishingReceipt=dict(worksheetRefs=1, **{stage:env.art(eid,stage)['adopted_version_id'] for stage in ('plan','prompts')}))
    def unexpected(*args,**kwargs):raise AssertionError('unchanged finishing must not submit another review')
    monkeypatch.setattr(wb,'submit_review',unexpected)
    assert wb._continuous_finish_stages(cid)


@pytest.mark.parametrize('stage',['space','cast','review','recheck','prompts','plan','assets','spec'])
def test_J_T1_delivery_problem_routes_and_spec_stops(tmp_path,monkeypatch,stage):
    env,case,cid=fixture(tmp_path);wb=env.wb;called=[]
    # The routing branch is tested separately from the real command tests in I/H.
    for method in ('_continuous_confirm_supplements','_image_fill_records','_continuous_cast','_continuous_create','_continuous_adopt','_continuous_cursor','_continuous_repair_delivery_assets','_continuous_images','_continuous_finish_stages'):
        monkeypatch.setattr(wb,method,lambda *a,_name=method,**k:called.append(_name) or True)
    monkeypatch.setattr(wb,'_continuous_prompt_checks',lambda c,v:called.append('_continuous_prompt_checks') or v)
    monkeypatch.setattr(wb,'_continuous_prompt_revision',lambda c,v,*a:called.append('_continuous_prompt_revision') or v)
    problem=dict(stage=stage,segment='G01' if stage=='recheck' else None,file=None,problem='本次核验问题')
    if stage=='spec':
        with pytest.raises(Halt):wb._continuous_delivery_problem(cid,problem)
        assert wb._continuous_record(cid)['state']=='failed' and wb._continuous_record(cid)['choice'] is None
        assert problem['problem'] in wb._continuous_record(cid)['reason']
    else:
        wb._continuous_delivery_problem(cid,problem)
        expected={'space':'_image_fill_records','cast':'_continuous_cast','review':'_continuous_prompt_checks','recheck':'_continuous_prompt_revision','prompts':'_continuous_prompt_revision','plan':'_continuous_create','assets':'_continuous_repair_delivery_assets'}
        assert expected[stage] in called


def test_J_T1_same_problem_stops_after_two_repairs(tmp_path,monkeypatch):
    env,case,cid=fixture(tmp_path);wb=env.wb;calls=[]
    monkeypatch.setattr(wb,'delivery_problems',lambda *a:([dict(stage='space',segment=None,file=None,problem='空间尚未确认')],None))
    monkeypatch.setattr(wb,'_continuous_delivery_problem',lambda *a:calls.append(a) or True)
    with pytest.raises(Halt):wb._continuous_delivery(cid)
    assert len(calls)==2 and '空间尚未确认' in wb._continuous_record(cid)['reason']


def test_report_text_only_does_not_restart_production(tmp_path):
    env,case,cid=fixture(tmp_path);wb=env.wb;eid=case['eid']
    skipped=wb.episode_view(eid)['skippedStages']['stress']
    wb.set_optional_stage(eid,'stress',False,expected_reason=skipped)
    report=dict(retelling='交接完成',scope='正文',diagnosis='旧表述',issues=[],options=[],productionNotes=[dict(id='P01',sceneIds=[],note='保留“原定动作”')])
    original,_=env.save_adopt(eid,'stress',report,'report-original')
    changed,_=env.save_adopt(eid,'stress',dict(report,diagnosis='更新诊断文字',productionNotes=[dict(id='P01',sceneIds=[],note='保留‘原定动作’')]),'report-updated')
    from workbench import productionnotes
    with env.db() as db:
        assert original in productionnotes.context(db,eid)['reportVersionIds']
    with env.db() as db:
        assert not db.execute("SELECT 1 FROM impact WHERE source_ref=? AND state='pending'",('stress:'+changed,)).fetchone()
    # Recover the same redundant impact left by an older service version.
    from workbench.db import transaction
    with transaction(wb.db_path) as db:
        art=dict(db.execute("SELECT * FROM artifact WHERE episode_id=? AND stage='stress'",(eid,)).fetchone())
        wb._record_impacts(db,art,[dict(target='workflow:prompts',reason='旧版传播')],'workflow_change','stress:'+changed)
    wb._continuous_settle_report_impacts(cid)
    with env.db() as db:
        assert not db.execute("SELECT 1 FROM impact WHERE source_ref=? AND state='pending'",('stress:'+changed,)).fetchone()
    notes=[dict(id='P01',sceneIds=[],note='新增制作要求')]
    changed,_=env.save_adopt(eid,'stress',dict(report,productionNotes=notes),'report-production-change')
    with env.db() as db:
        assert db.execute("SELECT 1 FROM impact WHERE source_ref=? AND state='pending'",('stress:'+changed,)).fetchone()
