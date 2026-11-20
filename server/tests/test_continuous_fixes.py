"""K scoped edits use actual versions, review scopes and immutable delivery files."""
import json
import pytest
from pathlib import Path
from helpers import Env, assembled_english_prompts
from test_image_assess import reply
from workbench.executors.fake import draft, directed
from workbench import promptdraft


def fixture(tmp_path,last_scene=False,scene=False):
    env=Env(tmp_path);case=env.full_episode(deliver=False);wb=env.wb;eid=case['eid']
    if scene:
        need=wb.asset_worksheet(eid)['rows'][0]
        wb.set_asset_need(eid,need['need_key'],need['revision'],'generate','scene-generate',prompt='无角色无剧情状态的便利店标准空景')
        need=wb.asset_worksheet(eid)['rows'][0]
        batch=wb.prepare_asset_batch(eid,{need['need_key']:need['revision']},command_id='scene-batch',**wb._image_options(eid,{}))
        value=wb.batch_view(batch['batchId']);wb.authorize_batches(eid,{value['id']:value['summary_hash']},'scene-auth');wb.execute_batch(value['id'])
        need=wb.asset_worksheet(eid)['rows'][0];scene_vid=need['candidates'][0]
        wb.adopt_asset_needs(eid,{need['need_key']:dict(versionId=scene_vid,revision=need['revision'])},'scene-adopt')
        case['scene']=scene_vid;case['needKey']=need['need_key']
    plan=case['plan']
    for shot in plan['shots']:
        shot.update(visibleRoles=['阿福'],propStates={})
        if scene and shot['segment'] in ('G06','G07'):shot['startAssetId']=case['scene']
        if last_scene and shot['segment']=='G07':shot['origin']=dict(scene='场01',line='阿福巡店，推门确认上锁。')
    env.save_adopt(eid,'plan',plan,'scoped-plan')
    wb.set_role_baseline(eid,'阿福',case['img']['versionId'],'柴犬阿福','identity')
    for segment in plan['segments']:wb.set_cast(eid,segment['id'],'阿福','ok',case['img']['versionId'])
    art=env.art(eid,'prompts');content=promptdraft.draft(wb,eid)
    vid=wb.save_manual(art['id'],art['adopted_version_id'],content,'readable')['versionId']
    for sid,entry in content['segments'].items():
        for ref in entry['refs']:
            vid=wb.save_shot_observation(vid,sid,ref['assetVersionId'],wb.asset_version(ref['assetVersionId'])['sha256'],ref['shots'],'柴犬阿福完整可见',{},[],command_id='observe-'+sid+'-'+ref['assetVersionId'])['versionId']
    wb.adopt(art['id'],vid,art['adopted_version_id'],'adopt-readables')
    env.review_segments(vid,[s['id'] for s in case['plan']['segments']],cid='readable')
    pub=env.art(eid,'publish');env.save_adopt(eid,'publish',wb._continuous_version(pub['adopted_version_id']),'publish-rechecked')
    assert not wb.delivery_problems(eid,'full')[0]
    case['prompts']=vid;case['delivery']=wb.deliver(eid,'full','original')
    with env.db() as db:
        case['initialDeliveries']=db.execute('SELECT count(*) FROM delivery WHERE episode_id=?',(eid,)).fetchone()[0]
    return env,case


def test_K_T2_question_does_not_change_versions_or_register_decision(tmp_path):
    env,case=fixture(tmp_path);wb=env.wb;eid=case['eid']
    before=[a['adopted_version_id'] for a in wb.episode_view(eid)['artifacts']]
    env.codex.script=[reply(dict(kind='discussion',earliestStage=None,instruction='这里用冷淡反应形成反差。',segments=[],reason='用户询问原因'))]
    result=wb.fix_after_delivery(eid,'为什么结尾这样处理？','question')
    assert result['state']=='done' and '冷淡反应' in result['reason']
    assert [a['adopted_version_id'] for a in wb.episode_view(eid)['artifacts']]==before
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM director_decision WHERE episode_id=?',(eid,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM delivery WHERE episode_id=?',(eid,)).fetchone()[0]==case['initialDeliveries']


