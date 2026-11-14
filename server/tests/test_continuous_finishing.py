"""I package: real commands, isolated storage and scripted offline executors."""
import json,re
from helpers import Env
from workbench.db import transaction,dumps
from workbench.executors.fake import review,draft
from test_image_assess import assessment,reply


def finishing_fixture(tmp_path,scoped=False,config="_template"):
    env=Env(tmp_path);case=env.full_episode(config=config,segments={'G01':[4000,4000],'G02':[8000]},deliver=False)
    eid=case['eid'];wb=env.wb;face=case['img']['versionId']
    if scoped:
        for shot in case['plan']['shots']:shot.update(visibleRoles=['阿福'],propStates={})
        env.save_adopt(eid,'plan',case['plan'],'scoped')
        wb.set_role_baseline(eid,'阿福',face,'柴犬阿福','baseline')
        for segment in ('G01','G02'):wb.set_cast(eid,segment,'阿福','ok',face)
    from workbench import promptdraft
    prompts=promptdraft.draft(wb,eid)
    if scoped:
        for sid,entry in prompts['segments'].items():entry['refs'][0]['shots']=[s['id'] for s in case['plan']['shots'] if s['segment']==sid]
    vid=wb.save_manual(env.art(eid,'prompts')['id'],case['prompts'],prompts,'candidate')['versionId']
    if not scoped:env.review_segments(vid,['G01','G02'],cid='candidate-baseline')
    run=wb.start_continuous(eid,command_id='run')
    wb._continuous_update(run['id'],state='running',reason=None)
    # The fixture's candidate precedes continuous execution and is not a user edit.
    with env.db() as db:baseline={r['stage']:r['adopted_version_id'] for r in db.execute('SELECT stage,adopted_version_id FROM artifact WHERE episode_id=? AND adopted_version_id IS NOT NULL',(eid,))}
    wb._continuous_cursor(run['id'],base=baseline)
    return env,case,run['id'],vid


