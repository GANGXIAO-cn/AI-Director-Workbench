"""H orchestration using real batch receipts and simulated images/models."""
import json,re
import pytest

@pytest.fixture(autouse=True)
def material_step_only(monkeypatch):
    from workbench.continuous import ContinuousMixin
    monkeypatch.setattr(ContinuousMixin,"_continuous_finish_stages",lambda self,cid:False)

def expert_provisional(req):
    data=json.loads(req.prompt.split("以下是资料：\n")[1].split("\n上次回包")[0])
    candidate=data["candidates"][0]
    return dict(action="provisional",target=data["target"],versionId=candidate["versionId"],issueKeys=[i["type"]+":"+i["where"] for i in candidate["critical"]],instruction="",reason="返修后仍有问题",summary="暂用当前候选，问题保留到交付。")

from test_image_assess import fixture,assessment,reply


def library_pick(take=True):
    """实测 135：库图先按名称与用途挑选；take=False 表示图库里没有适用图。"""
    def answer(req):
        ids=[e['id'] for e in json.loads(req.prompt.split('\n',1)[1])['library']]
        return dict(ids=ids if take else [],reason='同一物件' if take else '图库没有适用图')
    return answer


def with_pick(handler,take=True):
    pick=library_pick(take)
    return lambda req:pick(req) if req.purpose=='progress_expert' and '项目图库' in req.prompt else handler(req)


def image_reply(req,verdict='usable',required=False):
    ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
    return reply([assessment(vid,verdict=verdict,recommendRequired=required,critical=[dict(type='usage',where='支撑',text='关键支撑悬空')] if verdict=='blocked' else [],minor=[dict(where='远景',text='轻微颜色差异')] if verdict=='limited' else []) for vid in ids])(req)


def test_H_T4_generated_usable_adopted_and_agent_required(tmp_path):
    env,eid,_=fixture(tmp_path);env.codex.script=[lambda req:image_reply(req,required=True)]*8
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    need=env.wb.asset_worksheet(eid)['rows'][0]
    assert need['adopted_version_id'],result['reason']
    assert len(env.wb.episode_batches(eid))==1
    from workbench.imagechoices import requirements
    with env.db() as db:rules=requirements(db,eid)
    assert len(rules)==1 and rules[0]['selectedBy']=='agent'
    assert any(d['kind']=='choose_image' for d in env.wb.continuous_view(eid)['decisions'])


def test_H_T7_minor_limited_adopted_without_card(tmp_path):
    env,eid,_=fixture(tmp_path);env.codex.script=[lambda req:image_reply(req,'limited')]*8
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id'],result['reason']
    assert not result['choice'] and len(env.wb.episode_batches(eid))==1
    assert env.wb.image_assessments(eid)[0]['body']['minor']


def test_H_T6_two_failed_rounds_change_method_and_show_failures(tmp_path):
    env,eid,_=fixture(tmp_path);env.codex.script=[lambda req:expert_provisional(req) if req.purpose=='progress_expert' else image_reply(req,'blocked')]*24
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert result['choice'] is None
    assert env.wb.material_issues(eid)[0]['state']=='provisional'
    attempts=result['cursor']['images']['bell']['attempts']
    assert [r['method'] for r in attempts]==['首次生成','常规重出']
    assert len(env.wb.episode_batches(eid))==2
    assert env.wb.material_issues(eid)[0]['issue_refs']==['usage:支撑']


def key_fixture(tmp_path):
    env,eid,vid=fixture(tmp_path)
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id='room',kind='scene',title='主场景',description='木门门厅空景',imagePrompt='木门门厅空景',sceneIds=['场01'],keyVisual=True)]),'key-design')
    return env,eid


def test_H_T5_key_visual_waits_for_two_then_user_adopts(tmp_path):
    env,eid=key_fixture(tmp_path);env.codex.script=[image_reply]*12
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert result['choice'] is None
    need=env.wb.asset_worksheet(eid)['rows'][0]
    assert need['adopted_version_id']
    assert len(env.wb.episode_batches(eid))==1
    assert not any(call.purpose=='progress_expert' for call in env.codex.calls)