def test_K_T1_K_T3_prompt_change_preserves_other_segments_scoped_decision_and_old_delivery(tmp_path):
    env,case=fixture(tmp_path);wb=env.wb;eid=case['eid'];original=wb._continuous_version(case['prompts'])
    previous=wb.prompt_review_status(case['prompts']);oldbytes=Path(case['delivery']['html']).read_bytes()
    text='结尾让阿福更冷淡'
    task=dict(type='prompt',segments=['G07'],needKeys=[],scenes=[],instruction=text,reason='只调整末段反应')
    def rewrite(req):
        content=wb._continuous_version(case['prompts']);content['segments']['G07']['txt']+='\n阿福冷淡地看了一眼。'
        # Try to change another segment too: deterministic merging must preserve it.
        content['segments']['G01']['txt']+='\n不应保留的改写。'
        with env.db() as db:code=db.execute('SELECT code FROM director_decision WHERE episode_id=? ORDER BY rowid DESC',(eid,)).fetchone()[0]
        return draft(req,segments=json.dumps(content['segments']),decisionNotes=json.dumps([dict(decision=code,status='applied',where='G07',quote='阿福冷淡地看了一眼。')]))
    env.codex.script=[reply(dict(kind='change',earliestStage='prompts',instruction=text,segments=['G07'],reason='结尾反应')),reply([task]),rewrite,reply(dict(needsRevision=False,reason='本次只改反应，发布与配乐仍适用',quote='First Night Shift'))]
    result=wb.fix_after_delivery(eid,text,'change')
    assert result['state']=='done',result['reason']
    assert result['mode']=='fix' and result['choice'] is None
    final=env.art(eid,'prompts')['adopted_version_id'];content=wb._continuous_version(final)
    assert content['segments']['G07']!=original['segments']['G07']
    assert all(content['segments'][s]==original['segments'][s] for s in original['segments'] if s!='G07')
    status=wb.prompt_review_status(final)
    assert status['G01']['coldRead']['reviewId']==previous['G01']['coldRead']['reviewId']
    assert status['G01']['comparison']['reviewId']==previous['G01']['comparison']['reviewId']
    with env.db() as db:
        decision=db.execute('SELECT * FROM director_decision WHERE episode_id=?',(eid,)).fetchone()
        assert decision['text']==text and decision['scope_kind']=='scenes' and json.loads(decision['scope_value'])==['场02']
        assert db.execute('SELECT count(*) FROM delivery WHERE episode_id=?',(eid,)).fetchone()[0]==case['initialDeliveries']+1
    assert Path(case['delivery']['html']).read_bytes()==oldbytes
    new=wb.delivery_view(result['cursor']['deliveryId']);assert new['revision']==wb.delivery_view(case['delivery']['deliveryId'])['revision']+1
    assert '本次修改' in Path(new['html']).read_text() and text in Path(new['html']).read_text()


def notes(env,eid,where,quote):
    with env.db() as db:codes=[r[0] for r in db.execute('SELECT code FROM director_decision WHERE episode_id=?',(eid,))]
    return [dict(decision=code,status='applied',where=where,quote=quote) for code in codes]


def scripted_fields(req,content):
    return draft(req,**{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in content.items() if v!=''})


def test_K_T7_other_adjusts_named_plan_segment_and_its_prompts(tmp_path):
    import re
    from test_image_assess import assessment
    env,case=fixture(tmp_path);wb=env.wb;eid=case['eid'];old=wb._continuous_version(case['prompts']);oldplan=case['plan']
    task=dict(type='other',segments=['G05'],needKeys=[],scenes=[],stages=['plan'],targets=['G05'],instruction='第五段动作更紧凑',reason='调整该段表演节奏')
    def execute(req):
        if req.kind=='review' or '退回写稿人' in req.prompt:return directed(req,'codex')
        manifest=wb.run_manifest(req.run_id)['manifest'];stage=manifest['stage']
        if req.purpose=='image_assess':
            ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
            return reply([assessment(i,props={},observation='柴犬阿福可见') for i in ids])(req)
        if '核对当前上游变化' in req.prompt:return reply(dict(needsRevision=False,reason='发布与音乐不受动作微调影响',quote='First Night Shift'))(req)
        if stage=='publish':return scripted_fields(req,wb._continuous_version(env.art(eid,'publish')['adopted_version_id']))
        if stage=='plan':
            content=json.loads(json.dumps(oldplan))
            for shot in content['shots']:
                if shot['segment']=='G05':shot['action']='阿福更紧凑地推回抽屉，'+('先伸手' if shot['id']=='S11' else '随后停手')
            content['decisionNotes']=notes(env,eid,'G05','阿福更紧凑地推回抽屉')
        else:
            content=assembled_english_prompts(req);content['segments']['G05']['txt']+='\nAfu immediately pushes the drawer closed.'
            content['decisionNotes']=notes(env,eid,'G05','Afu immediately pushes the drawer closed.')
        return scripted_fields(req,content)
    env.codex.script=[reply(dict(kind='change',earliestStage='plan',instruction=task['instruction'],segments=['G05'],reason=task['reason'])),reply([task]),*([execute]*25)]
    result=wb.fix_after_delivery(eid,task['instruction'],'other')
    assert result['state']=='done',result['reason']
    plan=wb._continuous_version(env.art(eid,'plan')['adopted_version_id'])
    assert all(s in plan['shots'] for s in oldplan['shots'] if s['segment']!='G05')
    content=wb._continuous_version(env.art(eid,'prompts')['adopted_version_id'])
    assert content['segments']['G05']!=old['segments']['G05']
    assert all(content['segments'][sid]==entry for sid,entry in old['segments'].items() if sid!='G05')