def test_I_T2_observations_chain_final_review_and_adoption(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb;eid=case['eid'];saved=[]
    def images(req):
        ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
        return reply([assessment(i,observation='柴犬阿福完整可见',props={}) for i in ids])(req)
    env.codex.script=[images]*6
    original=wb.save_shot_observation
    def traced(version,*args,**kwargs):
        result=original(version,*args,**kwargs);saved.append((version,result['versionId']));return result
    monkeypatch.setattr(wb,'save_shot_observation',traced)
    final=wb._continuous_shot_observations(cid,vid)
    assert saved==[] and final==vid
    content=wb._continuous_version(final)
    assert all(not e['refs'][0].get('visualReviewId') for e in content['segments'].values())
    assert not wb.adoption_gate(final)['formatErrors']
    before=len(env.claude.calls)
    assert wb._continuous_prompt_reviews(cid,final)==final
    calls=env.claude.calls[before:]
    assert len(calls)==4
    with env.db() as db:
        assert all(db.execute('SELECT target_version_id FROM run WHERE id=?',(req.run_id,)).fetchone()[0]==final for req in calls)
    wb._continuous_adopt(cid,'prompts',final)
    assert env.art(eid,'prompts')['adopted_version_id']==final
    assert not wb._continuous_record(cid)['cursor'].get('pendingUserChange')


def test_I_T4_ordinary_segment_issue_auto_waived_and_logged(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    wb.save_step_config(case['eid'],'prompts',keep='本次核对措辞')
    env.claude.script=[lambda req:review(req,'return',[dict(category='clarity',reason='措辞可精简')])]*4
    assert wb._continuous_prompt_reviews(cid,vid)==vid
    assert all(item['ok'] for item in wb.prompt_review_status(vid).values())
    decisions=wb.continuous_view(case['eid'])['decisions']
    assert len([d for d in decisions if d['kind']=='waive_segment'])==2


def test_I_T1_unclear_cast_bound_and_unbound_resolved(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb;eid=case['eid'];face=case['img']['versionId']
    wb.set_cast(eid,'G01','阿福','unclear',face)
    wb.set_cast(eid,'G02','阿福','unclear')
    env.codex.script=[reply([assessment(face,props={},observation='柴犬阿福可见')]),reply(dict(visible=True,reason='实际图可见柴犬阿福'))]
    wb._continuous_cast(cid)
    matrix=wb.cast_matrix(eid)
    assert all(r['cells']['阿福']['status']=='ok' for r in matrix['rows'])
    assert all(r['cells']['阿福']['assetVersionId']==face for r in matrix['rows'])
    assert len([d for d in wb.continuous_view(eid)['decisions'] if d['kind']=='fill_observation'])>=2


def test_I_T3_blocking_two_rounds_repairs_only_affected_segment(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid'];before=len(env.claude.calls);count=[]
    # Change G01 only; G02 and its join remain untouched, so the hash-based
    # scope computation can reuse its completed review.
    original=wb._continuous_version(vid)
    original['segments']['G01']['txt']+='\n检查动作。'
    new=wb.save_manual(env.art(eid,'prompts')['id'],vid,original,'changed')['versionId']
    wb._continuous_internal_version(cid,new)
    def reviewer(req):
        from workbench.executors.fake import context
        ctx=context(req.prompt)
        if ctx['segment']=='G01' and ctx['passName']=='cold_read':
            count.append(req.run_id)
            if len(count)<=2:return review(req,'return',[dict(category='source',reason='动作缺来源')])
        return review(req)
    env.claude.script=[reviewer]*20
    def creator(req):
        if '判断阻断意见' in req.prompt:return reply(dict(kind='wording',reason='补明已有动作的主语'))(req)
        content=wb._continuous_version(wb._continuous_art(eid,'prompts')['versions'][-1]['id'])
        content['segments']['G01']['txt']+='\n阿福主动检查。'
        return draft(req,segments=json.dumps({'G01':content['segments']['G01']}))
    env.codex.script=[creator]*12
    final=wb._continuous_prompt_reviews(cid,new)
    assert len(count)==3
    assert wb._continuous_version(final)['segments']['G02']==original['segments']['G02']
    # The adjacent comparison includes G01's join; G02 cold read is reused.
    assert all(wb.run_manifest(r.run_id)['manifest']['scope']=='G01' or r.purpose=='comparison' for r in env.claude.calls[before:])
    wb._continuous_adopt(cid,'prompts',final)
    assert env.art(eid,'prompts')['adopted_version_id']==final


def test_I_T5_structure_splits_plan_updates_bindings_preserves_other_segment(tmp_path):
    from workbench import promptdraft
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb;eid=case['eid'];face=case['img']['versionId']
    old=wb._continuous_version(vid);old_plan=case['plan']
    def scripted(req):
        if req.purpose=='image_assess':return reply([assessment(face,observation='柴犬阿福完整可见',props={})])(req)
        if '决定简化动作或拆段' in req.prompt:return reply(dict(action='split',firstShotOfSecond='S02',reason='两个动作分别完成'))(req)
        content=promptdraft.draft(wb,eid)
        content['segments']['G02']['txt']+='\n模型不应带来的改写。'
        return draft(req,segments=json.dumps(content['segments']))
    env.codex.script=[scripted]*20
    final=wb._continuous_structure(cid,vid,'G01',[dict(reason='两个动作需要拆为独立段')])
    plan=wb._continuous_version(env.art(eid,'plan')['adopted_version_id'])
    assert {s['id'] for s in plan['segments']}=={'G01a','G01b','G02'}
    assert [s for s in plan['shots'] if s['segment']=='G02']==[s for s in old_plan['shots'] if s['segment']=='G02']
    content=wb._continuous_version(final)
    assert content['segments']['G02']==old['segments']['G02']
    for sid in ('G01a','G01b'):
        assert content['segments'][sid]['refs'][0]['shots']
        assert next(r for r in wb.cast_matrix(eid)['rows'] if r['segment']==sid)['cells']['阿福']['status']=='ok'
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM version WHERE id=?',(vid,)).fetchone()[0]==1


def test_I_T6_candidate_product_coverage_requires_record_mark_reference(tmp_path):
    import pytest
    from workbench.continuous import Halt
    env,case,cid,vid=finishing_fixture(tmp_path,config='puggy');wb=env.wb;eid=case['eid']
    wb.use_project_product(eid,'15','product')
    image=env.image(case['pid'],eid,name='椅背靠垫场景',category='scene',rgb=(100,120,130))['versionId']
    content=wb._continuous_version(vid)
    content['segments']['G01']['refs'].append(dict(assetVersionId=image,label='图片2',kind='image',role='椅背靠垫'))
    content['segments']['G01']['txt']+='\n图片2 是椅背靠垫场景。'
    candidate=wb.save_manual(env.art(eid,'prompts')['id'],vid,content,'product-candidate')['versionId']
    wb._continuous_internal_version(cid,candidate)
    assert wb.product_placements(eid,candidate=content)['verified']==0
    wb.save_product_placement(eid,'15','S01','椅背支撑，标签朝外，保留轮廓',image,'椅背上可见靠垫','placement')
    assert wb.product_placements(eid,candidate=content)['verified']==0
    wb.mark_image(eid,image,'required',['S01'],command_id='required')
    assert wb.product_placements(eid)['verified']==0  # Previous adopted prompts have no scene.
    assert wb.product_placements(eid,candidate=content)['verified']==1
    # Removing required status blocks AI adoption even after all reviews pass.
    env.review_segments(candidate,['G01','G02'],cid='product')
    wb.mark_image(eid,image,'required',enabled=False,command_id='not-required')
    with pytest.raises(Halt):wb._continuous_adopt(cid,'prompts',candidate)
    assert '商品入景覆盖不足' in wb._continuous_record(cid)['reason']
    assert env.art(eid,'prompts')['adopted_version_id']!=candidate
    wb.mark_image(eid,image,'required',['S01'],command_id='required-again')
    wb._continuous_update(cid,state='running',reason=None)
    wb._continuous_prompt_reviews(cid,candidate)
    wb._continuous_adopt(cid,'prompts',candidate)
    assert env.art(eid,'prompts')['adopted_version_id']==candidate


def test_PN_T4_continuous_returns_unapplied_note_to_plan_and_finishes(tmp_path):
    from workbench import promptdraft
    from workbench.stressrepairs import adopted
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb;eid=case['eid'];face=case['img']['versionId']
    report,_=env.save_adopt(eid,'stress',dict(retelling='阿福检查抽屉',scope='本集',diagnosis='成立',issues=[],options=[],productionNotes=[dict(id='P03',note='明确推回抽屉',sceneIds=['场02'])]),'notes')
    design=wb._continuous_version(env.art(eid,'asset_design')['adopted_version_id'])
    design['productionNoteResponses']=[dict(id='P03',reportVersionId=report,owner='plan',status='assigned',reason='镜头展示动作')]
    env.save_adopt(eid,'asset_design',design,'assign')
    # This legacy adopted plan has not acknowledged P03. Candidate coverage must
    # not borrow a response from any previous prompt.
    assert not wb._continuous_note_errors(eid,'prompts',wb._continuous_version(vid))
    with transaction(wb.db_path) as db:
        cursor=wb._continuous_record(cid)['cursor'];wb._continuous_reconcile(db,eid,cursor)
        db.execute('UPDATE continuous_run SET cursor=?,state=? WHERE id=?',(dumps(cursor),'running',cid))
    called=[]
    def executor(req):
        manifest=wb.run_manifest(req.run_id)['manifest'];stage=manifest['stage']
        if req.purpose=='image_assess':return reply([assessment(face,observation='阿福可见',props={})])(req)
        called.append(stage)
        if stage=='plan':
            content=dict(case['plan'],productionNoteResponses=[dict(id='P03',reportVersionId=report,owner='plan',status='applied',where='S01')])
        elif stage=='prompts':content=promptdraft.draft(wb,eid)
        else:raise AssertionError(stage)
        return draft(req,**{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in content.items() if v!=""})
    env.codex.script=[executor]*30
    assert wb._continuous_finish_stages(cid)
    assert 'plan' not in called
    adopted_prompt=env.art(eid,'prompts')['adopted_version_id']
    assert not wb._continuous_note_errors(eid,'prompts',wb._continuous_version(adopted_prompt))
    assert all(item['ok'] for item in wb.prompt_review_status(adopted_prompt).values())


import pytest
@pytest.mark.parametrize('conflict',[True,False])
def test_I_T7_prompt_supplement_decided_before_adoption(tmp_path,conflict):
    from workbench import promptdraft
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid']
    old=dict(subject='手机.位置',statement='手机在口袋',scenes=['场01'],reason='明确位置')
    # Record the existing confirmed fact through the real supplement API.
    with transaction(wb.db_path) as db:
        ident=wb._insert_supplement(db,eid,old,'script',env.art(eid,'script')['adopted_version_id'],state='confirmed')
    new=dict(old,statement='手机在手袋',scenes=['场02'] if not conflict else ['场01'])
    env.codex.script=[lambda req:draft(req,supplements=json.dumps([new]))]
    wb._continuous_create(cid,'prompts','登记本段制作补充',base=vid)
    candidate=env.art(eid,'prompts')['versions'][-1]['id']
    specific=dict(new,subject='场02手机.位置')
    env.codex.script=[reply(dict(conflict=conflict,keep='existing' if conflict else 'both',reason='核对采用剧本及各自场次')),lambda req:draft(req,supplements=json.dumps([] if conflict else [specific]))]
    wb._continuous_adopt(cid,'prompts',candidate)
    records=wb.production_supplements(eid)
    assert any(r['statement']==new['statement'] and r['state']==('rejected' if conflict else 'confirmed') for r in records)
    assert any(r['statement']==old['statement'] and r['state']=='confirmed' for r in records)
    adopted=env.art(eid,'prompts')['adopted_version_id']
    assert not wb.adoption_gate(adopted)['formatErrors']
    if not conflict:assert any(r['subject']==specific['subject'] for r in records)


def test_I_entry_from_07_finishes_09_and_resumes_without_new_drafts(tmp_path,monkeypatch):
    from workbench import promptdraft
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb;eid=case['eid']
    wb.cancel_continuous(cid)
    # J now follows I; isolate this test at the verified 09 boundary. J-T2
    # separately exercises real delivery and package creation.
    monkeypatch.setattr(wb,'_continuous_delivery',lambda c:wb._continuous_update(c,state='blocked',reason='01–09 已完成；交付预览调度将在 J/K 包接入'))
    def scripted(req):
        if req.purpose=='image_assess':
            ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
            return reply([assessment(i,observation='可见场景和柴犬阿福',props={}) for i in ids])(req)
        content=promptdraft.draft(wb,eid)
        return draft(req,segments=json.dumps(content['segments']))
    env.codex.script=[scripted]*30
    result=wb.start_continuous(eid,target_stage='delivery',command_id='full')
    assert result['reason']=='01–09 已完成；交付预览调度将在 J/K 包接入',result['reason']
    final=env.art(eid,'prompts')['adopted_version_id']
    assert final!=case['prompts'] and all(item['ok'] for item in wb.prompt_review_status(final).values())
    count=len(env.art(eid,'prompts')['versions'])
    result=wb.resume_continuous(result['id'],'resume')
    assert result['reason']=='01–09 已完成；交付预览调度将在 J/K 包接入',result['reason']
    assert len(env.art(eid,'prompts')['versions'])==count


def test_empty_scene_segment_chooses_existing_scene_reference(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    plan=case['plan']
    for shot in plan['shots']:
        shot.pop('startAssetId',None);shot.pop('endAssetId',None)
        shot['origin']={'scene':'scene-1'}
    image=case['img']['versionId'];bound=[]
    art=wb._continuous_art(case['eid'],'plan')
    monkeypatch.setattr(wb,'_continuous_art',lambda *_:art)
    monkeypatch.setattr(wb,'_continuous_version',lambda _:plan)
    monkeypatch.setattr(wb,'cast_matrix',lambda _:dict(rows=[]))
    monkeypatch.setattr(wb,'asset_worksheet',lambda _:dict(rows=[dict(kind='scene',adopted_version_id=image,title='门厅',description='已采用门厅基准',scene_ids=['scene-1'])]))
    from workbench import imagechoices
    monkeypatch.setattr(imagechoices,'requirements',lambda *_:[])
    def choose(cid,kind,key,instruction,materials,validate):
        answer=dict(selections=[dict(segment=s['segment'],versionId=image,reason='同场门厅参考') for s in materials['segments']])
        assert not validate(answer)
        assert validate(dict(selections=[]))
        return answer,'basis'
    monkeypatch.setattr(wb,'_continuous_bounded_json',choose)
    monkeypatch.setattr(wb,'_continuous_bind',lambda *args,**kwargs:bound.append(args))
    wb._continuous_empty_scene_bindings(cid)
    assert len(bound)==2 and all(row[1]==image for row in bound)