def test_H_T6b_user_acceptance_keeps_blocked_assessment_and_issues(tmp_path):
    env,eid,_=fixture(tmp_path);env.codex.script=[lambda req:expert_provisional(req) if req.purpose=='progress_expert' else image_reply(req,'blocked')]*24
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    issue=env.wb.material_issues(eid)[0];chosen=issue['version_id']
    env.wb.accept_material_issue(issue['id'],command_id='accept')
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']==chosen
    assert next(a for a in env.wb.image_assessments(eid) if a['version_id']==chosen)['verdict']=='blocked'
    with env.db() as db:
        issue=db.execute('SELECT * FROM accepted_image_issue WHERE asset_version_id=?',(chosen,)).fetchone()
        assert issue['issue_key']=='usage:支撑'
        decision=db.execute('SELECT detail FROM ai_decision WHERE id=?',(issue['decision_id'],)).fetchone()
    assert json.loads(decision[0])['userDirection']['instruction']=='用这张，保留这个问题'


def test_H_channel_inherits_existing_07_selection_and_defaults(tmp_path):
    env,eid,_=fixture(tmp_path)
    channels=env.wb.image_providers();selected=list(channels)[-1]
    options=env.wb._image_options(eid,dict(provider=selected))
    assert options['provider']==selected
    assert env.wb._image_options(eid,{})['provider']==next(iter(channels))
    assert env.wb._image_options(eid,dict(provider='removed',model='removed'))==env.wb._image_options(eid,{})
    env.codex.script=[image_reply]*12
    result=env.wb.start_continuous(eid,target_stage='delivery',image_options=dict(provider=selected),command_id='start')
    assert result['cursor']['imageOptions']==dict(provider=selected)
    assert env.wb.episode_batches(eid)[0]['provider']==selected


def test_H_T10_pause_after_receipt_resume_does_not_duplicate_batch(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path);env.codex.script=[image_reply]*12
    execute=env.wb.execute_batch
    def pause_after_receipt(batch_id):
        result=execute(batch_id)
        env.wb.pause_continuous(env.wb.continuous_view(eid)['run']['id'])
        return result
    monkeypatch.setattr(env.wb,'execute_batch',pause_after_receipt)
    paused=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert paused['state']=='paused'
    batches=env.wb.episode_batches(eid);assert len(batches)==1 and batches[0]['state']=='completed'
    monkeypatch.setattr(env.wb,'execute_batch',execute)
    result=env.wb.resume_continuous(paused['id'],command_id='resume')
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id'],result['reason']
    assert [b['id'] for b in env.wb.episode_batches(eid)]==[b['id'] for b in batches]


def test_H_T11_need_changed_during_submission_retains_candidate_and_reassesses(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path);env.codex.script=[image_reply]*12
    execute=env.wb.execute_batch
    def edit_during_submission(batch_id):
        need=env.wb.asset_worksheet(eid)['rows'][0]
        env.wb.set_asset_need(eid,'bell',need['revision'],'edit_prompt','user-change',prompt='门铃必须在右边，按新用途重新核对')
        result=execute(batch_id)
        current=env.wb.asset_worksheet(eid)['rows'][0]
        assert current['candidates'] and not current['adopted_version_id']
        return result
    monkeypatch.setattr(env.wb,'execute_batch',edit_during_submission)
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id'],result['reason']
    assert any('门铃必须在右边' in req.prompt for req in env.codex.calls if req.purpose=='image_assess')
    assert any(d['kind']=='apply_user_change' and '候选' in d['reason'] for d in env.wb.continuous_view(eid)['decisions'])


