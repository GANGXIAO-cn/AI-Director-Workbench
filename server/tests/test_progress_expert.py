"""Key-point checks only: isolate the material step, no full production run."""
import json
import pytest
from test_image_assess import fixture, assessment
from test_continuous_images import image_reply, key_fixture
from workbench.db import transaction, connect
from workbench.continuous import Halt, UserChange
from workbench.errors import Conflict


def material_run(env,eid,monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(env.wb,'_advance_continuous',env.wb._continuous_record)
        run=env.wb.start_continuous(eid,target_stage='delivery',command_id='material-only')
    monkeypatch.setattr(env.wb,'_image_fill_records',lambda cid:True)
    return run['id']


def expert_reply(req):
    source=json.loads(req.prompt.split('以下是资料：\n')[1].split('\n上次回包')[0])
    candidate=source['candidates'][0] if source['candidates'] else None
    action='provisional' if candidate else 'text_only' if 'text_only' in source['actions'] else 'wait'
    return dict(action=action,target=source['target'],versionId=candidate['versionId'] if candidate else '',
                issueKeys=[i['type']+':'+i['where'] for i in candidate['critical']] if candidate else [],
                instruction='保留用途，调整为文字描述',reason='已尝试返修，保留问题继续',summary='素材仍有支撑问题，暂用当前候选；后续可换图。')


def test_key_visual_auto_selects_without_expert(tmp_path,monkeypatch):
    env,eid=key_fixture(tmp_path);cid=material_run(env,eid,monkeypatch)
    env.codex.script=[image_reply]*12
    assert env.wb._continuous_images(cid)
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']
    assert len(env.wb.episode_batches(eid))==1
    assert not any(r.purpose=='progress_expert' for r in env.codex.calls)
    assert env.wb._continuous_record(cid)['choice'] is None


def test_three_repairs_then_scoped_provisional_and_user_acceptance(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path);cid=material_run(env,eid,monkeypatch)
    env.wb.save_episode_expert(eid,'antigravity',command_id='expert')
    env.codex.script=[lambda req:image_reply(req,'blocked')]*24
    env.ag.script=[expert_reply]
    assert env.wb._continuous_images(cid)
    assert len(env.wb.episode_batches(eid))==1 and not env.wb.material_issues(eid)
    need=env.wb._need(eid,'bell');vid=need['adopted_version_id']
    # Existing assessments and explicit acceptance remain readable for historical episodes.
    body=assessment(vid,verdict='blocked',critical=[dict(type='usage',where='支撑',text='关键支撑悬空')])
    env.codex.script=[lambda req:image_reply(req,'blocked')]
    env.wb.assess_images(eid,[vid],'historical-assessment',need_key='bell')
    env.wb._material_issue(cid,need,body,'历史暂用记录')
    issues=env.wb.material_issues(eid)
    assert env.wb._material_usable(eid,body,'bell')
    changed=dict(body,critical=[*body['critical'],dict(type='anatomy',where='脚',text='多了一条腿')])
    assert not env.wb._material_usable(eid,changed,'bell')
    with env.db() as db: assert not db.execute('SELECT * FROM accepted_image_issue').fetchall()
    calls=len(env.ag.calls)
    assert env.wb._continuous_images(cid)
    assert len(env.ag.calls)==calls and len(env.wb.episode_batches(eid))==1
    env.wb.accept_material_issue(issues[0]['id'],'accept')
    env.wb.accept_material_issue(issues[0]['id'],'accept')
    with env.db() as db: assert len(db.execute('SELECT * FROM accepted_image_issue').fetchall())==1
    assert env.wb.material_issues(eid)[0]['state']=='accepted'
    # The existing image-acceptance path writes accepted_image_issue directly.
    with transaction(env.wb.db_path) as db:db.execute("UPDATE continuous_issue SET state='provisional' WHERE id=?",(issues[0]['id'],))
    assert env.wb.material_issues(eid)[0]['state']=='accepted'
    with transaction(env.wb.db_path) as db:db.execute('UPDATE asset_need SET adopted_version_id=NULL WHERE episode_id=?',(eid,))
    assert env.wb.material_issues(eid)[0]['state']=='superseded'


def test_expert_invalid_twice_is_cached_and_does_not_accept(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path);cid=material_run(env,eid,monkeypatch)
    env.wb.save_episode_expert(eid,'antigravity',command_id='expert')
    env.ag.script=[lambda req:dict(action='not-allowed')]*2
    need=env.wb._need(eid,'bell')
    assert env.wb._material_judgment(cid,need,[],'无可用候选',['wait']) is None
    assert env.wb._material_judgment(cid,need,[],'无可用候选',['wait']) is None
    assert len([r for r in env.ag.calls if r.purpose=='progress_expert'])==2
    decisions=env.wb.continuous_view(eid)['decisions']
    assert len([d for d in decisions if d['kind']=='expert_judgment'])==2
    assert env.wb._continuous_record(cid)['cursor']['expertUnavailable']


def test_expert_user_change_and_pause_are_respected(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path);cid=material_run(env,eid,monkeypatch)
    env.wb.save_episode_expert(eid,'antigravity')
    need=env.wb._need(eid,'bell')
    def change(req):
        env.wb.set_asset_need(eid,'bell',need['revision'],'edit_prompt','edit',prompt='导演新稿')
        return expert_reply(req)
    env.ag.script=[change]
    with pytest.raises(UserChange):env.wb._material_judgment(cid,need,[],'需处理',['wait'])
    assert env.wb._need(eid,'bell')['image_prompt']=='导演新稿'
    def pause(req):
        env.wb.pause_continuous(cid);return expert_reply(req)
    env.ag.script=[pause]
    with pytest.raises(Halt):env.wb._material_judgment(cid,env.wb._need(eid,'bell'),[],'需处理',['wait'])
    assert env.wb._continuous_record(cid)['state']=='paused'


def test_defaults_and_episode_override(tmp_path):
    env,eid,_=fixture(tmp_path)
    plan=env.wb.executor_defaults();plan['expert']=dict(provider='antigravity',model=None,effort=None)
    env.wb.save_executor_defaults(plan)
    pid=env.wb.episode_view(eid)['episode']['project_id']
    prepared=env.wb.quick_start_prepare(pid,'新一集','prepare')
    args=env.wb._continuous_record(prepared['continuousId'])['cursor']['startArgs']
    assert args['executor_plan']['expert']['provider']=='antigravity'
    env.wb.save_episode_expert(eid,'antigravity',command_id='switch')
    assert env.wb.episode_expert(eid)['provider']=='antigravity'
    assert env.wb.executor_defaults()==plan


def test_running_batch_does_not_block_or_call_expert(tmp_path,monkeypatch):
    from workbench.continuous_images import MaterialWait
    env,eid,_=fixture(tmp_path);cid=material_run(env,eid,monkeypatch)
    def running(cid,need):raise MaterialWait('正在生成',running=True)
    monkeypatch.setattr(env.wb,'_image_generate',running)
    monkeypatch.setattr(env.wb,'_material_judgment',lambda *a:pytest.fail('正常生成不应调用专家'))
    with pytest.raises(Halt):env.wb._continuous_images(cid)
    run=env.wb._continuous_record(cid)
    assert run['state']=='waiting_external' and run['cursor']['blocks'][0]['kind']=='transient'


def test_legacy_visual_continue_rechecks_candidates(tmp_path,monkeypatch):
    env,eid=key_fixture(tmp_path);cid=material_run(env,eid,monkeypatch)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        env.wb._continuous_update(cid,state='waiting_choice',choice=dict(kind='visual',candidates=[dict(versionId='obsolete')]))
    assert env.wb._continuous_record(cid)['choice'] is None
    env.codex.script=[image_reply]*12
    assert env.wb._continuous_images(cid)
    assert env.wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']!='obsolete'


def test_independent_asset_finishes_before_expert_handles_missing(tmp_path,monkeypatch):
    env,eid,_=fixture(tmp_path)
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id='missing',kind='prop',title='缺提示词',description='铃',imagePrompt='门铃',sceneIds=['场01']),dict(id='good',kind='prop',title='可做',description='门铃',imagePrompt='门铃',sceneIds=['场01'])]),'two-assets')
    n=env.wb._need(eid,'missing');env.wb.set_asset_need(eid,'missing',n['revision'],'edit_prompt','clear',prompt='')
    cid=material_run(env,eid,monkeypatch)
    env.codex.script=[image_reply]*10
    env.wb.save_episode_expert(eid,'antigravity')
    def answer(req):
        assert env.wb._need(eid,'good')['adopted_version_id']
        return expert_reply(req)
    env.ag.script=[answer]
    assert env.wb._continuous_images(cid)
    assert env.wb._need(eid,'missing')['decision']=='text_only'