def test_K_T6_story_fixes_one_scene_and_only_mapped_segments(tmp_path):
    import re
    from test_image_assess import assessment
    env,case=fixture(tmp_path,last_scene=True);wb=env.wb;eid=case['eid'];old=wb._continuous_version(case['prompts'])
    original_script=wb._continuous_version(env.art(eid,'script')['adopted_version_id'])
    task=dict(type='story',segments=[],needKeys=[],scenes=['场01'],instruction='第一场先确认钥匙再锁门',reason='修正第一场钥匙因果')
    def execute(req):
        if req.kind=='review' or '退回写稿人' in req.prompt:return directed(req,'codex')
        stage=wb.run_manifest(req.run_id)['manifest']['stage']
        if req.purpose=='image_assess':
            ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
            return reply([assessment(i,props={},observation='柴犬阿福可见') for i in ids])(req)
        if '哪些采用图片必须随之改变' in req.prompt:return reply(dict(needKeys=[],reason='角色身份图仍适用，不需新图'))(req)
        if '核对当前上游变化' in req.prompt:
            quote='First Night Shift' if stage=='publish' else '便利店内景'
            return reply(dict(needsRevision=False,reason='已有静态场景和音乐仍适用',quote=quote))(req)
        if stage=='publish':return scripted_fields(req,wb._continuous_version(env.art(eid,'publish')['adopted_version_id']))
        if stage=='script':
            content=json.loads(json.dumps(original_script));content['scenes'][0]['action']='阿福先确认钥匙再锁门。';where='场01';quote='阿福先确认钥匙再锁门。'
        elif stage=='plan':
            content=json.loads(json.dumps(case['plan']))
            for shot in content['shots']:
                if shot['segment']=='G07':shot.update(action='阿福先确认钥匙再锁门。'+('点头' if shot['id']=='S15' else '退开'),origin=dict(scene='场01',line='阿福先确认钥匙再锁门。'))
            where='G07';quote='阿福先确认钥匙再锁门。'
        else:
            content=assembled_english_prompts(req);where='G07';quote='Afu checks the key before locking the door.'
            content['segments']['G07']['txt']+='\n'+quote
        content['decisionNotes']=notes(env,eid,where,quote)
        return scripted_fields(req,content)
    env.codex.script=[reply(dict(kind='change',earliestStage='script',instruction=task['instruction'],segments=['G07'],reason=task['reason'])),reply([task]),*([execute]*30)]
    result=wb.fix_after_delivery(eid,task['instruction'],'story-fix')
    assert result['state']=='done',result['reason']
    script=wb._continuous_version(env.art(eid,'script')['adopted_version_id'])
    assert script['scenes'][1]==original_script['scenes'][1]
    content=wb._continuous_version(env.art(eid,'prompts')['adopted_version_id'])
    assert all(content['segments'][sid]==entry for sid,entry in old['segments'].items() if sid!='G07')
    assert content['segments']['G07']!=old['segments']['G07']