import pytest
@pytest.mark.parametrize('verdict',['limited','blocked'])
def test_H_T9b_space_uses_adopted_limited_or_accepted_without_regeneration(tmp_path,verdict):
    env,eid=key_fixture(tmp_path)
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id='room',kind='scene',title='主场景',description='木门门厅空景',imagePrompt='木门门厅空景',sceneIds=['场01'])]),'nonkey')
    lock=env.wb.add_space_item(eid,'门厅左右关系','门在左侧','剧本',['场01'],scope='scenes',command_id='lock')['itemId']
    def response(req):
        if req.purpose=='progress_expert':return expert_provisional(req)
        if req.purpose=='image_assess':return image_reply(req,verdict)
        vid=env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']
        return reply(dict(versionId=vid,reason='已采用图符合门厅空间，已知差异保留'))(req)
    env.codex.script=[response]*24
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    locked=next(i for i in env.wb.space_locks(eid) if i['id']==lock)
    assert locked['status']=='confirmed',result['reason']
    assert locked['versionId']==env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']
    assert len(env.wb.episode_batches(eid))==(1 if verdict=='limited' else 2)
    assert any(d['kind']=='confirm_lock' for d in env.wb.continuous_view(eid)['decisions'])
    assert next(a for a in env.wb.image_assessments(eid) if a['version_id']==locked['versionId'])['verdict']==verdict


def test_H_T3_library_usable_is_reused_and_logged(tmp_path):
    from workbench.db import transaction,new_id
    env,eid,vid=fixture(tmp_path);pid=env.wb.episode_view(eid)['episode']['project_id']
    with transaction(env.wb.db_path) as db:
        db.execute('INSERT INTO library_entry(id,project_id,asset_version_id,usage,status,created_at) VALUES(?,?,?,?,?,?)',(new_id(),pid,vid,'门铃','approved',env.wb.clock()))
    env.codex.script=[with_pick(image_reply)]*12
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    need=env.wb.asset_worksheet(eid)['rows'][0]
    assert need['decision']=='reuse' and need['adopted_version_id'],result['reason']
    assert not env.wb.episode_batches(eid)
    assert any(d['kind']=='choose_image' for d in env.wb.continuous_view(eid)['decisions'])


def test_H_T7b_key_visual_single_has_extra_only_once(tmp_path):
    env,eid=key_fixture(tmp_path);first=[]
    def one_good(req):
        ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
        if not first:first.append(ids[0])
        return reply([assessment(vid,verdict='usable' if vid==first[0] else 'blocked',critical=[] if vid==first[0] else [dict(type='usage',where='门边',text='关系错位')]) for vid in ids])(req)
    env.codex.script=[one_good]*30
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert result['choice'] is None
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']==first[0]
    assert len(env.wb.episode_batches(eid))==1


def test_H_T8_H_T9_identity_before_dependent_scene_and_state_confirmation(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path)
    assets=[dict(id='identity',kind='character',roleId='afu',title='阿福',description='柴犬，浅色制服',imagePrompt='柴犬浅色制服身份图',sceneIds=['场01']),dict(id='room',kind='scene',title='门厅',description='门厅',imagePrompt='门厅空景',sceneIds=['场01'],dependsOn=['identity']),dict(id='wet',kind='state',roleId='afu',stateId='wet',title='湿衣状态',description='制服湿透',imagePrompt='湿衣状态',sceneIds=['场01'],dependsOn=['identity','room'])]
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=assets),'dependencies')
    env.codex.script=[image_reply]*30
    prepare=env.wb.prepare_asset_batch;sequence=[]
    def checked(episode_id,needs,**kwargs):
        rows={n['need_key']:n for n in env.wb.asset_worksheet(eid)['rows']}
        for key in needs:
            assert all(rows[dep]['adopted_version_id'] for dep in rows[key]['depends_on'])
            sequence.append(key)
        return prepare(episode_id,needs,**kwargs)
    monkeypatch.setattr(env.wb,'prepare_asset_batch',checked)
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert sequence==['identity','room','wet'],result['reason']
    matrix=env.wb.cast_matrix(eid)
    assert matrix['roleStates'][0]['confirmed']
    assert matrix['roleStates'][0]['observation']=='铜制门铃位于木门上'
    assert any(d['kind']=='fill_observation' for d in env.wb.continuous_view(eid)['decisions'])


