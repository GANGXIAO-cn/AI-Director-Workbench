"""Real run ingestion and small fixture checks for 09 material continuation."""
import json
import pytest
from helpers import Env
from test_continuous_finishing import finishing_fixture
from test_image_assess import reply, assessment
from workbench.db import dumps
from workbench.errors import Conflict
from workbench import productionnotes, promptdraft


def valid_answer(value):
    return [] if isinstance(value,dict) and value.get('value')==1 else ['value 必须为 1']


@pytest.mark.parametrize('provider',['codex','antigravity'])
def test_bounded_prompt_protocol_cache_and_no_version(tmp_path,provider):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid']
    wb.save_episode_expert(eid,provider)
    executor=env.codex if provider=='codex' else env.ag
    executor.strict_schema=provider=='codex'
    executor.script=[reply(dict(value=1))] if provider=='codex' else [lambda req:dict(kind='discussion',reply='{"value":1}',directorRequest='',fields={})]
    count=len(env.art(eid,'plan')['versions'])
    data=dict(shots=[dict(id='ONLY_ONCE',action='开门')])
    answer,basis=wb._continuous_bounded_json(cid,'test','one','reply 内填写 {"value":1}',data,valid_answer)
    calls=len(executor.calls)
    assert wb._continuous_bounded_json(cid,'test','one','reply 内填写 {"value":1}',data,valid_answer)==(answer,basis)
    assert len(executor.calls)==calls and len(env.art(eid,'plan')['versions'])==count
    req=executor.calls[-1];manifest=wb.run_manifest(req.run_id)['manifest']
    body_chars=sum(len(wb.store.read_blob(i['sha256']).decode()) for i in manifest['inputs'])
    print(f'BOUNDED_METRIC {provider} body_chars={body_chars} prompt_chars={len(req.prompt)}')
    assert {i['role'] for i in manifest['inputs']}=={'judgment_materials','judgment_instruction'}
    assert req.prompt.count('ONLY_ONCE')==1 and 'kind=draft' not in req.prompt and '可写字段' not in req.prompt
    if provider=='codex':
        assert req.schema['properties']['kind']['enum']==['discussion']
        assert '"changes": []' in req.prompt
    else:assert req.schema is None and '"fields": {}' in req.prompt and 'changes' not in req.prompt
    with env.db() as db:
        msg=db.execute("SELECT payload FROM message WHERE run_id=? AND role='assistant'",(req.run_id,)).fetchone()
    assert json.loads(msg[0])['internalJudgment']=='test'


