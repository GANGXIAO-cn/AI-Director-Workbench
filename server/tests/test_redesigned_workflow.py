"""08 owns performance and camera; 09 owns translation and its own content check."""
from helpers import Env
from workbench import workflow,productionnotes as pn
from workbench.sourcefiles import STAGES
from workbench.unifiedmethods import load


def test_active_order_and_loaded_methods():
    assert 'acting' not in workflow.ORDER
    assert workflow.NEXT['asset_design']=='plan'
    assert workflow.NEXT['plan']=='prompts'
    assert workflow.LABELS['plan']=='08 分镜'
    assert 'acting' not in workflow.DEPENDENCIES['plan']
    assert 'acting' not in workflow.DEPENDENCIES['prompts']
    assert 'plan' in STAGES['soundStandard']
    pack=load()
    text='\n'.join(pack['texts'][k] for k in pack['stages']['plan'])
    assert '同表完成的表演设计' in text and '动作中的表情' in text
    assert '本次写作内完成结果检查' in pack['texts']['prompts_contract']


def test_plan_can_start_without_independent_acting(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode()
    env.adopt_story(eid);env.script(eid)
    job=env.wb.create_task(eid,'plan','完成动作与摄影的统一分镜')
    entries=env.wb.run_manifest(job['runId'])['manifest']['inputs']
    assert not any(e['role']=='adopted_acting' for e in entries)
    env.drain(job['runId'])
    assert env.art(eid,'plan')['versions']


def test_unfinished_historical_acting_note_is_owned_by_plan_not_silently_completed():
    basis=dict(reportVersionId='r',notes=[dict(id='P1')],stages={s:{} for s in pn.STAGES})
    basis['stages']['asset_design']={'productionNoteResponses':[dict(id='P1',reportVersionId='r',owner='acting',status='assigned',reason='表情落实')]}
    status=pn.coverage_status('plan',dict(shots=[]),basis)
    assert status['owners']['P1']=='plan'
    assert status['missing']==['P1']
    content=dict(shots=[dict(id='s1')],productionNoteResponses=[dict(id='P1',reportVersionId='r',owner='plan',status='applied',whereIds=['s1'])])
    assert not pn.check('plan',content,basis,coverage=True)


def test_plan_review_uses_plan_method_and_real_images(tmp_path):
    from test_continuous_finishing import finishing_fixture
    env,case,cid,vid=finishing_fixture(tmp_path,True)
    art=env.art(case['eid'],'plan')
    run=env.wb.submit_review(art['adopted_version_id'],'plan-review')
    manifest=env.wb.run_manifest(run['runId'])['manifest']
    entries=env.wb.store.manifest_texts(manifest)
    assert any(e['name']=='plan_review.md' for e in entries)
    assert not any(e['name']=='story_review.md' for e in entries)
    assert any(e['role']=='image' for e in entries)
    assert any(e['role']=='material_use_conclusions' for e in entries)
    env.drain(run['runId'])
    with env.db() as db:
        result=db.execute('SELECT pass FROM review WHERE run_id=?',(run['runId'],)).fetchone()
        assert result and result[0]=='plan'
    assert env.wb.adoption_gate(art['adopted_version_id'])['review']


def new_flow_case(tmp_path):
    env=Env(tmp_path);pid,eid=env.new_episode()
    env.adopt_story(eid);env.script(eid)
    plan=env.plan86({'seg-01':[3000,2000],'seg-02':[3000,2000]})
    for shot in plan['shots']:
        shot.update(visibleRoles=['afu'],propStates={})
    env.save_adopt(eid,'plan',plan)
    img=env.image(pid,eid)
    env.wb.set_role_baseline(eid,'afu',img['versionId'],'柴犬阿福','baseline')
    for segment in ('seg-01','seg-02'):
        env.wb.set_cast(eid,segment,'afu','ok',img['versionId'])
    content=env.prompts_for(plan,img['versionId'])
    for sid,entry in content['segments'].items():
        entry['txt']='柴犬阿福。\n'+entry['txt']
        entry['refs'][0]['shots']=[s['id'] for s in plan['shots'] if s['segment']==sid]
    vid,_=env.save_adopt(eid,'prompts',content)
    return env,eid,plan,vid,content


def test_export_without_post_prompt_reviews(tmp_path):
    import json
    from pathlib import Path
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    with env.db() as db:
        assert not db.execute("SELECT 1 FROM run WHERE purpose IN ('cold_read','comparison')").fetchone()
    result=env.wb.deliver(eid,'trial','new-flow-export',segments=['seg-01','seg-02'])
    root=Path(result['path']);manifest=json.loads((root/'episode.json').read_text())
    assert manifest['workbench']['flow']=='storyboard-translation-v1'
    assert 'prompt_review' not in manifest
    assert manifest['workbench']['shotReferencePackets']
    assert env.wb.verify_delivery(result['deliveryId'])['ok']
    view=env.wb.episode_view(eid)
    assert view['productionStatus']['assets']
    assert view['reviewStatus']['state']=='未启用'
    assert view['promptStatus']['state']=='已采用'


def test_local_prompt_adoption_resolves_only_updated_segment_and_valid_joints(tmp_path):
    import copy
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    changed=copy.deepcopy(plan)
    changed['shots'][0]['action']='阿福抬起右爪，推开门'
    changed['shots'][2]['action']='阿福低头看抽屉'
    env.save_adopt(eid,'plan',changed,'new-plan')
    first=copy.deepcopy(content);first['segments']['seg-01']['txt']+='\n阿福抬起右爪，推开门。'
    v1,_=env.save_adopt(eid,'prompts',first,'first-only')
    with env.db() as db:
        pending={r[0] for r in db.execute("SELECT target_ref FROM impact WHERE episode_id=? AND state='pending'",(eid,))}
    assert 'prompts:seg-01' not in pending
    assert 'prompts:seg-02' in pending
    assert 'joint:seg-01-seg-02' in pending
    assert 'workflow:prompts' not in pending
    second=copy.deepcopy(first);second['segments']['seg-02']['txt']+='\n阿福低头看抽屉。'
    env.save_adopt(eid,'prompts',second,'second-only')
    with env.db() as db:
        assert not db.execute("SELECT 1 FROM impact WHERE episode_id=? AND kind='plan_change' AND state='pending'",(eid,)).fetchone()


def test_segment_state_change_and_hyphenated_joint_are_visible_to_delivery(tmp_path):
    import copy
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    changed=copy.deepcopy(plan)
    changed['segments'][0]['endState']='阿福停在门边'
    env.save_adopt(eid,'plan',changed,'state-change')
    problems,_=env.wb.delivery_problems(eid,'trial',segments=['seg-01','seg-02'])
    rechecks=[p for p in problems if p['stage']=='recheck']
    assert any(p.get('affectedSegments')==['seg-01'] for p in rechecks)
    assert any(p.get('affectedSegments')==['seg-01','seg-02'] for p in rechecks)


def test_retired_cli_defaults_and_new_runs_keep_antigravity_models(tmp_path):
    env=Env(tmp_path);wb=env.wb
    wb.put_setting('executor-defaults',{r:dict(provider='claude',model='sonnet') for r in ('create','review','expert')})
    assert {s['provider'] for s in wb.executor_defaults().values()}=={'codex'}
    assert 'claude' not in wb.model_catalog()['text']
    assert 'claude' not in wb.probe()
    slot=dict(provider='antigravity',model='claude-sonnet-4-5',effort=None)
    assert wb.active_selection(slot)==slot
    _,eid=env.new_episode(plan={r:dict(provider='claude') for r in ('create','review')})
    job=wb.create_task(eid,'story','生成故事')
    assert wb.run_manifest(job['runId'])['manifest']['provider']=='codex'


def test_retired_queued_cli_recovery_preserves_old_run(tmp_path):
    from workbench.db import transaction
    env=Env(tmp_path);wb=env.wb;_,eid=env.new_episode()
    job=wb.create_task(eid,'story','生成故事')
    with transaction(wb.db_path) as db:
        db.execute("UPDATE run SET provider='claude' WHERE id=?",(job['runId'],))
    next_runs=wb.execute_run(job['runId'])
    assert len(next_runs)==1
    env.drain(next_runs[0])
    with env.db() as db:
        old=db.execute('SELECT provider,status FROM run WHERE id=?',(job['runId'],)).fetchone()
        new=db.execute('SELECT provider,status FROM run WHERE id=?',(next_runs[0],)).fetchone()
    assert tuple(old)==('claude','stopped')
    assert tuple(new)==('codex','succeeded')


def test_shooting_segment_only_edit_does_not_invalidate_all_downstream(tmp_path):
    import copy
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    script_art=env.art(eid,'script')
    script=env.wb._continuous_version(script_art['adopted_version_id'])
    script['shootingSegments']=[dict(id=s['id'],order=s['order'],duration_ms=5000,action='对应原场动作') for s in plan['segments']]
    env.save_adopt(eid,'script',script,'segment-budget')
    with env.db() as db:
        pending=[tuple(r) for r in db.execute("SELECT kind,target_ref FROM impact WHERE state='pending' AND episode_id=?",(eid,))]
    assert ('script_change','plan:seg-01') in pending
    assert ('script_change','plan:seg-02') in pending
    assert not any(kind=='workflow_change' for kind,target in pending)
    changed=copy.deepcopy(plan);changed['shots'][0]['action']='同步第一段动作'
    env.save_adopt(eid,'plan',changed,'sync-one')
    with env.db() as db:
        pending={r[0] for r in db.execute("SELECT target_ref FROM impact WHERE state='pending' AND kind='script_change' AND episode_id=?",(eid,))}
    assert pending=={'plan:seg-02'}


def test_expert_plan_minor_note_does_not_create_another_revision(tmp_path):
    from workbench.executors.fake import review
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    art=env.art(eid,'plan');plan_id=art['adopted_version_id']
    env.codex.script=[lambda request:review(request,'return',[dict(reason='表情文字可略精简')])]
    job=env.wb.submit_review(plan_id,'minor-note');env.drain(job['runId'])
    version=next(v for v in env.art(eid,'plan')['versions'] if v['id']==plan_id)
    calls=len(env.codex.calls)
    assert env.wb._expert_text_review(dict(id='test'),eid,'plan',version,None) is False
    assert len(env.codex.calls)==calls
    assert not env.wb.adoption_gate(plan_id)['unresolved']


def test_prompt_writer_receives_windows_not_concatenated_final_prose(tmp_path):
    import json
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    job=env.wb.create_task(eid,'prompts','按分镜撰写自然正文',prompt_images=True)
    manifest=env.wb.run_manifest(job['runId'])['manifest']
    entries=env.wb.store.manifest_texts(manifest)
    template=next(e['text'] for e in entries if e['role']=='method' and e['name']=='prompt-format.md')
    assert '## flex五栏' in template and '## grid' in template
    scaffold=json.loads(next(e['text'] for e in entries if e['role']=='prompt_scaffold'))
    for entry in scaffold.values():
        assert 'txt' not in entry
        assert len(entry['timeWindows'])==2
        assert entry['refs'] and 'fixedSections' in entry
        assert '不拼接字段' in entry['blocks']['【镜头与动作】']


def test_old_prompt_review_queue_is_retired_without_model_call(tmp_path):
    from test_continuous_finishing import finishing_fixture
    env,case,cid,vid=finishing_fixture(tmp_path,True)
    job=env.wb.start_prompt_review(vid,'G01','cold_read','old-queued-review')
    before=len(env.codex.calls)
    env.wb._continuous_update(cid,state='running')
    env.wb._continuous_execute(cid,job['runId'])
    with env.db() as db:
        assert db.execute('SELECT status FROM run WHERE id=?',(job['runId'],)).fetchone()[0]=='stopped'
    assert len(env.codex.calls)==before
    assert not env.wb._continuous_record(cid)['cursor']['queue']


def test_registered_episode_identity_is_visible_and_reused_by_07(tmp_path):
    import json
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    with env.db() as db:
        pid=db.execute('SELECT project_id FROM episode WHERE id=?',(eid,)).fetchone()[0]
        baseline=db.execute("SELECT asset_version_id FROM episode_role_baseline WHERE episode_id=? AND role='afu'",(eid,)).fetchone()[0]
        entries=env.wb._extra_creation_inputs(db,dict(id='inventory',stage='asset_design',episode_id=eid,project_id=pid))
    inventory=json.loads(next(e['text'] for e in entries if e['role']=='asset_inventory'))
    assert any(r['roleId']=='afu' and r['assetVersionId']==baseline for r in inventory['fixed'])
    design=dict(strategy='lightweight',assets=[dict(id='identity',title='阿福身份',kind='character',roleId='afu',stateId='default',description='复用本集已登记身份图',imagePrompt='',evidenceStatus='verified',dependsOn=[])])
    env.save_adopt(eid,'asset_design',design,'identity-reuse')
    with env.db() as db:
        row=db.execute("SELECT decision,adopted_version_id,generation_requested FROM asset_need WHERE episode_id=? AND need_key='identity'",(eid,)).fetchone()
    assert tuple(row)==('reuse',baseline,0)
    with env.db() as db:
        appearance=db.execute('SELECT appearance FROM episode_role_baseline WHERE episode_id=?',(eid,)).fetchone()[0]
    assert env.wb.cast_matrix(eid)['roster']['afu']['appearance']==appearance
    # An inventory description is not a replacement for the registered identity.
    run=env.wb.start_continuous(eid,command_id='identity-resume')
    env.wb._image_fill_records(run['id'])
    with env.db() as db:
        assert db.execute('SELECT appearance FROM episode_role_baseline WHERE episode_id=?',(eid,)).fetchone()[0]==appearance


def test_native_request_metadata_does_not_require_model_rewrite(tmp_path):
    from workbench.executors.fake import draft
    env=Env(tmp_path);_,eid=env.new_episode()
    env.codex.binds_request_context=True
    def typo(request):
        result=draft(request);result.update(runId='mistyped-run',inputFingerprint='mistyped-hash')
        return result
    env.codex.script=[typo]
    job=env.wb.create_task(eid,'story','写故事')
    env.drain(job['runId'])
    assert len(env.codex.calls)==1
    with env.db() as db:
        assert db.execute('SELECT status FROM run WHERE id=?',(job['runId'],)).fetchone()[0]=='succeeded'


def test_plan_review_includes_segment_reference_images_without_required_mark(tmp_path):
    env,eid,plan,vid,content=new_flow_case(tmp_path)
    with env.db() as db:pid=db.execute('SELECT project_id FROM episode WHERE id=?',(eid,)).fetchone()[0]
    prop=env.image(pid,eid,'实际道具参考',category='prop',rgb=(40,50,60))
    plan['segments'][0]['referenceImages']=[dict(imageId=prop['versionId'],purpose='道具外观与落地关系')]
    plan_id,_=env.save_adopt(eid,'plan',plan,'explicit-reference')
    job=env.wb.submit_review(plan_id,'check-actual-references')
    entries=env.wb.run_manifest(job['runId'])['manifest']['inputs']
    assert any(e.get('role')=='image' and prop['versionId'] in e['name'] for e in entries)