def test_H_T2_fixed_role_identity_never_generates(tmp_path):
    from test_asset_worksheet import prepare
    env,eid,identity,items=prepare(tmp_path)
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[items[0]]),'fixed-only')
    # Isolate fixed identity handling from the unrelated project product requirement.
    from workbench.db import transaction
    with transaction(env.wb.db_path) as db:db.execute('INSERT OR REPLACE INTO episode_product_rule VALUES(?,?)',(eid,0))
    env.codex.script=[image_reply]*12
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']==identity,result['reason']
    assert env.wb.episode_batches(eid)==[]


def test_H_T7c_library_key_visual_enters_choice_instead_of_auto_reuse(tmp_path):
    from workbench.db import transaction,new_id
    env,eid=key_fixture(tmp_path);pid=env.wb.episode_view(eid)['episode']['project_id']
    vid=env.image(pid,eid,'门厅旧图','scene')['versionId']
    with transaction(env.wb.db_path) as db:db.execute('INSERT INTO library_entry(id,project_id,asset_version_id,usage,status,created_at) VALUES(?,?,?,?,?,?)',(new_id(),pid,vid,'门厅','approved',env.wb.clock()))
    env.codex.script=[with_pick(image_reply)]*20
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert result['choice'] is None
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']
    assert len(env.wb.episode_batches(eid))==0


def test_H_image_card_discussion_preserves_candidates_and_no_director_decision(tmp_path):
    env,eid=key_fixture(tmp_path);env.codex.script=[image_reply]*20
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    # Historical cards remain discussable; new runs no longer create one.
    need=env.wb.asset_worksheet(eid)['rows'][0]
    value=env.wb.image_assessments(eid,'room')[0]['body']
    choice=dict(kind='visual',stage='assets',needKey='room',needRevision=need['revision'],candidates=[dict(key='A',**value)])
    env.wb._continuous_update(result['id'],state='waiting_choice',choice=choice)
    result=env.wb._continuous_record(result['id'])
    before=env.wb.episode_view(eid)['artifacts'];batches=len(env.wb.episode_batches(eid))
    env.codex.script=[lambda req:dict(reply({})(req),reply='这两张都是同一场景，光线略有区别。',removedIds=[])]
    after=env.wb.choose_continuous(result['id'],'free',text='这两张有什么区别？',command_id='ask')
    assert after['state']=='waiting_choice' and after['choice']['candidates']==result['choice']['candidates'],after
    assert after['choice']['reply']=='这两张都是同一场景，光线略有区别。'
    assert len(env.wb.episode_batches(eid))==batches
    assert [(a['stage'],a['adopted_version_id']) for a in env.wb.episode_view(eid)['artifacts']]==[(a['stage'],a['adopted_version_id']) for a in before]
    with env.db() as db:assert not db.execute('SELECT 1 FROM director_decision WHERE episode_id=?',(eid,)).fetchone()


def test_H_product_catalog_input_and_actual_main_image_in_generation(tmp_path):
    from helpers import Env
    env=Env(tmp_path);pid,eid=env.new_episode(config='puggy');env.adopt_story(eid);env.script(eid)
    assert env.wb.product_placements(eid)['minimum']>0
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id='room',kind='scene',title='门厅商品入景',description='商品自然放在门厅边桌',imagePrompt='门厅边桌商品自然入景，保留主图图案',sceneIds=['场01'],productCode='15')]),'product-design')
    task=env.wb.create_task(eid,'asset_design','核对商品输入',discussion_only=True,workspace_revision=env.art(eid,'asset_design')['revision'])
    manifest=env.wb.run_manifest(task['runId'])['manifest']
    assert any(item['role']=='project_products' for item in manifest['inputs'])
    assert any(item['name']=='product_placement.md' for item in manifest['inputs'])
    env.drain(task['runId'])
    env.codex.script=[image_reply]*20
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    need=env.wb.asset_worksheet(eid)['rows'][0]
    assert need['adopted_version_id'],result['reason']
    with env.db() as db:main=db.execute('SELECT version_id FROM episode_product WHERE episode_id=? AND code=?',(eid,'15')).fetchone()[0]
    batch=env.wb.batch_view(env.wb.episode_batches(eid)[0]['id'])
    assert main in batch['spec']['items'][0]['inputs']
    assert env.wb.asset_version(main)['category']=='product_reference'