@pytest.mark.parametrize("new_scene,pause",[(False,False),(True,False),(True,True)])
def test_K_T5_continuity_uses_common_scene_preserves_script_and_other_segments(tmp_path,new_scene,pause,monkeypatch):
    import re
    from test_image_assess import assessment
    env,case=fixture(tmp_path,scene=True);wb=env.wb;eid=case['eid'];old=wb._continuous_version(case['prompts'])
    script=env.art(eid,'script')['adopted_version_id'];task=dict(type='continuity',segments=['G06','G07'],needKeys=[],scenes=[],instruction='第六段和第七段桌子位置对不上',reason='统一窗边桌子和相邻段接点')
    def execute(req):
        if req.kind=='review' or '退回写稿人' in req.prompt:return directed(req,'codex')
        if req.purpose=='image_assess':
            ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
            return reply([assessment(i,props={},observation='实际原图可见便利店，角色图为柴犬阿福') for i in ids])(req)
        if '按本次连续性反馈和两段既有绑定' in req.prompt:
            assert not req.images  # continuity edits inherit the existing references
            return reply(dict(explanation='前段桌子在窗边，后段写在门口；统一为窗边。',sceneVersionId=None if new_scene else case['scene'],newSceneInstruction='无角色、无剧情状态，桌子在窗边的便利店空景' if new_scene else '',stateNeedKeys=[]))(req)
        if '核对当前上游变化' in req.prompt:
            from workbench.decisions import _text_fields
            stage=wb.run_manifest(req.run_id)['manifest']['stage']
            quote=next(t for t in _text_fields(wb._continuous_version(env.art(eid,stage)['adopted_version_id'])) if t.strip())
            return reply(dict(needsRevision=False,reason='本阶段保留，空间引用已在这两段提示词更新',quote=quote))(req)
        if wb.run_manifest(req.run_id)['manifest']['stage']=='asset_design':
            content=wb._continuous_version(env.art(eid,'asset_design')['adopted_version_id'])
            content['assets'].append(dict(id='common-room',kind='scene',title='统一标准空景',description='桌子始终在窗边',imagePrompt='无角色便利店空景，桌子始终在窗边',sceneIds=['场02'],dependsOn=[],keyVisual=False))
            content['decisionNotes']=notes(env,eid,'场02','桌子始终在窗边')
            return scripted_fields(req,content)
        content=assembled_english_prompts(req)
        sid,=content['segments']
        assert sid in ('G06','G07')
        content['segments'][sid]['txt']+='\nThe table stays beside the window.'
        content['decisionNotes']=notes(env,eid,sid,'The table stays beside the window.')
        return scripted_fields(req,content)
    env.codex.script=[reply(dict(kind='change',earliestStage='prompts',instruction=task['instruction'],segments=task['segments'],reason=task['reason'])),reply([task]),*([execute]*20)]
    if pause:
        mark=wb.mark_image
        def pause_after_binding(*args,**kwargs):
            value=mark(*args,**kwargs)
            if ':continuity-bind:' in kwargs.get('command_id',''):
                wb.pause_continuous(wb.continuous_view(eid)['run']['id'])
            return value
        monkeypatch.setattr(wb,'mark_image',pause_after_binding)
    result=wb.fix_after_delivery(eid,task['instruction'],'continuity')
    if pause:
        assert result['state']=='paused',result['reason']
        before=[b['id'] for b in wb.episode_batches(eid)]
        monkeypatch.setattr(wb,'mark_image',mark)
        result=wb.resume_continuous(result['id'],'resume-continuity')
        assert [b['id'] for b in wb.episode_batches(eid)]==before
    assert result['state']=='done',result['reason']
    assert env.art(eid,'script')['adopted_version_id']==script
    content=wb._continuous_version(env.art(eid,'prompts')['adopted_version_id'])
    scene=wb._need(eid,'common-room')['adopted_version_id'] if new_scene else case['scene']
    assert all(any(r['assetVersionId']==scene for r in content['segments'][sid]['refs']) for sid in task['segments'])
    assert all(content['segments'][sid]==entry for sid,entry in old['segments'].items() if sid not in task['segments'])
    html=Path(wb.delivery_view(result['cursor']['deliveryId'])['html']).read_text()
    assert 'G06' in html and 'G07' in html and '本次修改' in html and task['reason'] in html