def test_business_retry_has_previous_answer_specific_error_and_same_materials(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb
    wb.save_episode_expert(case['eid'],'codex')
    env.codex.script=[reply(dict(value=9)),reply(dict(value=1))]
    answer,_=wb._continuous_bounded_json(cid,'test','one','填写 value',dict(shots=['last-shot']),valid_answer)
    assert answer==dict(value=1)
    assert '"value": 9' in env.codex.calls[-1].prompt and 'value 必须为 1' in env.codex.calls[-1].prompt
    assert env.codex.calls[-1].prompt.count('last-shot')==1
    receipt=wb._continuous_record(cid)['cursor']['judgmentReceipts']['test:one']
    assert len(receipt['attempts'])==2 and receipt['attempts'][0]['problems']


def test_binding_updates_existing_without_erasing_director_or_other_source(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid'];vid=case['img']['versionId']
    plan=env.art(eid,'plan')['adopted_version_id']
    wb.mark_image(eid,vid,'required',shot_ids=['S01'],requirement=dict(reason='导演指定',targetSegments=['G01']))
    original=wb._continuous_image_rule(eid,vid)
    wb._continuous_bind(cid,vid,['S02'],plan,'material','b1','参考',original)
    first=wb._continuous_image_rule(eid,vid)
    assert first['shotIds']==['S01','S02'] and first['selectedBy']=='user'
    wb._continuous_bind(cid,vid,['S03'],plan,'product:p','b2','入景',first)
    wb._continuous_bind(cid,vid,['S03'],plan,'material','b3','更新',wb._continuous_image_rule(eid,vid))
    rule=wb._continuous_image_rule(eid,vid)
    assert rule['shotIds']==['S01','S03'] and set(rule['targetSegments'])=={'G01','G02'}
    with pytest.raises(Conflict):wb._continuous_bind(cid,vid,['S02'],plan,'material','b4','过时',original)
    wb.mark_image(eid,vid,'required')
    assert wb._continuous_image_rule(eid,vid)==rule
    with env.db() as db:assert db.execute("SELECT count(*) FROM image_choice WHERE asset_version_id=? AND mark='required'",(vid,)).fetchone()[0]==1


def test_explicit_mark_updates_shots_omission_preserves(tmp_path):
    env,case,_,_=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid'];vid=case['img']['versionId']
    wb.mark_image(eid,vid,'required');wb.mark_image(eid,vid,'required',shot_ids=['S02'])
    wb.mark_image(eid,vid,'required')
    assert wb._continuous_image_rule(eid,vid)['shotIds']==['S02']


def test_note_projection_keeps_notes_and_responses_without_stage_bodies(tmp_path):
    env,case,_,_=finishing_fixture(tmp_path)
    with env.db() as db:
        full=productionnotes.context(db,case['eid']);small=productionnotes.model_context(db,case['eid'])
    assert 'stages' in full and 'stages' not in small
    assert full['notes']==small['notes'] and 'shots' not in dumps(small)


def test_required_scene_gets_shot_scope_and_reuses_without_call(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path,True);wb=env.wb;eid=case['eid']
    image=env.image(case['pid'],eid,name='街景',category='scene',rgb=(50,60,70))['versionId']
    wb.mark_image(eid,image,'required',requirement=dict(reason='街景参考',targetSegments=['G01']))
    with env.db() as db:
        r=db.execute('SELECT d.* FROM required_image_details d JOIN image_choice c ON c.id=d.choice_id WHERE c.asset_version_id=?',(image,)).fetchone()
        body=json.loads(r['details']);body['selectedBy']='agent'
        db.execute('UPDATE required_image_details SET details=? WHERE choice_id=?',(dumps(body),r['choice_id']))
    wb.save_episode_expert(eid,'codex')
    env.codex.script=[reply(dict(bindings=[dict(needKey=image,versionId=image,shotIds=['S01'],reason='场景参考',unresolvedReason='')]))]
    wb._continuous_required_bindings(cid)
    calls=len(env.codex.calls)
    wb._continuous_required_bindings(cid)
    assert len(env.codex.calls)==calls
    assert wb._continuous_image_rule(eid,image)['shotIds']==['S01']
    result=promptdraft.draft(wb,eid)
    assert next(r for r in result['segments']['G01']['refs'] if r['assetVersionId']==image)['shots']==['S01']


def test_prop_alignment_preserves_observed_state_and_full_segment_conflict(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path,True);wb=env.wb
    wb.save_episode_expert(case['eid'],'codex')
    image=case['img']['versionId'];raw=assessment(image,props={'door-old':'关闭'})
    shots=[dict(id='S01',propStates={'door':'关闭'},visibleRoles=[]),dict(id='S02',propStates={'door':'打开'},visibleRoles=[])]
    env.codex.script=[reply(dict(mappings=[dict(source='door-old',target='door',mode='state',reason='观察与镜头均为同一扇门')],combined=False,otherUses={}))]
    props,_,_=wb._continuous_reference_props(cid,'G01',dict(shots=['S01']),shots,raw)
    assert props=={'door':'关闭'} and raw['props']=={'door-old':'关闭'}
    from workbench.shotrefs import validate_scope,Shot
    with pytest.raises(ValueError,match='状态冲突'):
        validate_scope(dict(id='G01',identity_bindings={}),dict(shots=['S01']),dict(id='img',type='image',category='scene'),
                       {'S01':Shot(props={'door':{'关闭'}}),'S02':Shot(props={'door':{'打开'}})}, {},dict(prominent_props=props))


def product_fixture(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path,config='puggy');wb=env.wb;eid=case['eid']
    wb.use_project_product(eid,'15','product')
    image=env.image(case['pid'],eid,name='椅背靠垫场景',category='scene',rgb=(100,120,130))['versionId']
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id='product-scene',kind='scene',title='椅背靠垫场景',description='椅背有靠垫',imagePrompt='椅背有靠垫',sceneIds=[],productCode='15')]),'product-design')
    with env.db() as db:
        db.execute("UPDATE asset_need SET adopted_version_id=? WHERE episode_id=? AND need_key='product-scene'",(image,eid))
        cursor=wb._continuous_record(cid)['cursor'];wb._continuous_reconcile(db,eid,cursor)
        db.execute('UPDATE continuous_run SET cursor=? WHERE id=?',(dumps(cursor),cid))
    wb.save_episode_expert(eid,'codex')
    return env,case,cid,image