def test_H_T4_best_candidate_follows_model_order_not_insertion_order(tmp_path):
    from workbench.db import transaction
    env,eid,first=fixture(tmp_path);pid=env.wb.episode_view(eid)['episode']['project_id']
    preferred=env.image(pid,eid,'更适配用途的门铃','prop',rgb=(180,80,40))['versionId']
    need=env.wb.asset_worksheet(eid)['rows'][0]
    with transaction(env.wb.db_path) as db:
        for vid in (first,preferred):db.execute('INSERT INTO asset_need_candidate VALUES(?,?)',(need['id'],vid))
    def ranked(req):
        return reply([assessment(preferred,reason='更清楚呈现关键支撑',recommendRequired=True),assessment(first,reason='可以使用但支撑不如另一张清楚')])(req)
    env.codex.script=[ranked]*8
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']==preferred,result['reason']
    assert env.wb.episode_batches(eid)==[]
    rule=next(a['requirement'] for a in env.wb.episode_assets(eid) if a['id']==preferred)
    assert rule['selectedBy']=='agent' and rule['reason']=='更清楚呈现关键支撑'


def test_unrelated_library_skipped_and_blocked_candidates_not_reassessed(tmp_path):
    """实测 135：无关库图不拿去看图评估；自己出新图后只评新图，已判不可用的旧图不重评。"""
    from workbench.db import transaction,new_id
    env,eid,vid=fixture(tmp_path);pid=env.wb.episode_view(eid)['episode']['project_id']
    other=env.image(pid,eid,'爆米花','prop')['versionId']
    with transaction(env.wb.db_path) as db:
        db.execute('INSERT INTO library_entry(id,project_id,asset_version_id,usage,status,created_at) VALUES(?,?,?,?,?,?)',(new_id(),pid,other,'爆米花盆','approved',env.wb.clock()))
    seen=[]
    def judge(req):
        ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
        seen.append(ids)
        return image_reply(req,'usable' if len(seen)>=2 else 'blocked')
    env.codex.script=[with_pick(judge,take=False)]*10
    env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']
    assert all(len(ids)==1 for ids in seen) and len(seen)==2  # 第一张被判不可用，出第二张后只评第二张
    assert len({i for ids in seen for i in ids})==2


def test_independent_needs_generate_and_assess_in_parallel(tmp_path):
    """实测 139：图片通道允许并发时，互不依赖的条目同时出图、同时评估，各自完成采用；游标记录不互相覆盖。"""
    import threading,time
    from test_image_assess import Env
    env=Env(tmp_path);pid,eid=env.new_episode();env.adopt_story(eid);env.script(eid)
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[
        dict(id='bell',kind='prop',title='门铃',description='铜制门铃',imagePrompt='铜制门铃',sceneIds=['场01']),
        dict(id='lamp',kind='prop',title='台灯',description='黄铜台灯',imagePrompt='黄铜台灯',sceneIds=['场01'])]),'design')
    env.wb.image_providers_map['fake-image'].max_concurrency=2
    lock=threading.Lock();active=[0];peak=[0]
    def judge(req):
        with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
        time.sleep(.3)
        with lock:active[0]-=1
        return image_reply(req)
    env.codex.script=[judge]*8
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    rows={r['need_key']:r for r in env.wb.asset_worksheet(eid)['rows']}
    assert rows['bell']['adopted_version_id'] and rows['lamp']['adopted_version_id'],result['reason']
    assert peak[0]==2
    images=env.wb._continuous_record(result['id'])['cursor']['images']
    assert {'bell','lamp'}<=set(images) and all(images[k]['attempts'] for k in ('bell','lamp'))