def test_text_only_updates_dependencies_before_set_and_replays(tmp_path,monkeypatch):
    from workbench.executors.fake import draft
    from workbench.continuous_images import MaterialWait
    env,eid,_=fixture(tmp_path)
    items=[dict(id='source',kind='prop',title='缺图',description='门铃',imagePrompt='门铃',sceneIds=['场01'])]
    items += [dict(id=key,kind='scene',title=key,description='门厅',imagePrompt='引用门铃',sceneIds=['场01'],dependsOn=['source']) for key in ('left','right')]
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=items),'deps')
    n=env.wb._need(eid,'source');env.wb.set_asset_need(eid,'source',n['revision'],'edit_prompt','clear',prompt='')
    cid=material_run(env,eid,monkeypatch)
    revised=[dict(i,dependsOn=[],imagePrompt='文字描述门铃') if i['id']!='source' else i for i in items]
    env.codex.script=[lambda req:draft(req,assets=json.dumps(revised,ensure_ascii=False))]
    action=dict(action='text_only',summary='没有候选，改为文字描述')
    actual=env.wb.set_asset_need;failed=[]
    def interrupt(episode_id,key,revision,mode,*args,**kwargs):
        if key=='source' and mode=='text_only' and not failed:
            assert not any('source' in n['depends_on'] for n in env.wb.asset_worksheet(eid)['rows'])
            failed.append(True);raise MaterialWait('中断后接续',running=True)
        return actual(episode_id,key,revision,mode,*args,**kwargs)
    monkeypatch.setattr(env.wb,'set_asset_need',interrupt)
    with pytest.raises(MaterialWait):env.wb._material_text_only(cid,env.wb._need(eid,'source'),action)
    calls=len(env.codex.calls)
    assert env.wb._material_text_only(cid,env.wb._need(eid,'source'),action)
    assert len(env.codex.calls)==calls
    assert env.wb._need(eid,'source')['decision']=='text_only'


