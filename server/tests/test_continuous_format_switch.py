"""Small, offline run-chain scenarios; no full production or real model requests."""
import json
import pytest
from helpers import Env
from test_continuous_wake import run_record
from workbench.continuous import Halt
from workbench.db import transaction
from workbench.executors.base import ExecOutcome


def make_run(tmp_path):
    env=Env(tmp_path); _,eid=env.new_episode(); wb=env.wb
    cid=run_record(env,eid)
    rid=wb.create_task(eid,'story','起草故事',command_id='task')['runId']
    wb._continuous_cursor(cid,queue=[rid],ownedRuns=[rid],runId=rid)
    return env,eid,cid,rid


def fail(wb,rid,reason='schema'):
    with transaction(wb.db_path) as db:
        db.execute("UPDATE run SET status='failed',reason=?,diagnostic=? WHERE id=?",(reason,reason,rid))


def decisions(env,cid):
    with env.db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ai_decision WHERE continuous_id=? AND kind='retry_executor'",(cid,))]


def claim(wb,cid,auto=None):
    wb._continuous_update(cid,state='blocked')
    with transaction(wb.db_path) as db:wb._claim_resume(db,cid,auto=auto)


def test_format_switch_runs_review_provider_without_changing_settings(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb
    env.codex.script=[lambda req:ExecOutcome(False,error_class='schema',diagnostic='bad JSON')]*2
    wb._continuous_execute(cid)
    records=decisions(env,cid)
    assert len(env.codex.calls)==2 and len(records)==1
    assert json.loads(records[0]['detail'])['to']=='claude'
    assert not wb._continuous_record(cid)['cursor']['queue']
    with env.db() as db:
        plan=json.loads(db.execute('SELECT executor_plan FROM kickoff WHERE episode_id=?',(eid,)).fetchone()[0])
        assert plan['create']['provider']=='codex' and plan['review']['provider']=='claude'
        assert db.execute('SELECT count(*) FROM stage_executor').fetchone()[0]==0


@pytest.mark.parametrize('real_edit',[False,True])
def test_manual_success_claimed_before_user_change_detection(tmp_path,real_edit):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    child=wb.retry_run(rid,provider='claude',command_id='director-retry')['runId']
    wb.execute_run(child)
    art=env.art(eid,'story');version=art['versions'][-1]
    if real_edit:
        content=dict(version['content']);content['title']='导演的新标题'
        manual=wb.save_manual(art['id'],None,content,'director-edit')['versionId']
    claim(wb,cid)
    cursor=wb._continuous_record(cid)['cursor']
    assert child in cursor['ownedRuns']
    if real_edit:
        assert cursor['userDrafts']['story']==manual and not cursor['queue']
    else:
        assert cursor['queue']==[child] and not cursor.get('userDrafts')
        wb._continuous_execute(cid)
        assert not wb._continuous_record(cid)['cursor']['queue']


def test_checkpoint_claims_success_without_reconcile(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    child=wb.retry_run(rid,provider='claude',command_id='manual')['runId'];wb.execute_run(child)
    assert wb._continuous_checkpoint(cid)['cursor']['queue']==[child]


@pytest.mark.parametrize('status',['queued','running'])
def test_manual_inflight_waits_and_wakes(tmp_path,status):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    child=wb.retry_run(rid,provider='claude',command_id='manual')['runId']
    with transaction(wb.db_path) as db:db.execute('UPDATE run SET status=? WHERE id=?',(status,child))
    claim(wb,cid)
    if status=='running':
        # Use the real orchestration catch, before any stage is generated.
        assert wb._run_continuous(cid)['state']=='running'
        assert wb._continuous_record(cid)['cursor']['waitingRun']==child
        assert wb.changed_continuous_runs()==[]
        with transaction(wb.db_path) as db:db.execute("UPDATE run SET status='queued' WHERE id=?",(child,))
        wb.execute_run(child)
        assert wb.changed_continuous_runs()==[cid]
    wb._continuous_execute(cid)
    assert not wb._continuous_record(cid)['cursor'].get('waitingRun')
    assert len(env.claude.calls)==1 and not env.codex.calls


def test_manual_failed_retry_can_switch_latest(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    child=wb.retry_run(rid,provider='claude',command_id='manual')['runId'];fail(wb,child)
    claim(wb,cid);wb._continuous_execute(cid)
    record=decisions(env,cid)[0]
    assert json.loads(record['detail'])['from']=='claude'
    assert json.loads(record['detail'])['to']=='codex'


@pytest.mark.parametrize('single_provider',[False,True])
def test_no_candidate_or_second_format_failure_stops(tmp_path,single_provider):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    if single_provider:wb.executors={'codex':env.codex}
    else:env.claude.script=[lambda req:ExecOutcome(False,error_class='schema')]*2
    with pytest.raises(Halt):wb._continuous_execute(cid)
    reason=wb._continuous_record(cid)['reason']
    assert ('没有其他可用执行器' if single_provider else '本轮已换过一次执行器') in reason
    assert len(decisions(env,cid))==(0 if single_provider else 1)


@pytest.mark.parametrize('recovery',['retry','restart-retry'])
@pytest.mark.parametrize('boundary',['auto','continue','manual'])
def test_auto_descendant_inherits_budget_but_director_can_retry(tmp_path,recovery,boundary):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    b=wb.retry_run(rid,provider='claude',command_id=f'{cid}:format-switch:0:{rid}')['runId']
    fail(wb,b,'network' if recovery=='retry' else '服务重启时中断')
    c=wb.retry_run(b,command_id=f'{cid}:{recovery}:{b}')['runId'];fail(wb,c)
    claim(wb,cid,auto=[])
    assert wb._continuous_record(cid)['cursor'].get('formatRound',0)==0
    if boundary=='continue':claim(wb,cid)
    elif boundary=='manual':
        m=wb.retry_run(c,command_id='director-retry')['runId'];fail(wb,m)
    if boundary=='auto':
        with pytest.raises(Halt):wb._continuous_execute(cid)
        assert len(decisions(env,cid))==1
    else:
        wb._continuous_execute(cid)
        assert len(decisions(env,cid))==2


def test_crash_after_switch_command_recovers_receipt_once(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    b=wb.retry_run(rid,provider='claude',command_id=f'{cid}:format-switch:0:{rid}')['runId']
    fail(wb,b)
    assert not decisions(env,cid)
    claim(wb,cid,auto=[])
    wb._continuous_checkpoint(cid);wb._continuous_checkpoint(cid)
    records=decisions(env,cid)
    assert len(records)==1 and records[0]['ref']==b
    assert json.loads(records[0]['detail'])['round']==0
    assert wb._continuous_record(cid)['cursor']['queue']==[b]
    with pytest.raises(Halt):wb._continuous_execute(cid)
    with env.db() as db:assert db.execute('SELECT count(*) FROM run').fetchone()[0]==2


@pytest.mark.parametrize('reason',['network','authentication','timeout'])
def test_nonformat_uses_existing_recovery(tmp_path,reason):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid,reason)
    env.codex.script=[lambda req:ExecOutcome(False,error_class=reason)]
    with pytest.raises(Halt):wb._continuous_execute(cid)
    wb._commit_blocks(cid)
    assert not decisions(env,cid)
    assert wb._continuous_record(cid)['cursor']['blocks'][0]['kind']==('auth' if reason=='authentication' else 'transient')


def test_direct_run_claims_manual_child_without_switch(tmp_path):
    from test_image_assess import fixture, reply, assessment
    env,eid,vid=fixture(tmp_path);wb=env.wb;cid=run_record(env,eid)
    rid=wb.prepare_image_assess(eid,[vid],'assess')['runId']
    wb._continuous_cursor(cid,ownedRuns=[rid],directRuns={rid:rid})
    fail(wb,rid)
    env.codex.script=[reply([assessment(vid)])]
    child=wb.retry_run(rid,command_id='manual')['runId'];wb.execute_run(child)
    assert wb._continuous_run_direct(cid,rid)==child
    assert wb._continuous_record(cid)['cursor']['directRuns'][rid]==child
    assert not decisions(env,cid)
    assert wb.finish_image_assess(child)[0]['versionId']==vid


def test_stage_review_override_and_model_used(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    wb.save_stage_executor(env.art(eid,'story')['id'],'review','antigravity','test-model',None,'stage-review')
    wb._continuous_cursor(cid,stage='prompts')
    wb._continuous_execute(cid)
    with env.db() as db:
        retried=db.execute('SELECT * FROM run WHERE id=?',(decisions(env,cid)[0]['ref'],)).fetchone()
        assert retried['provider']=='antigravity' and retried['requested_model']=='test-model'


def test_invalid_review_falls_back_to_expert(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb;fail(wb,rid)
    with transaction(wb.db_path) as db:
        plan=json.loads(db.execute('SELECT executor_plan FROM kickoff WHERE episode_id=?',(eid,)).fetchone()[0])
        plan['review']=dict(provider='claude',model='invalid model name')
        plan['expert']=dict(provider='antigravity')
        db.execute('UPDATE kickoff SET executor_plan=? WHERE episode_id=?',(json.dumps(plan),eid))
    wb._continuous_execute(cid)
    assert json.loads(decisions(env,cid)[0]['detail'])['to']=='antigravity'


@pytest.mark.parametrize('reason',['回包两次不符合契约','已收到回包，未通过入库校验'])
def test_explicit_contract_reasons_switch(tmp_path,reason):
    env,eid,cid,rid=make_run(tmp_path);fail(env.wb,rid,reason)
    env.wb._continuous_execute(cid)
    assert len(decisions(env,cid))==1


def test_ingest_failure_does_not_switch(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);fail(env.wb,rid,'回包无法入库')
    env.wb._continuous_execute(cid)
    assert not decisions(env,cid) and env.codex.calls and not env.claude.calls


def test_pause_while_waiting_prevents_auto_wake(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb
    with transaction(wb.db_path) as db:db.execute("UPDATE run SET status='running' WHERE id=?",(rid,))
    assert wb._run_continuous(cid)['state']=='running'
    wb.pause_continuous(cid);fail(wb,rid)
    assert wb.changed_continuous_runs()==[]
    assert wb._run_continuous(cid)['state']=='paused'


def test_continue_retries_same_provider_when_no_other_candidate(tmp_path):
    env,eid,cid,rid=make_run(tmp_path);wb=env.wb
    wb.executors={'codex':env.codex}
    fail(wb,rid,'回包两次不符合契约')
    with pytest.raises(Halt):wb._continuous_execute(cid)
    assert '没有其他可用执行器' in wb._continuous_record(cid)['reason']
    env.codex.script=[lambda req:ExecOutcome(False,error_class='schema')]*2
    claim(wb,cid)
    with pytest.raises(Halt):wb._continuous_execute(cid)
    assert '本轮已重试过一次（codex）' in wb._continuous_record(cid)['reason']
    assert '导演继续后用 codex 重试' in decisions(env,cid)[0]['reason']
    env.codex.script=[]
    claim(wb,cid)
    wb._continuous_execute(cid)
    assert not wb._continuous_record(cid)['cursor']['queue']


def test_failed_contract_saves_failed_result_json(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb
    env.codex.script=[lambda req:ExecOutcome(True,structured=dict(title='缺梗概'))]*2
    rid=wb.create_task(eid,'story','起草故事',command_id='save-failed')['runId']
    wb.execute_run(rid)
    saved=wb.store.run_dir(rid)/'failed-result.json'
    assert saved.is_file() and '缺梗概' in saved.read_text(encoding='utf-8')
