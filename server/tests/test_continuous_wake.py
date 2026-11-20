"""Recovery acceptance with isolated SQLite state and simulated providers only."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from helpers import Env
from workbench.db import transaction, dumps, new_id
from workbench.continuous import Halt
from workbench.continuous_images import MaterialWait, generated_count
from workbench.images import INTERRUPTED_IMAGE, TRANSPORT_FAILURE, ImageOutcome
from workbench.executors.base import ExecOutcome
from workbench.service import Workbench
from workbench.app import create_app
from test_image_assess import fixture
from test_continuous_images import image_reply


def run_record(env,eid,state='running',**fields):
    wb=env.wb
    from workbench.stressrepairs import adopted
    with transaction(wb.db_path) as db:
        cid=new_id()
        cursor=dict(base=adopted(db,eid),ownedRuns=[],queue=[],attempts={},rounds={},stressReports=0,
                    userStamp=wb._continuous_user_stamp(db,eid,[]),**fields)
        db.execute('INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                   (cid,eid,'delivery',state,dumps(cursor),wb.clock(),wb.clock()))
    return cid


def stop(wb,cid,blocks):
    with pytest.raises(Halt):wb._continuous_problem(cid,'等待恢复',blocks=blocks)


def auth_failure(req):
    return ExecOutcome(False,error_class='authentication',diagnostic='authentication required')


def test_restart_resumes_running_but_not_director_pause(tmp_path,monkeypatch):
    env=Env(tmp_path);_,eid=env.new_episode()
    a=run_record(env,eid)
    _,other=env.new_episode(code='E02');b=run_record(env,other)
    env.wb.pause_continuous(b)
    wb=Workbench(env.wb.store.root,env.wb.executors,image_providers=env.wb.image_providers_map)
    calls=[]
    monkeypatch.setattr(wb,'_advance_continuous',lambda cid:calls.append(cid))
    create_app(wb,inline_worker=True)
    assert calls==[a]
    assert wb._continuous_record(b)['state']=='paused'
    assert wb._continuous_record(b)['reason']=='已暂停，可继续'
    assert not wb._continuous_record(a)['cursor'].get('resumeAfterRestart')


@pytest.mark.parametrize('edit',[False,True])
@pytest.mark.parametrize('error',[INTERRUPTED_IMAGE,TRANSPORT_FAILURE])
def test_interrupted_image_new_batch_and_edit_refund(tmp_path,edit,error):
    env,eid,vid=fixture(tmp_path);wb=env.wb
    cid=run_record(env,eid);wb.initialize_asset_worksheet(eid,env.art(eid,'asset_design')['adopted_version_id'],'worksheet')
    need=wb._need(eid,'bell')
    env.images.remote_status=True;env.images.script=['timeout']
    if edit:
        wb._image_state(cid,'bell',edits=1)
        with transaction(wb.db_path) as db:db.execute('INSERT INTO asset_need_candidate VALUES(?,?)',(need['id'],vid))
    with pytest.raises(MaterialWait):wb._image_generate(cid,need,edit=dict(prompt='修正门铃',versionId=vid,n=1) if edit else None)
    old=wb._image_state(cid,'bell')['pendingBatch']
    env.images.inspect=lambda _:ImageOutcome('failed',error=error)
    wb.settle_unknown_requests([old])
    assert wb.batch_view(old)['requests'][0]['error']==error
    wb._image_generate(cid,wb._need(eid,'bell'))
    state=wb._image_state(cid,'bell')
    assert state['attempts'][-1]['interrupted'] and generated_count(state['attempts'])==0
    if edit:assert state['edits']==0
    wb._image_generate(cid,wb._need(eid,'bell'))
    batches=wb.episode_batches(eid)
    assert len(batches)==2 and env.images.sent==2
    assert all(not r.get('replaces_id') for b in batches for r in wb.batch_view(b['id'])['requests'])


def test_auth_recovery_limit_and_manual_reset(tmp_path,monkeypatch):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb
    env.codex.script=[auth_failure]*20
    result=wb.start_continuous(eid,command_id='start');cid=result['id']
    assert result['cursor']['blocks'][0]['kind']=='auth'
    monkeypatch.setattr(wb,'executor_identity',lambda provider:dict(authenticated=True))
    now=wb.clock()
    for i in range(3):
        assert wb.changed_continuous_runs(now+61*i)==[cid]
        wb._advance_continuous(cid)
    assert wb.changed_continuous_runs(now+500)==[]
    assert list(wb._continuous_record(cid)['cursor']['wakeCounts'].values())==[3]
    assert wb._continuous_record(cid)['state']=='failed'  # no further probes or manual retry loop


def test_paused_choice_and_missing_do_not_wake(tmp_path,monkeypatch):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb
    b=wb._recovery_block('run:story:create','auth',provider='codex',role='create')
    cid=run_record(env,eid,'paused',blocks=[b])
    monkeypatch.setattr(wb,'executor_identity',lambda provider:dict(authenticated=True))
    assert wb.changed_continuous_runs()==[]
    import sqlite3
    for forbidden in ('waiting_choice','blocked'):
        with pytest.raises(sqlite3.IntegrityError):wb._continuous_update(cid,state=forbidden)
    assert wb._continuous_record(cid)['state']=='paused'


def test_only_ready_item_executes(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path);wb=env.wb
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id=k,kind='prop',title=k,description=k,imagePrompt=k,sceneIds=['场01']) for k in ('bell','other')]),'two')
    cid=run_record(env,eid)
    wb.initialize_asset_worksheet(eid,env.art(eid,'asset_design')['adopted_version_id'],'worksheet')
    auth=wb._recovery_block('need:bell','auth',provider='codex',role='image_assess')
    image=wb._recovery_block('need:other','image_wait',role='image',batchIds=['finished'])
    wb._continuous_cursor(cid,blocks=[auth,image],stoppedNeeds=[dict(key='bell',title='bell',reason='登录失效',**{k:auth[k] for k in ('kind','provider','role','batchIds','resetsAt')})])
    wb._continuous_update(cid,state='waiting_external')
    monkeypatch.setattr(wb,'_recovery_probes',lambda snapshots,now:dict(identity={'codex':False},batches={'finished':True}))
    calls=[]
    def run_round(cid,need,parallel=False):
        calls.append(need['need_key'])
        wb.set_asset_need(eid,need['need_key'],need['revision'],'text_only','finish-'+need['need_key'])
    monkeypatch.setattr(wb,'_image_need_round',run_round)
    monkeypatch.setattr(wb,'_image_fill_records',lambda cid:True)
    assert wb.changed_continuous_runs()==[cid]
    with pytest.raises(Halt):wb._continuous_images(cid)
    assert calls==['other']
    assert wb._continuous_record(cid)['cursor']['wakeCounts'][auth['id']]==0
    monkeypatch.setattr(wb,'_recovery_probes',lambda snapshots,now:dict(identity={'codex':True},batches={}))
    assert wb.changed_continuous_runs()==[cid]
    assert wb._continuous_images(cid)
    assert calls==['other','bell']


def parked_batch(tmp_path):
    env,eid,_=fixture(tmp_path);wb=env.wb;cid=run_record(env,eid)
    wb.initialize_asset_worksheet(eid,env.art(eid,'asset_design')['adopted_version_id'],'worksheet')
    env.images.remote_status=True;env.images.script=['timeout']
    with pytest.raises(MaterialWait):wb._image_generate(cid,wb._need(eid,'bell'))
    bid=wb._image_state(cid,'bell')['pendingBatch']
    return env,eid,cid,bid


def test_reconciled_batch_wakes_and_adopts(tmp_path):
    env,eid,cid,bid=parked_batch(tmp_path);wb=env.wb
    stop(wb,cid,[wb._recovery_block('need:bell','image_wait',role='image',batchIds=[bid])])
    req=wb.batch_view(bid)['requests'][0]
    wb.reconcile_request(req['id'],'not_executed')
    wb.resubmit_request(req['id'],'resubmit');wb.execute_batch(bid)
    assert wb.changed_continuous_runs()==[cid]
    env.codex.script=[lambda req:image_reply(req)]*10
    assert wb._image_need_round(cid,wb._need(eid,'bell')) is None
    assert wb._need(eid,'bell')['adopted_version_id']


def test_interrupted_text_has_separate_retry_budget(tmp_path,monkeypatch):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb;cid=run_record(env,eid)
    rid=wb.create_task(eid,'story','起草故事',command_id='task')['runId']
    with transaction(wb.db_path) as db:db.execute("UPDATE run SET status='failed',reason='服务重启时中断' WHERE id=?",(rid,))
    observed=[];original=wb.execute_run
    def execute(rid):
        observed.append(dict(wb._continuous_record(cid)['cursor'].get('retried',{})))
        return original(rid)
    monkeypatch.setattr(wb,'execute_run',execute)
    env.codex.script=[auth_failure]
    wb._continuous_execute(cid,rid)
    assert observed[0]=={} and observed[1]
    assert len(wb._continuous_record(cid)['cursor']['restartRetries'])==1


def test_real_image_failure_counts_two_attempts(tmp_path):
    env,eid,_=fixture(tmp_path);wb=env.wb;cid=run_record(env,eid)
    wb.initialize_asset_worksheet(eid,env.art(eid,'asset_design')['adopted_version_id'],'worksheet')
    env.images.script=['insufficient']*5
    env.codex.script=[lambda req:dict(ids=[],reason='无适用图') if req.purpose=='progress_expert' else image_reply(req,'blocked')]*20
    wait=wb._image_need_round(cid,wb._need(eid,'bell'))
    attempts=wb._image_state(cid,'bell')['attempts']
    assert env.images.sent==1 and generated_count(attempts)==1
    assert all(not a.get('interrupted') and 'insufficient_quota' in a['result'] for a in attempts)
    assert wait['kind']=='quota'


def test_running_failed_batch_wakes(tmp_path):
    env,eid,cid,bid=parked_batch(tmp_path);wb=env.wb
    wb._continuous_cursor(cid,waits={'bell':dict(running=True)})
    env.images.inspect=lambda _:ImageOutcome('failed',error=INTERRUPTED_IMAGE)
    assert wb.changed_continuous_runs()==[cid]
    assert wb.batch_view(bid)['state']=='failed'
    wb._image_generate(cid,wb._need(eid,'bell'))
    wb._image_generate(cid,wb._need(eid,'bell'))
    assert len(wb.episode_batches(eid))==2


def test_timer_and_post_claim_once(tmp_path,monkeypatch):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb;cid=run_record(env,eid)
    stop(wb,cid,[wb._recovery_block('run:story:create','auth',provider='codex',role='create')])
    barrier=threading.Barrier(2)
    def probe(*args):
        barrier.wait(timeout=10)
        return dict(identity={'codex':True},batches={})
    monkeypatch.setattr(wb,'_recovery_probes',probe)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:wb.changed_continuous_runs(),range(2)))
    assert sum(results,[])==[cid]
    assert list(wb._continuous_record(cid)['cursor']['wakeCounts'].values())==[1]


def test_quota_restop_preserves_spacing(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb;cid=run_record(env,eid)
    wb.clock=lambda:10000
    b=wb._recovery_block('run:story:create','quota',provider='codex',role='create')
    stop(wb,cid,[b]);assert wb.changed_continuous_runs(11800)==[cid]
    stop(wb,cid,[wb._recovery_block('run:story:create','quota',provider='codex',role='create')])
    assert wb._continuous_record(cid)['cursor']['blocks'][0]['lastTry']==11800
    assert wb.changed_continuous_runs(11830)==[]
    assert wb.changed_continuous_runs(13599)==[]
    assert wb.changed_continuous_runs(13600)==[cid]


def test_serial_temporary_stop_keeps_other_counts(tmp_path):
    env,eid,_=fixture(tmp_path);wb=env.wb;cid=run_record(env,eid)
    wb.initialize_asset_worksheet(eid,env.art(eid,'asset_design')['adopted_version_id'],'worksheet')
    x=wb._recovery_block('need:other','auth',provider='codex',role='image_assess')
    wb._continuous_cursor(cid,blocks=[x],wakeCounts={x['id']:2})
    rid=wb.create_task(eid,'story','起草故事',command_id='task')['runId']
    wb._image_state(cid,'bell',parkedQueue=[rid]);env.codex.script=[auth_failure]*3
    wait=wb._image_need_round(cid,wb._need(eid,'bell'),parallel=False)
    c=wb._continuous_record(cid)['cursor']
    assert c['blocks']==[x] and c['wakeCounts']=={x['id']:2} and 'pendingBlocks' not in c
    y=wb._recovery_block('need:bell',**wait)
    stop(wb,cid,[x,y])
    assert wb._continuous_record(cid)['cursor']['wakeCounts'][x['id']]==2


def test_parallel_need_adopts_without_review(tmp_path):
    env,eid,_=fixture(tmp_path);wb=env.wb;cid=run_record(env,eid)
    wb.initialize_asset_worksheet(eid,env.art(eid,'asset_design')['adopted_version_id'],'worksheet')
    wb._image_generate(cid,wb._need(eid,'bell'))
    before=len(env.codex.calls)
    assert wb._image_need_round(cid,wb._need(eid,'bell'),parallel=True) is None
    assert len(env.codex.calls)==before and wb._need(eid,'bell')['adopted_version_id']


def test_repeated_image_transport_losses_stop(tmp_path):
    env,eid,_=fixture(tmp_path);wb=env.wb;cid=run_record(env,eid)
    wb.initialize_asset_worksheet(eid,env.art(eid,'asset_design')['adopted_version_id'],'worksheet')
    env.images.script=['timeout']*10
    env.codex.script=[lambda req:dict(ids=[],reason='无适用图')]*10
    wait=wb._image_need_round(cid,wb._need(eid,'bell'))
    assert env.images.sent==1 and wait['kind'] in ('image_wait','transient')


def test_auth_switch_uses_new_executor(tmp_path,monkeypatch):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb
    env.codex.script=[auth_failure]*10
    result=wb.start_continuous(eid,command_id='start');cid=result['id']
    monkeypatch.setattr(wb,'executor_identity',lambda provider:dict(authenticated=False))
    monkeypatch.setattr(wb,'_stage_executors',lambda db,eid,stage:{'create':{'provider':'antigravity'}})
    assert wb.changed_continuous_runs()==[cid]
    wb._continuous_execute(cid)
    with env.db() as db:
        assert db.execute("SELECT 1 FROM run WHERE provider='antigravity' AND purpose='create' AND status='succeeded'").fetchone()
    assert not wb._continuous_record(cid)['cursor']['queue']


def test_restart_does_not_hold_items_from_an_older_stop(tmp_path):
    """Claude 复查：重启恢复不是“只认领部分受阻项”，旧停下留下的条目受阻项不能把条目挂起。"""
    import json
    from workbench.db import transaction
    from workbench.db import dumps
    from helpers import Env
    env=Env(tmp_path);pid,eid=env.new_episode()
    run=env.wb.start_continuous(eid,target_stage='stress',command_id='start')
    cursor=env.wb._continuous_record(run['id'])['cursor']
    cursor['blocks']=[dict(id='need:bell|auth|codex',scope='need:bell',kind='auth',provider='codex',role='image_assess',batchIds=[],resetsAt=None,at=1,lastTry=None)]
    cursor['resumeAfterRestart']=True
    with transaction(env.wb.db_path) as db:
        db.execute("UPDATE continuous_run SET state='running',pause_requested=0,cursor=? WHERE id=?",(dumps(cursor),run['id']))
        assert env.wb._claim_resume(db,run['id'],auto=[])==run['id']
    assert env.wb._continuous_record(run['id'])['cursor'].get('autoScope') is None


def test_future_quota_hint_cannot_postpone_deadline(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode();wb=env.wb
    b=wb._recovery_block('run:story:create','quota',resetsAt=wb.clock()+86400*365)
    cid=run_record(env,eid,'waiting_external',blocks=[b])
    assert not wb.changed_continuous_runs(b['at']+86401)
    assert wb._continuous_record(cid)['state']=='failed'


def test_unqueryable_submission_never_reissued_even_when_wait_expires(tmp_path):
    env,eid,cid,bid=parked_batch(tmp_path);wb=env.wb
    env.images.remote_status=False
    b=wb._recovery_block('need:bell','image_wait',batchIds=[bid])
    stop(wb,cid,[b])
    original=wb.batch_view(bid)['requests'][0]['id']
    for elapsed in (5,90,7201):
        assert wb.changed_continuous_runs(b['at']+elapsed)==[]
    req,=wb.batch_view(bid)['requests']
    assert req['id']==original and req['state']=='unknown' and env.images.sent==1
    assert wb._continuous_record(cid)['state']=='failed'


def test_transport_retry_only_draws_missing_item(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path)
    wb=env.wb
    assets=[dict(id=k,kind='prop',title=k,description=k,imagePrompt=k) for k in ('bell','lamp')]
    env.save_adopt(eid,'asset_design',dict(assets=assets),'two-props')
    cid=run_record(env,eid)
    submit=env.images.submit
    calls=[]
    def draw(req):
        calls.append(req['prompt'])
        if req['prompt']=='bell' and calls.count('bell')==1:
            return ImageOutcome('failed',error=TRANSPORT_FAILURE)
        return submit(req)
    monkeypatch.setattr(env.images,'submit',draw)
    monkeypatch.setattr(wb,'_image_fill_records',lambda cid:True)
    with pytest.raises(Halt):wb._continuous_images(cid)
    lamp=wb._need(eid,'lamp')['adopted_version_id']
    bell=wb._need(eid,'bell')
    assert lamp and not bell['adopted_version_id'] and bell['decision']!='text_only'
    blocked_at=wb._continuous_record(cid)['cursor']['blocks'][0]['at']
    assert wb.changed_continuous_runs(blocked_at+29)==[]
    assert wb.changed_continuous_runs(blocked_at+30)==[cid]
    assert wb._continuous_images(cid)
    assert wb._need(eid,'bell')['adopted_version_id'] and wb._need(eid,'lamp')['adopted_version_id']==lamp
    assert sorted(calls)==['bell','bell','lamp']


def test_quota_wait_honors_director_model_change_on_same_provider(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    _,eid=env.new_episode(plan={'create':{'provider':'codex','model':'original'},'review':{'provider':'codex'}})
    cid=run_record(env,eid,stage='story')
    env.codex.script=[lambda req:ExecOutcome(False,error_class='quota',quota_resets_at='in 12h')]
    rid=wb.create_task(eid,'story','写稿')['runId']
    with pytest.raises(Halt):
        wb._continuous_execute(cid,rid)
    assert wb.changed_continuous_runs()==[]
    wb.save_stage_executor(env.art(eid,'story')['id'],'create','codex','replacement')
    assert wb.changed_continuous_runs()==[cid]
    wb._continuous_execute(cid)
    assert [r.model for r in env.codex.calls]==['original','replacement']
