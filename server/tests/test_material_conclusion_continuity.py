"""Same evidence continues; new conflicts cannot borrow an earlier clearance."""
import json
import pytest
from workbench.db import transaction
from workbench.executors.fake import review
from workbench.errors import Rejected
from test_continuous_finishing import finishing_fixture
from test_image_assess import assessment,reply


def limited_fixture(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True)
    wb=env.wb;image=case['img']['versionId'];eid=case['eid']
    content=wb._continuous_version(vid)
    content['segments']['G01']['txt']=content['segments']['G01']['txt'].replace('【参考素材】','【参考素材】\n只参考身份；服装使用本场指定的蓝衣。')
    vid=wb.save_manual(env.art(eid,'prompts')['id'],vid,content,'use-scope')['versionId'];wb._continuous_internal_version(cid,vid)
    env.codex.script=[reply([assessment(image,verdict='limited',minor=[dict(where='衣服',text='原图红衣仅用于身份，不承担蓝衣服装状态')],props={},fitness='身份参考可用，服装按场景')])]
    wb.assess_images(eid,[image],'limited-assessment',continuous_id=cid)
    return env,case,cid,vid,image


def test_limited_use_reads_current_scope_and_reuses_observation(tmp_path):
    env,case,cid,vid,image=limited_fixture(tmp_path);wb=env.wb
    before=len(env.codex.calls)
    def judge(req):
        assert '只参考身份；服装使用本场指定的蓝衣。' in req.prompt
        assert '原图红衣仅用于身份' in req.prompt
        assert 'reference' in req.prompt and '不重新做图片质量选优' in req.prompt
        return reply([])(req)
    env.codex.script=[judge]
    final=wb._continuous_shot_observations(cid,vid,{'G01'})
    assert len(env.codex.calls)==before
    assert wb._continuous_shot_observations(cid,final,{'G01'})==final
    assert len(env.codex.calls)==before
    assert wb._continuous_version(final)['segments']['G01']['txt']==wb._continuous_version(vid)['segments']['G01']['txt']


def test_independent_review_inherits_usage_but_unrelated_issue_not_waived(tmp_path):
    from workbench.materialcontext import usage_clearances
    env,case,cid,vid,image=limited_fixture(tmp_path);wb=env.wb;eid=case['eid']
    env.codex.script=[reply([])]
    final=wb._continuous_shot_observations(cid,vid,{'G01'})
    final=wb.save_shot_observation(final,'G01',image,wb.asset_version(image)['sha256'],['S01','S02'],'历史用途观察',{},[],command_id='historical-clearance')['versionId']
    wb._continuous_internal_version(cid,final)
    env.claude.script=[lambda req:review(req,'return',[dict(category='clarity',reason='参考图是红衣，需要换蓝衣参考')])]
    job=wb.start_prompt_review(final,'G01','cold_read','review-use');env.drain(job['runId'])
    manifest=wb.run_manifest(job['runId'])['manifest']
    assert any(i['role']=='material_use_conclusions' for i in manifest['inputs'])
    assert any(i['role']=='review_boundary' for i in manifest['inputs'])
    issue=wb.prompt_review_status(final)['G01']['coldRead']['issues'][0]
    with transaction(wb.db_path) as db:db.execute('UPDATE issue SET blocking=1 WHERE id=?',(issue['id'],))
    with env.db() as db:use=usage_clearances(db,eid,'G01',wb._continuous_version(final)['segments']['G01'])[0]
    match={k:use[k] for k in ('assetVersionId','assessmentId','observationId','sourceIssue')}
    match['quote']='只参考身份；服装使用本场指定的蓝衣。'
    with pytest.raises(Rejected):wb.waive_segment_issue(issue['id'],'错误证据','wrong-source',usage_issue=dict(match,sourceIssue='新发现身份错误'))
    with pytest.raises(Rejected):wb.waive_segment_issue(issue['id'],'无原句','wrong-quote',usage_issue=dict(match,quote='正文没有的话'))
    env.codex.script=[reply(dict(usageIssue=match,acceptedIssue=None,provisionalIssue=None,reason='正文明确限定身份，服装另行指定'))]
    wb._accept_known_image_review_issues(cid,final,'G01','cold_read')
    assert wb.prompt_review_status(final)['G01']['coldRead']['issues'][0]['waiver_reason']
    with env.db() as db:assert db.execute('SELECT count(*) FROM accepted_image_issue WHERE asset_version_id=?',(image,)).fetchone()[0]==0


def test_provisional_observation_contract_accepts_correct_field(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb;image=case['img']['versionId']
    final=wb.save_shot_observation(vid,'G01',image,wb.asset_version(image)['sha256'],['S01','S02'],'阿福可见',{},
        [dict(text='保留原问题',provisionalIssue='usage:衣服')],command_id='provisional-save')
    # Historical observation conflicts do not become prompt format errors.
    assert not any('图片仍有冲突' in e for e in wb.adoption_gate(final['versionId'])['formatErrors'])


def test_note_receipt_ignores_observation_metadata_but_tracks_prose():
    from workbench.promptrepairs import note_proof
    from test_production_notes import basis
    b=basis();content=dict(segments={'G01':dict(txt='正文',refs=[])},productionNoteResponses=[])
    proof=note_proof(content,b)
    content['notes']='管理备注';content['segments']['G01']['refs']=[dict(visualReviewId='new')]
    b['stages']['prompts']=content
    assert note_proof(content,b)==proof
    content['segments']['G01']['txt']='改了内容'
    assert note_proof(content,b)!=proof


def test_repeated_wording_does_not_force_structural_rewrite(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb;calls=[];rounds=[]
    def status(*args):
        rounds.append(1)
        if len(rounds)==4:return {s:dict(ok=True) for s in ('G01','G02')}
        return {'G01':dict(ok=False,coldRead=dict(issues=[dict(blocking=True,reason='措辞问题')]),comparison=dict(issues=[])), 'G02':dict(ok=True)}
    monkeypatch.setattr(wb,'_continuous_review_passes',status)
    monkeypatch.setattr(wb,'_continuous_json',lambda *a,**k:dict(kind='wording',reason='仅需措辞调整'))
    monkeypatch.setattr(wb,'_continuous_prompt_revision',lambda *a,**k:calls.append('wording') or vid)
    monkeypatch.setattr(wb,'_continuous_structure',lambda *a,**k:pytest.fail('不能因次数升级拆镜'))
    assert wb._continuous_prompt_reviews(cid,vid)==vid
    assert calls==['wording']*3


def test_exact_accepted_issue_does_not_depend_on_model_copying_receipt_key(tmp_path):
    env,case,cid,vid,image=limited_fixture(tmp_path);wb=env.wb
    issue='原图红衣仅用于身份，不承担蓝衣服装状态'
    decision=wb._continuous_log(cid,'assets','choose_image',image,'用户接受',{})
    with transaction(wb.db_path) as db:db.execute('INSERT INTO accepted_image_issue VALUES(?,?,?,?,?)',(image,'usage:衣服',issue,decision,wb.clock()))
    env.codex.script=[reply([issue])]
    final=wb._continuous_shot_observations(cid,vid,{'G01'})
    assert final==vid
    with env.db() as db:
        assert db.execute('SELECT text FROM accepted_image_issue WHERE asset_version_id=?',(image,)).fetchone()[0]==issue