def test_K_T4_packaged_prompt_and_image_edits_preserve_old_outputs(tmp_path):
    import re
    from test_image_assess import assessment
    env,case=fixture(tmp_path,scene=True);wb=env.wb;eid=case['eid'];old=wb._continuous_version(case['prompts']);old_reviews=wb.prompt_review_status(case['prompts'])
    packaged=wb.package(case['delivery']['deliveryId'],list(old['segments']),'old-package')
    old_zip=Path(packaged['path']).read_bytes();old_html=Path(case['delivery']['html']).read_bytes()
    text='第三段提示词不合格，末两段的场景图也不合格'
    tasks=[dict(type='prompt',segments=['G03'],needKeys=[],scenes=[],instruction='修改第三段提示词',reason='第三段动作更明确'),dict(type='image',segments=[],needKeys=[case['needKey']],scenes=[],instruction='末两段换新场景图',reason='换符合用途的场景图')]
    def execute(req):
        if req.kind=='review' or '退回写稿人' in req.prompt:return directed(req,'codex')
        manifest=wb.run_manifest(req.run_id)['manifest']
        stage=manifest['stage']
        if manifest.get('judgmentKind')=='选图决定':return directed(req,'codex')
        if manifest.get('judgmentKind')=='采用素材逐段选图':
            from workbench.executors.fake import sections
            from workbench.worksheetrefs import fallback_shots
            material=json.loads(sections(req.prompt)['judgment_materials'])
            return reply(dict(segments={r['segment']:fallback_shots(r['candidates'],r['shots'])[:r['limit']] for r in material['segments']}))(req)
        if manifest.get('judgmentKind')=='image_prompt_revision':
            return reply(dict(prompt='空便利店内货架排列整齐，门口和两侧走道清晰可见。'))(req)
        if req.purpose=='image_assess':
            ids=[re.search(r'候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
            return reply([assessment(i,props={},observation='实际原图清晰支持该段场景') for i in ids])(req)
        if '核对当前上游变化' in req.prompt:
            return reply(dict(needsRevision=False,reason='本阶段正文仍适用',quote='First Night Shift' if stage=='publish' else '动作 1'))(req)
        if stage=='publish':return scripted_fields(req,wb._continuous_version(env.art(eid,'publish')['adopted_version_id']))
        if stage=='asset_design':
            content=wb._continuous_version(env.art(eid,stage)['adopted_version_id']);content['assets'][0]['imagePrompt']='新场景：空间清晰的空便利店'
            content['decisionNotes']=notes(env,eid,'场02','新场景：空间清晰的空便利店')
        elif stage=='plan':
            content=wb._continuous_version(env.art(eid,stage)['adopted_version_id']);new=wb._need(eid,case['needKey'])['adopted_version_id']
            for shot in content['shots']:
                if shot.get('startAssetId')==case['scene']:shot['startAssetId']=new
            content['decisionNotes']=notes(env,eid,'G06、G07',new)
        else:
            content=assembled_english_prompts(req)
            sid,=content['segments']
            assert sid in ('G03','G06','G07')
            content['segments'][sid]['txt']+='\nAfu enters the new room.'
            content['decisionNotes']=notes(env,eid,sid,'Afu enters the new room.')
        return scripted_fields(req,content)
    env.codex.script=[reply(dict(kind='change',earliestStage='asset_design',instruction=text,segments=['G03','G06','G07'],reason='两项定点修改')),reply(tasks),*([execute]*45)]
    result=wb.fix_after_delivery(eid,text,'packaged-fix')
    assert result['state']=='done',result['reason']
    final=env.art(eid,'prompts')['adopted_version_id'];content=wb._continuous_version(final);new=wb._need(eid,case['needKey'])['adopted_version_id']
    assert new!=case['scene']
    assert all(any(r['assetVersionId']==new for r in content['segments'][s]['refs']) for s in ('G06','G07'))
    assert content['segments']['G03']!=old['segments']['G03']
    assert all(content['segments'][s]==old['segments'][s] for s in ('G01','G02','G04','G05'))
    current=wb.prompt_review_status(final)
    assert current['G01']['coldRead']['reviewId']==old_reviews['G01']['coldRead']['reviewId']
    # The comparison includes the adopted storyboard references. A replacement
    # changes that basis even when the segment text and its cold read stay valid.
    assert old_reviews['G01']['comparison']['state']=='passed'
    assert current['G01']['comparison']['state']=='needed'
    assert wb.adoption_gate(final)['review'] is None
    assert Path(packaged['path']).read_bytes()==old_zip and Path(case['delivery']['html']).read_bytes()==old_html
    assert wb.delivery_view(result['cursor']['deliveryId'])['revision']==wb.delivery_view(case['delivery']['deliveryId'])['revision']+1