def test_continuous_follows_latest_channel_chosen_by_director(tmp_path):
    """实测 139：导演在备图工作单换了出图通道后，连续制作接下来跟着用新通道，不被之前的选择锁死。"""
    from workbench.images import FakeImageProvider
    env,eid,_=fixture(tmp_path);env.wb.image_providers_map['other-image']=FakeImageProvider('other-image')
    need=env.wb.asset_worksheet(eid)['rows'][0]
    first=env.wb.prepare_asset_batch(eid,{need['need_key']:need['revision']},provider='fake-image',size='1024x1024',command_id='manual-1')
    record=dict(id='probe',episode_id=eid,cursor=dict(imageOptions={'provider':'fake-image'}))
    env.wb._continuous_record=lambda cid:record
    assert env.wb._image_channel('probe',eid)['provider']=='fake-image'
    env.wb.cancel_batch(first['batchId'],'manual-cancel')
    need=env.wb.asset_worksheet(eid)['rows'][0]
    env.wb.prepare_asset_batch(eid,{need['need_key']:need['revision']},provider='other-image',size='1024x1024',command_id='manual-2')
    assert env.wb._image_channel('probe',eid)['provider']=='other-image'
    assert first['batchId']


def test_system_image_channel_used_by_new_episode_and_expert(tmp_path):
    from workbench.images import FakeImageProvider
    env,eid,_=fixture(tmp_path);wb=env.wb
    wb.image_providers_map['other-image']=FakeImageProvider('other-image')
    wb.save_system_image_channel('other-image')
    assert wb.system_image_channel()['provider']=='other-image'
    wb._continuous_record=lambda cid:dict(cursor={})
    assert wb._image_channel('probe',eid)['provider']=='other-image'
    wb.save_episode_image_channel(eid,'fake-image')
    assert wb._image_channel('probe',eid)['provider']=='fake-image'
    assert wb.system_image_channel()['provider']=='other-image'


def test_director_channel_choice_saved_and_wins_over_last_batch(tmp_path):
    """实测 139：导演在备图工作单选通道即保存为本集设置，连续制作按它出图，不需要先手动出一批。"""
    from workbench.images import FakeImageProvider
    from workbench.errors import Rejected
    env,eid,_=fixture(tmp_path);env.wb.image_providers_map['other-image']=FakeImageProvider('other-image')
    need=env.wb.asset_worksheet(eid)['rows'][0]
    env.wb.prepare_asset_batch(eid,{need['need_key']:need['revision']},provider='fake-image',size='1024x1024',command_id='manual-1')
    assert env.wb.episode_image_channel(eid)['provider']=='fake-image'
    assert env.wb.save_episode_image_channel(eid,'other-image')['source']=='director'
    assert env.wb.episode_image_channel(eid)['provider']=='other-image'
    with pytest.raises(Rejected):env.wb.save_episode_image_channel(eid,'missing-channel')


def test_director_adopted_blocked_image_counts_as_accepted(tmp_path):
    """实测 144：导演在备图工作单亲手采用有严重问题的图，视为导演已接受，连续制作不再停在“未接受的严重问题”。"""
    from workbench.db import transaction
    env,eid,vid=fixture(tmp_path)
    need=env.wb.asset_worksheet(eid)['rows'][0]
    with transaction(env.wb.db_path) as db:db.execute('INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)',(need['id'],vid))
    env.wb.adopt_asset_needs(eid,{need['need_key']:dict(versionId=vid,revision=need['revision'])},'director-adopt')
    env.codex.script=[lambda req:image_reply(req,'blocked')]*6
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert 'stop' not in (result.get('reason') or '') and '未接受的严重问题' not in (result.get('reason') or ''),result['reason']
    with env.db() as db:
        issue=db.execute('SELECT * FROM accepted_image_issue WHERE asset_version_id=?',(vid,)).fetchone()
        assert issue is None  # No new assessment or invented acceptance after adoption.
    assert not any(r.purpose=='image_assess' for r in env.codex.calls)
    assert not env.wb.material_issues(eid)  # Nothing was newly assessed or waived.