def test_progress_migration_preserves_rows_and_foreign_keys(tmp_path):
    import sqlite3
    from workbench.db import SCHEMA,initialize
    path=tmp_path/'legacy.sqlite'
    old=SCHEMA.replace(",'expert_judgment','provisional_use','accept_issue'",'')
    with sqlite3.connect(path) as db:
        db.executescript(old)
        db.execute("INSERT INTO project VALUES('p','项目','','_template','short-film@v7',0,'{}','{}',1,0)")
        db.execute("INSERT INTO episode(id,project_id,display_code,created_at) VALUES('e','p','E01',0)")
        db.execute("INSERT INTO continuous_run(id,episode_id,target_stage,state,created_at,updated_at) VALUES('c','e','delivery','paused',0,0)")
        db.execute("INSERT INTO ai_decision VALUES('d','c','e','assets','choose_image','v','保留记录','{}',NULL,0)")
    initialize(path);initialize(path)
    with connect(path) as db:
        assert db.execute("SELECT reason FROM ai_decision WHERE id='d'").fetchone()[0]=='保留记录'
        assert db.execute("SELECT id FROM ai_decision_before_progress_expert").fetchone()[0]=='d'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        db.execute("INSERT INTO ai_decision VALUES('new','c','e','assets','expert_judgment','x','新判断','{}',NULL,1)")


def test_provisional_shot_evidence_does_not_cover_other_conflicts():
    from workbench.shotrefs import image_review
    asset=dict(sha256='hash',provisional_image_issues=[dict(issue_key='usage:门')])
    review=dict(sha256='hash',observed='实际观察',conflicts=[dict(text='门歪了',provisionalIssue='usage:门')],evidence_path='e')
    assert image_review(lambda path:'实际观察',asset,dict(visual_review=review),'图')==review
    review['conflicts'].append(dict(text='肢体畸形',provisionalIssue='anatomy:腿'))
    with pytest.raises(ValueError):image_review(lambda path:'实际观察',asset,dict(visual_review=review),'图')