def test_existing_product_record_repairs_lost_binding_without_model(tmp_path):
    env,case,cid,image=product_fixture(tmp_path);wb=env.wb;eid=case['eid']
    wb.save_product_placement(eid,'15','S01','靠垫朝外',image,'椅背可见靠垫','placement')
    wb.mark_image(eid,image,'required')
    before=len(env.codex.calls)
    wb._continuous_products(cid);wb._continuous_products(cid)
    assert len(env.codex.calls)==before
    assert wb._continuous_image_rule(eid,image)['shotIds']==['S01']


def test_product_judgment_persists_before_write_and_resumes_without_call(tmp_path,monkeypatch):
    env,case,cid,image=product_fixture(tmp_path);wb=env.wb;eid=case['eid']
    monkeypatch.setattr(wb,'_continuous_existing_assessment',lambda e,v:assessment(v,props={},observation='场景椅背有靠垫'))
    env.codex.script=[reply(dict(placements=[dict(code='15',shotId='S01',intent='靠垫朝外')],unresolvedReason=''))]
    original=wb.save_product_placement
    def crash(*args,**kwargs):raise RuntimeError('simulated write interruption')
    monkeypatch.setattr(wb,'save_product_placement',crash)
    with pytest.raises(RuntimeError,match='interruption'):wb._continuous_products(cid)
    calls=len(env.codex.calls)
    monkeypatch.setattr(wb,'save_product_placement',original)
    wb._continuous_products(cid);wb._continuous_products(cid)
    assert len(env.codex.calls)==calls
    assert wb._continuous_image_rule(eid,wb.use_project_product(eid,'15','product')['versionId'])['shotIds']==['S01']
    with env.db() as db:assert db.execute('SELECT count(*) FROM product_placement WHERE episode_id=?',(eid,)).fetchone()[0]==1