def _always_blocked_with(expert):
    def handle(req):
        if req.purpose=='progress_expert' and '项目图库' not in req.prompt:return expert(req)
        return image_reply(req,'blocked')
    return handle


def test_quality_stuck_never_waits_and_falls_back_to_provisional(tmp_path):
    """实测 143：返修用尽只剩质量问题时，没有“等待”可选；专家硬选 wait 时回退为暂用最好的一张，连续制作不停。"""
    env,eid,_=fixture(tmp_path)
    def expert_wait(req):
        data=json.loads(req.prompt.split("以下是资料：\n")[1].split("\n上次回包")[0])
        assert 'wait' not in data['actions'] and data['waitFor']==[]
        return dict(action='wait',target=data['target'],versionId='',instruction='',reason='没有可执行动作',summary='等待',issueKeys=[])
    env.codex.script=[_always_blocked_with(expert_wait)]*40
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    need=env.wb.asset_worksheet(eid)['rows'][0]
    assert need['adopted_version_id'],result['reason']
    assert any(i['state']=='provisional' and i['version_id']==need['adopted_version_id'] for i in env.wb.material_issues(eid))


def test_local_edit_limited_and_ends_in_provisional(tmp_path):
    """实测 143：局部修图以最好的候选为底图，每项最多 2 次，不占两次常规出图上限，之后必定暂用。"""
    env,eid,_=fixture(tmp_path)
    def expert_edit(req):
        data=json.loads(req.prompt.split("以下是资料：\n")[1].split("\n上次回包")[0])
        top=data['candidates'][0]
        if 'edit' in data['actions']:
            return dict(action='edit',target=data['target'],versionId=top['versionId'],instruction='只修正门铃支撑，其余保持不变',reason='局部错误',summary='局部修图',issueKeys=[])
        return dict(action='provisional',target=data['target'],versionId=top['versionId'],instruction='',reason='修图后仍有问题',summary='暂用',issueKeys=[i['type']+':'+i['where'] for i in top['critical']])
    env.codex.script=[_always_blocked_with(expert_edit)]*60
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    attempts=env.wb._continuous_record(result['id'])['cursor']['images']['bell']['attempts']
    edits=[a for a in attempts if a.get('edit')]
    assert len(edits)==2 and len([a for a in attempts if not a.get('edit')])==2
    batches=[env.wb.batch_view(b['id']) for b in env.wb.episode_batches(eid)]
    edited=[b for b in batches if b['spec']['items'][0]['inputs'] and b['spec']['items'][0]['inputs'][0] in {a['base'] for a in edits}]
    assert len(edited)==2
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id'],result['reason']


def test_stop_reason_names_the_stuck_need(tmp_path):
    """实测 143：停下原因逐项写明条目名，并记下 stoppedNeeds 供卡片跳转。"""
    env,eid,_=fixture(tmp_path)
    env.wb.image_providers_map['fake-image'].script=['insufficient']*10
    env.codex.script=[image_reply]*10
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='start')
    assert result['state']=='blocked' and result['reason'].startswith('门铃：'),result['reason']
    stopped=env.wb._continuous_record(result['id'])['cursor']['stoppedNeeds']
    assert stopped[0]['key']=='bell' and stopped[0]['title']=='门铃'


def test_image_parallelism_follows_channel_setting():
    from types import SimpleNamespace
    from workbench.continuous_images import ContinuousImages
    runner = SimpleNamespace(
        _continuous_record=lambda cid: {'episode_id': 'episode'},
        _image_channel=lambda cid, eid: {'provider': 'api'},
        _image_options=lambda eid, options: options,
        image_providers_map={'api': SimpleNamespace(max_concurrency=5)},
    )
    assert ContinuousImages._image_parallelism(runner, 'run') == 5
