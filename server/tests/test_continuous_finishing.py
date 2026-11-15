"""I package: real commands, isolated storage and scripted offline executors."""
import json,re
from helpers import Env
from workbench.db import transaction,dumps
from workbench.executors.fake import review,draft
from test_image_assess import assessment,reply


def finishing_fixture(tmp_path,scoped=False,config="_template",**options):
    env=Env(tmp_path);case=env.full_episode(config=config,segments={'G01':[4000,4000],'G02':[8000]},deliver=False,**options)
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
    from test_continuous_wake import run_record
    cid=run_record(env,eid)
    return env,case,cid,vid


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
    before=len(env.codex.calls)+len(env.claude.calls)
    assert wb._continuous_prompt_checks(cid,final)==final
    assert len(env.codex.calls)+len(env.claude.calls)==before
    wb._continuous_adopt(cid,'prompts',final)
    assert env.art(eid,'prompts')['adopted_version_id']==final
    assert not wb._continuous_record(cid)['cursor'].get('pendingUserChange')


def test_plain_prompt_needs_no_extra_model_review(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    before=tuple(len(e.calls) for e in (env.codex,env.claude))
    assert wb._continuous_prompt_checks(cid,vid)==vid
    assert tuple(len(e.calls) for e in (env.codex,env.claude))==before


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


def test_prompt_repair_targets_only_invalid_segment(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    original=wb._continuous_version(vid)
    old=original['segments']['G02'].copy()
    original['segments']['G01']['txt']+=' 图片9'
    invalid=wb.save_manual(env.art(case['eid'],'prompts')['id'],vid,original,'invalid-label')['versionId']
    calls=[]
    def fix(cid,base,affected,why):
        calls.append((base,affected,why));return vid
    monkeypatch.setattr(wb,'_continuous_prompt_revision',fix)
    assert wb._continuous_prompt_checks(cid,invalid)==vid
    assert len(calls)==1 and calls[0][:2]==(invalid,{'G01'})
    assert wb._continuous_version(invalid)['segments']['G02']==old


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
    from test_continuous_wake import run_record
    cid=run_record(env,eid)
    wb._continuous_adopt(cid,'prompts',wb._continuous_prompt_checks(cid,candidate))
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
        if req.kind=='review':return review(req)
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
    assert not wb.adoption_gate(adopted_prompt)['formatErrors']


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
    # Exercise the actual 07 → 08 → 09 boundary directly, without a synthetic stop.
    assert wb._continuous_images(cid)
    assert wb._continuous_finish_stages(cid)
    art=env.art(eid,'prompts');final=art['adopted_version_id']
    assert final and not wb.adoption_gate(final)['formatErrors']
    versions=[v['id'] for v in art['versions']]
    before=len(env.codex.calls)
    assert wb._continuous_finish_stages(cid)
    assert [v['id'] for v in env.art(eid,'prompts')['versions']]==versions
    assert not any(c.purpose=='image_assess' for c in env.codex.calls[before:])


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
    def choose(cid,kind,key,instruction,materials,validate,**kwargs):
        answer=dict(selections=[dict(segment=s['segment'],versionId=image,reason='同场门厅参考') for s in materials['segments']])
        assert not validate(answer)
        assert validate(dict(selections=[]))
        return answer,'basis'
    monkeypatch.setattr(wb,'_continuous_bounded_json',choose)
    monkeypatch.setattr(wb,'_continuous_bind',lambda *args,**kwargs:bound.append(args))
    wb._continuous_empty_scene_bindings(cid)
    assert len(bound)==2 and all(row[1]==image for row in bound)


def test_format_error_targets_named_segment(monkeypatch,tmp_path):
    env, _, cid, vid = finishing_fixture(tmp_path)
    wb = env.wb
    monkeypatch.setattr(wb,"adoption_gate",lambda _: {"formatErrors":["G01：参考标签与上传顺序不符"]})
    scopes=[]
    def revise(cid, vid, affected, instruction):
        scopes.append(affected)
        return vid
    monkeypatch.setattr(wb,"_continuous_prompt_revision",revise)
    assert wb._continuous_prompt_checks(cid,vid)==vid
    assert scopes==[{"G01"}]
