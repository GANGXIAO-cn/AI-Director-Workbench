"""H-T1/H-T6c: image assessment contract, actual attachments and recorded evidence."""
import json
import pytest
from helpers import Env
from workbench.executors.fake import draft
from workbench.errors import Rejected
from workbench.imageassess import validate_assessments


def assessment(vid,**values):
    return dict(dict(versionId=vid,verdict='usable',value='主体完整',fitness='支持本条目用途',critical=[],minor=[],observation='铜制门铃位于木门上',props={'bell':'挂在木门'},recommendRequired=False,reason='可用于本场空景'),**values)


def fixture(tmp_path):
    env=Env(tmp_path);pid,eid=env.new_episode();env.adopt_story(eid);env.script(eid)
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id='bell',kind='prop',title='门铃',description='铜制门铃',imagePrompt='铜制门铃',sceneIds=['场01'])]),'design')
    image=env.image(pid,eid,'门铃','prop')['versionId']
    return env,eid,image


def reply(value):
    return lambda req:dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps(value,ensure_ascii=False))


def test_H_T1_assessment_retries_invalid_critical_and_freezes_approved_method(tmp_path):
    env,eid,vid=fixture(tmp_path)
    issue=dict(type='usage',where='门上',text='门铃悬空，支撑关系错误')
    env.codex.script=[reply([assessment(vid,critical=[issue])]),reply([assessment(vid,critical=[issue],verdict='blocked')])]
    before=len(env.codex.calls)
    result=env.wb.assess_images(eid,[vid],'assess',need_key='bell')
    assert result[0]['verdict']=='blocked' and len(env.codex.calls)-before==2
    rows=env.wb.image_assessments(eid,'bell');assert len(rows)==1
    manifest=env.wb.run_manifest(rows[0]['run_id'])['manifest']
    assert manifest['purpose']=='image_assess' and manifest['stageDialogue']['readOnly']
    methods=[e for e in manifest['inputs'] if e['name']=='image_assess.md']
    assert len(methods)==1
    from workbench import unifiedmethods
    assert env.wb.store.read_blob(methods[0]['sha256'])==(unifiedmethods.LOCAL/'image_assess.md').read_bytes()
    assert any(e['role']=='accepted_image_issue' for e in manifest['inputs'])
    assert any(e['name'].startswith('候选-'+vid) and e.get('binary') for e in manifest['inputs'])
    assert env.codex.calls[-1].images and all(p.is_file() for p in env.codex.calls[-1].images)
    assert env.wb.assess_images(eid,[vid],'assess',need_key='bell')==result
    assert len(env.codex.calls)-before==2


@pytest.mark.parametrize('kind',['identity','anatomy','usage'])
def test_H_T6c_critical_types_require_blocked(kind):
    item=assessment('v',critical=[dict(type=kind,where='主体',text='明确缺陷')])
    with pytest.raises(Rejected,match='blocked'):validate_assessments([item],['v'])
    assert validate_assessments([dict(item,verdict='blocked')],['v'])
    limited=assessment('v',verdict='limited',minor=[dict(where='远景',text='背景颜色轻微差异，不影响本次用途')])
    assert validate_assessments([limited],['v'])[0]['critical']==[]


def test_H_T1_invalid_reply_twice_has_no_assessment_record(tmp_path):
    env,eid,vid=fixture(tmp_path);env.codex.script=[reply([assessment(vid,observation='')])]*2
    before=len(env.codex.calls)
    with pytest.raises(Rejected,match='格式重试'):env.wb.assess_images(eid,[vid],'bad',need_key='bell')
    assert len(env.codex.calls)-before==2 and env.wb.image_assessments(eid)==[]


def test_H_single_image_edit_existing_entry_keeps_original(tmp_path):
    env,eid,vid=fixture(tmp_path);original=env.wb.asset_version(vid)
    job=env.wb.create_batch(eid,'fake-image','edit',[dict(prompt='只修正门铃支撑，保留其他部分',inputs=[vid],target=dict(name='门铃修订',category='prop'),segments=[])],'64x64',command_id='single-edit')
    env.wb.authorize_batch(job['batchId'],job['summaryHash'],'authorize')
    env.wb.execute_batch(job['batchId'])
    result=env.wb.batch_view(job['batchId'])
    assert result['state']=='completed' and result['operation']=='edit'
    assert result['spec']['items'][0]['inputs']==[vid]
    assert env.wb.asset_version(vid)['sha256']==original['sha256']