def test_reconcile_preserves_receipts_relevant_input_change_rejudges(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid']
    wb.save_episode_expert(eid,'codex');env.codex.script=[reply(dict(value=1)),reply(dict(value=1))]
    wb._continuous_bounded_json(cid,'test','one','value=1',dict(scope=1),valid_answer)
    cursor=wb._continuous_record(cid)['cursor'];receipt=cursor['judgmentReceipts']
    with env.db() as db:
        wb._continuous_reconcile(db,eid,cursor)
        assert cursor['judgmentReceipts']==receipt
        db.execute('UPDATE continuous_run SET cursor=? WHERE id=?',(dumps(cursor),cid))
    count=len(env.codex.calls)
    wb._continuous_bounded_json(cid,'test','one','value=1',dict(scope=1),valid_answer)
    assert len(env.codex.calls)==count
    wb._continuous_bounded_json(cid,'test','one','value=1',dict(scope=2),valid_answer)
    assert len(env.codex.calls)==count+1


def test_combined_identity_extra_scope_requires_per_shot_prop(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid'];wb.save_episode_expert(eid,'codex')
    image=case['img']['versionId']
    shots=[dict(id='S01',visibleRoles=['cat'],propStates={'car':'停放'}),dict(id='S02',visibleRoles=[],propStates={'car':'停放'})]
    env.codex.script=[reply(dict(mappings=[dict(source='car',target='car',mode='state',reason='图内车辆供两个镜头参考')],combined=True,otherUses={'S02':'car'}))]
    props,combined,other=wb._continuous_reference_props(cid,'G01',dict(shots=['S01','S02']),shots,assessment(image,props={'car':'停放'}),['cat'])
    from workbench.shotrefs import validate_scope,Shot
    validate_scope(dict(id='G01',identity_bindings={'cat':'img'}),dict(shots=['S01','S02']),dict(id='img',type='image',category='character'),
        {'S01':Shot(roles={'cat'},props={'car':{'停放'}}),'S02':Shot(props={'car':{'停放'}})}, {'img':'cat'},dict(prominent_props=props,combined=combined,other_uses=other))


def test_partial_product_write_does_not_mistake_subset_for_complete_history(tmp_path,monkeypatch):
    env,case,cid,image=product_fixture(tmp_path);wb=env.wb;eid=case['eid']
    monkeypatch.setattr(wb,'_continuous_existing_assessment',lambda e,v:assessment(v,props={}))
    records=[dict(code='15',shotId=s,intent='靠垫朝外') for s in ['S01','S02']]
    env.codex.script=[reply(dict(placements=records,unresolvedReason=''))]
    original=wb.save_product_placement;calls=[]
    def crash(*args,**kwargs):
        calls.append(args[2])
        if len(calls)==2:raise RuntimeError('second write interrupted')
        return original(*args,**kwargs)
    monkeypatch.setattr(wb,'save_product_placement',crash)
    with pytest.raises(RuntimeError):wb._continuous_products(cid)
    count=len(env.codex.calls)
    monkeypatch.setattr(wb,'save_product_placement',original)
    wb._continuous_products(cid)
    assert len(env.codex.calls)==count
    assert wb._continuous_image_rule(eid,wb.use_project_product(eid,'15','product')['versionId'])['shotIds']==['S01','S02']


def test_new_required_binding_invalidates_old_prompt_receipt(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid']
    image=env.image(case['pid'],eid,name='新场景',category='scene',rgb=(80,90,100))['versionId']
    wb.mark_image(eid,image,'required',shot_ids=['S02'])
    assert wb._continuous_reference_gaps(eid,wb._continuous_version(vid))=={'G01':[dict(versionId=image,name='新场景',shotIds=['S02'],reason='')]}
    content=wb._continuous_version(vid)
    content['segments']['G01']['refs'].append(dict(kind='image',assetVersionId=image,shots=['S02']))
    assert not wb._continuous_reference_gaps(eid,content)


def test_manual_resume_retries_failed_business_reply_but_not_success(tmp_path):
    from workbench.continuous import Halt
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;wb.save_episode_expert(case['eid'],'codex')
    env.codex.script=[reply(dict(value=0)),reply(dict(value=2)),reply(dict(value=1))]
    from workbench.continuous import UserChange
    with pytest.raises(UserChange):wb._continuous_bounded_json(cid,'test','one','value=1',{},valid_answer)
    assert wb._continuous_record(cid)['state']=='running'
    answer,_=wb._continuous_bounded_json(cid,'test','one','value=1',{},valid_answer)
    assert answer==dict(value=1)
    assert len(wb._continuous_record(cid)['cursor']['judgmentReceipts']['test:one']['attempts'])==1


def test_overlapping_ai_uses_can_change_without_losing_director_scope(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid'];vid=case['img']['versionId'];plan=env.art(eid,'plan')['adopted_version_id']
    wb.mark_image(eid,vid,'required',shot_ids=['S01'])
    for source,basis,shots in [('material','a',['S02']),('product:p','b',['S02']),('material','c',['S03']),('product:p','d',['S03'])]:
        wb._continuous_bind(cid,vid,shots,plan,source,basis,'测试用途',wb._continuous_image_rule(eid,vid))
    assert wb._continuous_image_rule(eid,vid)['shotIds']==['S01','S03']


def test_identity_accessories_keep_evidence_and_inherit_use(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb
    wb.save_episode_expert(case['eid'],'codex')
    raw=assessment(case['img']['versionId'],props={'necklace':'金色','shoulder-bag':'肩上'})
    shots=[dict(id='S01',visibleRoles=['woman'],action='双手送毛巾',propStates={'towel':'折叠'})]
    answer=dict(mappings=[dict(source=k,target='',mode='background',reason='仅供身份，不借用附带物状态') for k in raw['props']],combined=False,otherUses={})
    env.codex.script=[reply(answer)]
    result=wb._continuous_reference_props(cid,'G01',dict(shots=['S01']),shots,raw,['woman'],prompt_text='双手送毛巾',usage_conclusion=dict(action='continue',conflicts=[]))
    assert result==({},False,None)
    assert raw['props']=={'necklace':'金色','shoulder-bag':'肩上'}
    req=env.codex.calls[-1]
    texts=wb.store.manifest_texts(wb.run_manifest(req.run_id)['manifest'])
    materials=json.loads(next(x['text'] for x in texts if x['role']=='judgment_materials'))
    assert materials['usedPropIds']==['towel'] and materials['promptText']=='双手送毛巾'
    assert materials['usageConclusion']['action']=='continue'
    with env.db() as db:
        assert db.execute('SELECT t.stage FROM task t JOIN run r ON r.task_id=t.id WHERE r.id=?',(req.run_id,)).fetchone()[0]=='prompts'
    count=len(env.codex.calls)
    wb._continuous_reference_props(cid,'G01',dict(shots=['S01']),shots,raw,['woman'],prompt_text='双手送毛巾',usage_conclusion=dict(action='continue',conflicts=[]))
    assert len(env.codex.calls)==count


def test_registered_prop_cannot_be_hidden_as_accessory(tmp_path):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;wb.save_episode_expert(case['eid'],'codex')
    raw=assessment(case['img']['versionId'],props={'bag':'肩上'})
    shots=[dict(id='S01',visibleRoles=['woman'],action='拿起包',propStates={'bag':'手中'})]
    env.codex.script=[reply(dict(mappings=[dict(source='bag',target='',mode='background',reason='附带物')],combined=False,otherUses={})),
                      reply(dict(mappings=[dict(source='bag',target='bag',mode='state',reason='动作道具')],combined=False,otherUses={}))]
    result=wb._continuous_reference_props(cid,'G01',dict(shots=['S01']),shots,raw,['woman'])
    assert result[0]=={'bag':'肩上'}
    assert '不能将当前镜头使用的道具隐藏为背景' in env.codex.calls[-1].prompt


def test_missing_action_prop_is_business_issue_not_format_retry(tmp_path):
    from workbench.continuous import Halt
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;wb.save_episode_expert(case['eid'],'codex')
    raw=assessment(case['img']['versionId'],props={'bag':'肩上'})
    shots=[dict(id='S01',visibleRoles=['woman'],action='拿起包',propStates={})]
    env.codex.script=[reply(dict(mappings=[dict(source='bag',target='',mode='unresolved',reason='正文用到包但未登记',shotId='S01',quote='拿起包')],combined=False,otherUses={}))]
    before=len(env.codex.calls)
    from workbench.continuous import UserChange
    with pytest.raises(UserChange):wb._continuous_reference_props(cid,'G01',dict(shots=['S01']),shots,raw,['woman'])
    assert len(env.codex.calls)==before+1
    assert '拿起包' in wb._continuous_record(cid)['reason']


def test_releasing_optional_art_preserves_other_shot_bindings(tmp_path):
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid, vid = env.wb, case['eid'], case['img']['versionId']
    plan_id = env.art(eid, 'plan')['adopted_version_id']
    wb._continuous_bind(cid, vid, ['S01'], plan_id, 'scene', 'scene', '空镜', None)
    wb._continuous_bind(cid, vid, ['S03'], plan_id, 'worksheet', 'art', '场景延续',
                        wb._continuous_image_rule(eid, vid))
    wb.bind_required_image(eid, vid, [], plan_id, 'worksheet', 'revision', '分镜已修订',
                           wb._continuous_image_rule(eid, vid))
    rule = wb._continuous_image_rule(eid, vid)
    assert rule['shotIds'] == ['S01'] and rule['targetSegments'] == ['G01']
    assert set(rule['autoBindings']) == {'scene'}