def test_H_T0_probe_freezes_three_originals_without_calling_models(tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'validation/workflows/image_read_probe.py'
    spec=importlib.util.spec_from_file_location('image_read_probe',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    files=[]
    from helpers import png_bytes
    for i in range(3):
        image=tmp_path/f'{i}.png';image.write_bytes(png_bytes(64,64,(i*30,50,100)));files.append(str(image))
    out=tmp_path/'probe';out.mkdir()
    monkeypatch.setattr(module.tempfile,'mkdtemp',lambda **kw:str(out))
    monkeypatch.setattr(module.CodexExecutor,'start',lambda *a:pytest.fail('offline validation must not call a real model'))
    assert module.main(['--images',*files])==0
    records=json.loads((out/'inputs.json').read_text());assert len(records)==3
    for original,record in zip(files,records):assert Path(original).read_bytes()==Path(record['path']).read_bytes()


def test_H_image_assess_run_purpose_migration_retains_history_and_foreign_keys(tmp_path):
    import sqlite3
    from workbench.db import SCHEMA,initialize,connect
    path=tmp_path/'legacy.sqlite'
    old=SCHEMA.replace(",'image_assess'",'').replace("'image_assess',",'')
    with sqlite3.connect(path) as db:
        db.executescript(old)
        db.execute("INSERT INTO project VALUES('p','项目','','_template','short-film@v7',0,'{}','{}',1,0)")
        db.execute("INSERT INTO episode(id,project_id,display_code,created_at) VALUES('e','p','E01',0)")
        db.execute("INSERT INTO artifact(id,project_id,episode_id,stage) VALUES('a','p','e','story')")
        db.execute("INSERT INTO task(id,project_id,episode_id,artifact_id,stage,goal,max_rounds,executor_plan,created_at) VALUES('t','p','e','a','story','历史任务',1,'{}',0)")
        db.execute("INSERT INTO run(id,task_id,provider,purpose,status,manifest_relpath,input_hash,session_mode,isolation_level,created_at) VALUES('r','t','codex','create','succeeded','runs/r/input.json','hash','new','session',0)")
    initialize(path);initialize(path)
    with connect(path) as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute("SELECT status FROM run WHERE id='r'").fetchone()[0]=='succeeded'
        assert db.execute("SELECT id FROM run_before_nine_stages").fetchone()[0]=='r'
        db.execute("INSERT INTO run(id,task_id,provider,purpose,status,manifest_relpath,input_hash,session_mode,isolation_level,created_at) VALUES('new','t','codex','image_assess','queued','runs/new/input.json','newhash','ephemeral','session',1)")


def test_assessment_task_is_internal_not_director_chat(tmp_path):
    """实测 134：看图评估的指令与 JSON 回包不进导演助手对话，也不出现在“记为决定”候选里。"""
    env,eid,vid=fixture(tmp_path)
    env.codex.script=[reply([assessment(vid)])]
    env.wb.assess_images(eid,[vid],'assess',need_key='bell')
    art=next(a for a in env.wb.episode_view(eid)['artifacts'] if a['stage']=='asset_design')
    flags=[(t['internal'],t['runs'][0]['purpose'] if t['runs'] else None) for t in art['tasks']]
    assert (True,'image_assess') in flags and all(i==(p=='image_assess') for i,p in flags)
    assert not any('image_assess.md' in c['text'] for c in env.wb.decision_candidates(art['id']))


def test_assessment_uploads_small_images_and_no_cast_for_props(tmp_path):
    """实测 136：道具评估不附角色对照图；大图缩到长边 1280 再传。"""
    from io import BytesIO
    from PIL import Image
    env,eid,_=fixture(tmp_path);pid=env.wb.episode_view(eid)['episode']['project_id']
    big=env.image(pid,eid,'门铃大图','prop',size=(2048,3072))['versionId']
    milo=env.image(pid,eid,'MILO','identity')['versionId'];leo=env.image(pid,eid,'LEO','identity')['versionId']
    matrix=env.wb._cast_matrix
    def with_roster(db,episode):
        value=matrix(db,episode);value['roster'].update(milo=dict(assetVersionId=milo),leo=dict(assetVersionId=leo));return value
    env.wb._cast_matrix=with_roster
    env.codex.script=[reply([assessment(big)]),reply([assessment(big)])]
    env.wb.assess_images(eid,[big],'assess-big',need_key='bell')
    env.wb.assess_images(eid,[big],'assess-milo',context=dict(kind='character',role_id='milo',title='MILO'))
    runs=[r['run_id'] for r in env.wb.image_assessments(eid)]
    def images(rid):return [e for e in env.wb.run_manifest(rid)['manifest']['inputs'] if e.get('binary')]
    prop=next(images(r) for r in runs if not any(e['role']=='cast_baseline_image' for e in images(r)))
    cast=next(images(r) for r in runs if any(e['role']=='cast_baseline_image' for e in images(r)))
    assert [e['name'].split('.')[0] for e in cast if e['role']=='cast_baseline_image']==['角色对照-milo']
    with Image.open(BytesIO(env.wb.store.read_blob(prop[0]['sha256']))) as image:assert max(image.size)==1280


def test_schema_error_is_retried_once_with_format_request(tmp_path):
    """实测 143：执行器没按格式回包（schema）时，先请它按格式重回一次，成功则运行正常完成。"""
    from workbench.executors.base import ExecOutcome
    env,eid,vid=fixture(tmp_path)
    env.codex.script=[lambda req:ExecOutcome(ok=False,error_class='schema',diagnostic='执行器回复了文字，但没有按要求的格式回包'),reply([assessment(vid)])]
    result=env.wb.assess_images(eid,[vid],'assess-schema',need_key='bell')
    assert result[0]['verdict']=='usable'
    run=env.wb.image_assessments(eid,'bell')[-1]['run_id']
    with env.db() as db:assert tuple(db.execute('SELECT format_attempts,status FROM run WHERE id=?',(run,)).fetchone())==(1,'succeeded')
    assert '没有按要求的格式回包' in env.codex.calls[-1].prompt
    assert '"previousResponse": null' in env.codex.calls[-1].prompt
