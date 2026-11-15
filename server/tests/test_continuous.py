"""Continuous production with isolated data and fake executors."""
from helpers import Env


def test_B_T1_full_run(tmp_path):
    env=Env(tmp_path)
    _,eid=env.new_episode()
    result=env.wb.start_continuous(eid,command_id='start')
    assert result['state']=='done',result['reason']
    view=env.wb.continuous_view(eid)
    assert len([d for d in view['decisions'] if d['kind']=='adopt'])==6
    assert all(a['adopted_version_id'] for a in env.wb.episode_view(eid)['artifacts'][:6])

import json
from pathlib import Path
import pytest
from workbench.executors.fake import review, directed, draft
from workbench.errors import Conflict

@pytest.mark.parametrize('case,normal', [('B-T2',False),('B-T3',True),('B-T4',False)])
def test_B_T2_B_T3_B_T4_story_review(tmp_path,case,normal):
    env=Env(tmp_path); _,eid=env.new_episode()
    seen=[]
    def reviewer(req):
        seen.append(req)
        count=2 if case=='B-T2' else 3
        return review(req,'return', [dict(category='clarity' if normal else 'source',reason='必须交代钥匙来源')]) if len(seen)<=count else review(req)
    def execute(req):return reviewer(req) if req.kind=='review' else directed(req,'codex')
    env.codex.script=[execute]*80
    result=env.wb.start_continuous(eid,command_id='start')
    assert result['state']=='done',result['reason']
    art=env.art(eid,'story')
    if case=='B-T2': assert art['tasks'][0]['used_rounds']==2
    if case=='B-T3':
        assert any(d['kind']=='waive' for d in env.wb.continuous_view(eid)['decisions'])
        assert art['tasks'][0]['used_rounds']==0
        assert len(art['versions'])==1
    if case=='B-T4':
        assert any('前两轮已用的修法与独审意见' in r.prompt and '必须交代钥匙来源' in r.prompt for r in env.codex.calls)
        assert len(art['versions'])==4

def test_B_T22_no_legacy_authorization_gate(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode()
    assert not hasattr(env.wb,'authorize_expert')
    assert env.wb.start_continuous(eid,command_id='start')['state']=='done'

def test_B_T0_standard_goals_moved_verbatim():
    from workbench.workflow import STANDARD_GOALS
    root=Path(__file__).resolve().parents[2]
    front=(root/'web/src/FirstDraft.tsx').read_text()+(root/'web/src/Assistant.tsx').read_text()
    expected={'story':'按开工资料起草故事稿','characters':'根据已采用的故事稿和项目角色设定，起草本集出场角色的小传。','world':'根据已采用的故事和角色，整理会影响角色选择或事件因果的规则，逐条写明影响及依据。上游未确认的推断单列到备注，避免与已确认规则重复。','treatment':'按已采用的故事、角色及适用世界规则划分场次。逐场写明时间、地点、出场角色的位置、动作、结果、状态变化和估计时长，核对相邻场次的人物与道具连续性，汇总全片时长预算。','script':'根据已采用的分场大纲、故事、角色和世界规则逐场写完整剧本。保留场次顺序和人物行动因果，写清动作、对白及场间衔接。','stress':'根据本集采用稿及开工要求审查剧本，提交冷读复述、审查范围、具体问题、依据和可选返修方案；待导演选择返修范围。'}
    assert STANDARD_GOALS==expected
    assert all(goal not in front for goal in expected.values())
    assert 'goal:standardGoals.data?.story' in front and 'goal:standardGoals.data?.characters' in front
    assert 'config.goal ?? goals.data?.[stage]' in front

@pytest.mark.parametrize('case,first_status',[('B-T7','已解决'),('B-T8','仍存在')])
def test_B_T7_B_T8_stress_repairs(tmp_path,case,first_status):
    env=Env(tmp_path); _,eid=env.new_episode(); verify=[]; selections=[]; reports=[]
    def reply(req):
        manifest=env.wb.run_manifest(req.run_id)['manifest']
        if req.kind=='review': return review(req)
        meta=manifest.get('repairStep')
        if meta and meta['action']=='verify':
            verify.append(req)
            status=first_status if len(verify)==1 else '已解决'
            return dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps([dict(id='I01',status=status,note='已核对本轮修改')]))
        if meta and meta['action']=='check':
            return dict(directed(req,'codex',stage_reply=False),reply='',directorRequest=manifest['stageDialogue']['request'])
        if manifest.get('stageDialogue') and 'optionIndex' in req.prompt:
            index=len(selections); selections.append(index)
            return dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps([dict(issueId='I01',optionIndex=index,newProposal=None,reason='不改核心')]))
        if manifest['stage']=='stress':
            reports.append(req)
            if verify and (first_status=='已解决' or len(verify)>1): return directed(req,'codex')
            return draft(req,retelling='值班员发现异常后交接',scope='01–05',diagnosis='核对来源',issues=json.dumps([dict(id='I01',stage='world',severity='blocking',evidence='柜台有锁',problem='钥匙来源')]),options=json.dumps([dict(issueId='I01',proposal=p,repairScope='场01',changesCore=False) for p in ('写明交接','换成直接呈现')]),productionNotes='[]')
        return directed(req,'codex')
    env.codex.script=[reply]*50; env.claude.script=[reply]*50
    result=env.wb.start_continuous(eid,command_id='start')
    assert result['state']=='done',result['reason']
    assert len(selections)==(1 if case=='B-T7' else 2)
    assert selections==list(range(len(selections)))
    assert len(reports)==1, '返修已验证后不重新生成整份审查报告'
    plans=env.wb.stress_worklist(eid)['plans']
    assert plans[0]['state']=='done'
    assert all(s['state']=='done' for s in plans[0]['steps'])
    assert any(d['kind']=='choose_option' for d in env.wb.continuous_view(eid)['decisions'])

def test_B_T21_manual_repair_defaults_unchanged(tmp_path):
    import inspect
    from test_stress_repairs import fixture
    env,eid,vid=fixture(tmp_path)
    assert inspect.signature(env.wb.start_repair_plan).parameters['max_rounds'].default==3
    assert env.wb.continuous_view(eid)['run'] is None
    plan=env.wb.start_repair_plan(eid,'report','manual',vid,{'I01':0})
    env.drain(plan['runId'])
    assert env.wb.stress_worklist(eid)['plans'][0]['steps'][0]['state']=='adopt'
    assert env.art(eid,'world')['adopted_version_id']!=env.wb.stress_worklist(eid)['plans'][0]['steps'][0]['versionId']

@pytest.mark.parametrize('case', ['B-T5','B-T6','B-T27'])
def test_B_T5_B_T6_B_T27_story_candidates(tmp_path,case):
    env=Env(tmp_path);_,eid=env.new_episode()
    def reviewer(req):
        manifest=env.wb.run_manifest(req.run_id)['manifest']
        with env.db() as db: goal=db.execute('SELECT goal FROM task WHERE id=?',(manifest['taskId'],)).fetchone()[0]
        candidate='第三种表现方式' in goal or '允许调整引发问题' in goal
        passed=candidate and case!='B-T6' and (case!='B-T27' or '允许调整引发问题' in goal)
        return review(req) if passed else review(req,'return',[dict(category='source',reason='钥匙来源与必守原文冲突')])
    env.codex.script=[lambda req:reviewer(req) if req.kind=='review' and env.wb.run_manifest(req.run_id)['manifest']['stage']=='story' else directed(req,'codex')]*100
    result=env.wb.start_continuous(eid,command_id='start')
    assert result['choice'] is None
    if case=='B-T6':
        assert result['state']=='failed' and result['cursor']['repairHistory'] and not result['cursor'].get('pauseHistory')
    else:
        assert result['state']=='done',result['reason']
        selected=env.art(eid,'story')['adopted_version_id']
        assert env.wb.adoption_gate(selected)['adoptable']
        assert any(d['kind']=='expert_judgment' and d['ref']==selected for d in env.wb.continuous_view(eid)['decisions'])

def test_B_T13_quota_resume_same_executor(tmp_path):
    from workbench.executors.fake import quota_exhausted
    env=Env(tmp_path);_,eid=env.new_episode(); calls=[]
    def run(req):
        calls.append(req)
        return quota_exhausted(req) if len(calls)==3 else directed(req,'codex')
    env.codex.script=[run]*10;env.claude.script=[run]*10
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='waiting_external'
    waiting=value['cursor']['queue'][0]
    with env.db() as db: provider=db.execute('SELECT provider FROM run WHERE id=?',(waiting,)).fetchone()[0]
    block=value['cursor']['blocks'][0]
    assert env.wb.changed_continuous_runs(block['resetsAt'] or block['at']+1800)==[value['id']]
    value=env.wb._advance_continuous(value['id'])
    assert value['state']=='done',value['reason']
    with env.db() as db:
        retry=db.execute('SELECT provider FROM run WHERE base_version_id IS NULL AND id<>? AND provider=?',(waiting,provider)).fetchone()
    assert retry
    assert not any(d['kind']=='retry_executor' for d in env.wb.continuous_view(eid)['decisions'])

@pytest.mark.parametrize('case,adopt_change',[('B-T16',False),('B-T18',True)])
def test_B_T16_B_T18_user_change_continues(tmp_path,case,adopt_change):
    env=Env(tmp_path);_,eid=env.new_episode();changed=[]
    def run(req):
        manifest=env.wb.run_manifest(req.run_id)['manifest']
        if manifest['stage']=='world' and not changed:
            art=env.art(eid,'world')
            vid=env.wb.save_manual(art['id'],None,dict(rules=[dict(id='manual',rule='用户明确的规则',choiceImpact='按新规则行动')]),'user')['versionId']
            changed.append(vid)
            if adopt_change: env.wb.adopt(art['id'],vid,None,'user-adopt')
        return directed(req,'codex')
    env.codex.script=[run]*20
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='done',value
    assert env.art(eid,'world')['adopted_version_id']==changed[0]
    if not adopt_change:
        assert any(d['kind']=='apply_user_change' for d in env.wb.continuous_view(eid)['decisions'])
    assert not any(d['kind']=='adopt' and json.loads(d['detail']).get('versionId') in value['cursor'].get('ignoredVersions',[]) for d in env.wb.continuous_view(eid)['decisions'] if d['stage']=='world')

def repair_fixture(tmp_path, *, core=False, forbidden=False, reject_a=False, reject_b=False, invalid_selection=False, unresolved=False):
    from test_stress_repairs import fixture
    env,eid,vid=fixture(tmp_path)
    content=next(v['content'] for v in env.art(eid,'stress')['versions'] if v['id']==vid)
    content['issues']=content['issues'][:1]
    if forbidden: content['issues'][0]['category']='required_setting'
    content['options']=[dict(issueId='I01',proposal='改变钥匙情节' if core else '清楚呈现钥匙',repairScope='场01',changesCore=core),dict(issueId='I01',proposal='换个交接动作',repairScope='场01',changesCore=core)]
    content['productionNotes']=[]
    env.save_adopt(eid,'stress',content,'new-report')
    counts=dict(selection=0,verify=0)
    def reply(req):
        manifest=env.wb.run_manifest(req.run_id)['manifest']; meta=manifest.get('repairStep')
        if req.kind=='review':return review(req)
        def discussion(answer):
            return dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=answer if isinstance(answer,str) else json.dumps(answer))
        if meta and meta['action']=='verify':
            counts['verify']+=1
            return discussion([dict(id='I01',status='仍存在' if unresolved else '已解决',note='核对本轮变化')])
        if meta and meta['action']=='check':
            return dict(directed(req,'codex',stage_reply=False),reply='',directorRequest=manifest['stageDialogue']['request'])
        if manifest.get('stageDialogue'):
            request=manifest['stageDialogue']['request']
            if manifest['stageDialogue'].get('internalJudgment')=='返修选稿':
                return discussion(dict(key='B' if reject_a else 'A',reason='保留结局并说明动作因果'))
            if 'optionIndex' in request:
                index=99 if invalid_selection else counts['selection']
                counts['selection']+=1
                return discussion([dict(issueId='I01',optionIndex=index,newProposal=None,reason='不改核心')])
            return discussion('让值班员先展示钥匙，再完成交接。')
        with env.db() as db: goal=db.execute('SELECT goal FROM task WHERE id=?',(manifest['taskId'],)).fetchone()[0]
        bad=reject_a and '落实此新方案' in goal or reject_b and '采用以下方案修改' in goal
        if bad:return draft(req,rules='[]')
        return directed(req,'codex')
    env.codex.script=[reply]*100;env.claude.script=[reply]*100
    return env,eid,counts

@pytest.mark.parametrize('case', ['B-T9','B-T10','B-T11','B-T28','B-T29'])
def test_B_T9_B_T10_B_T11_B_T28_B_T29_repair_choices(tmp_path,case):
    env,eid,counts=repair_fixture(tmp_path,core=case in ('B-T10','B-T28','B-T29'),forbidden=case=='B-T29',reject_a=case in ('B-T28','B-T29'),reject_b=case=='B-T29',invalid_selection=case=='B-T11',unresolved=case=='B-T9')
    value=env.wb.start_continuous(eid,command_id='start')
    if case=='B-T11':
        assert value['state']=='done' and counts['selection']==2 and counts['verify']==1,value
        return
    if case=='B-T29':
        assert value['state']=='failed' and '柜台有锁' in value['reason'] and value['choice'] is None,value
        return
    if case=='B-T9':
        assert value['state']=='failed' and '仍未解决' in value['reason'] and counts['verify']>=3 and value['choice'] is None
        assert 'I01：核对本轮变化' in value['reason']
        with env.db() as db:
            base=db.execute("SELECT base_version_id FROM run JOIN task ON task.id=run.task_id WHERE task.goal LIKE '只修复被指出的字段%专家修订后仍未解决%' ORDER BY run.rowid LIMIT 1").fetchone()
        decisions=env.wb.continuous_view(eid)['decisions']
        assert base and any(d['kind']=='expert_judgment' and json.loads(d['detail']).get('choice') and d['ref']==base[0] for d in decisions)
        return
    assert value['state']=='done',value['reason']
    assert not any(p['state']=='waiting_choice' for p in value['cursor'].get('pauseHistory',[]))
    decisions=env.wb.continuous_view(eid)['decisions']
    assert any(d['kind']=='expert_judgment' and '动作因果' in d['reason'] for d in decisions)


def test_B_T23_project_methods_not_silently_adopted(tmp_path):
    env=Env(tmp_path);pid,eid=env.new_episode()
    uploaded=env.wb.import_custom_method(pid,'SKILL.md','本项目方法原文'.encode(),title='新方法')
    env.wb.set_custom_methods(pid,'story',[uploaded['versionId']],[])
    view=env.wb.continuous_view(eid)
    assert '1 份' in view['methodNotice']
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='done'
    assert not any('本项目方法原文' in r.prompt for r in env.codex.calls)

def test_B_T24_story_format_gate_only_continuous(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode()
    first=lambda req:draft(req,synopsis='值班员发现抽屉没关，推回并登记。',handoff='进入下一步')
    env.codex.script=[first]
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='done',value['reason']
    assert any('按结构错误修正' in r.prompt for r in env.codex.calls)
    assert len(env.art(eid,'story')['versions'])==2
    other=Env(tmp_path/'manual');_,eid=other.new_episode();other.codex.script=[first]
    assert other.adopt_story(eid)

def test_B_T25_old_script_report_refreshed(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode()
    initial=env.wb.start_continuous(eid,command_id='start')
    report=env.art(eid,'stress')['adopted_version_id']
    script=env.art(eid,'script');content=dict(script['versions'][-1]['content']);content['notes']='用户修改后的版本'
    env.save_adopt(eid,'script',content,'new-script')
    value=env.wb.start_continuous(eid,command_id='new-continuous')
    assert value['state']=='done',value['reason']
    assert env.art(eid,'stress')['adopted_version_id']!=report

def test_B_T17_discussion_does_not_pause(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode();once=[]
    def run(req):
        if not once:
            once.append(True)
            art=env.art(eid,'story')
            task=env.wb.create_task(eid,'story','只讨论故事优点',discussion_only=True,workspace_revision=art['revision'],command_id='user-chat')
            env.drain(task['runId'])
        return directed(req,'codex')
    env.codex.script=[run]
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='done',value['reason']

def test_B_T19_restart_continues_pending_draft(tmp_path):
    from workbench.service import Workbench
    env=Env(tmp_path);_,eid=env.new_episode()
    def run(req):
        cid=env.wb.continuous_view(eid)['run']['id']
        env.wb.pause_continuous(cid)
        return directed(req,'codex')
    env.codex.script=[run]
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='paused'
    draft_id=env.art(eid,'story')['versions'][0]['id']
    env.wb=Workbench(tmp_path/'data',{'codex':env.codex,'claude':env.claude})
    done=env.wb.resume_continuous(value['id'],'resume')
    assert done['state']=='done',done['reason']
    assert len(env.art(eid,'story')['versions'])==1
    assert env.art(eid,'story')['adopted_version_id']==draft_id

def test_B_T26_choice_resets_report_counter_preserves_history(tmp_path):
    from workbench.db import transaction,dumps
    env=Env(tmp_path);_,eid=env.new_episode()
    value=env.wb.start_continuous(eid,command_id='start')
    script=env.art(eid,'script');content=dict(script['versions'][-1]['content']);content['notes']='模拟本轮 AI 返修结果'
    env.save_adopt(eid,'script',content,'auto-fixture')
    with transaction(env.wb.db_path) as db:
        from workbench.stressrepairs import adopted
        cursor=value['cursor'];cursor.update(stressReports=3,attempts={'I01':[dict(round=1,method='历史修法',result='unresolved')]},base=adopted(db,eid))
        cursor['userStamp']=env.wb._continuous_user_stamp(db,eid,cursor['ownedRuns'])
        db.execute("UPDATE continuous_run SET state='paused',cursor=? WHERE id=?",(dumps(cursor),value['id']))
    done=env.wb.resume_continuous(value['id'],'resume')
    assert done['state']=='done',done['reason']
    assert done['cursor']['stressReports']==1
    assert done['cursor']['attempts']['I01'][0]['method']=='历史修法'

def test_B_T19_restart_continues_open_repair(tmp_path):
    from workbench.service import Workbench
    env,eid,_=repair_fixture(tmp_path)
    original=env.codex.script[0];once=[]
    def run(req):
        meta=env.wb.run_manifest(req.run_id)['manifest'].get('repairStep')
        if meta and meta['action']=='revise' and not once:
            once.append(True)
            env.wb.pause_continuous(env.wb.continuous_view(eid)['run']['id'])
        return original(req)
    env.codex.script=[run]*100
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='paused',value['reason']
    assert value['cursor']['planId']
    before=len(env.art(eid,'world')['versions'])
    env.wb=Workbench(tmp_path/'data',{'codex':env.codex,'claude':env.claude})
    assert not env.wb.pending_runs()
    done=env.wb.resume_continuous(value['id'],'resume')
    assert done['state']=='done',done['reason']
    assert len(env.art(eid,'world')['versions'])==before

def test_B_T16_edit_while_waiting_for_choice_continues(tmp_path):
    env,eid,_=repair_fixture(tmp_path,core=True)
    from test_continuous_wake import run_record
    run_record(env,eid)
    art=env.art(eid,'world');content=dict(art['versions'][-1]['content']);content['notes']='用户新要求'
    env.wb.save_manual(art['id'],art['adopted_version_id'],content,'user-edit')
    assert env.wb.continuous_view(eid)['run']['state']=='running'
    from workbench.app import Worker
    Worker(env.wb,inline=True).continue_changed()
    current=env.wb.continuous_view(eid)['run']
    assert current['state']!='paused'
    art=env.art(eid,'world')
    assert next(v for v in art['versions'] if v['id']==art['adopted_version_id'])['content'].get('notes')=='用户新要求'

def test_B_T7_repair_story_gets_independent_review(tmp_path):
    env,eid,_=repair_fixture(tmp_path)
    report=env.art(eid,'stress');content=dict(next(v['content'] for v in report['versions'] if v['id']==report['adopted_version_id']))
    content['issues'][0]['stage']='story'
    env.save_adopt(eid,'stress',content,'story-target')
    value=env.wb.start_continuous(eid,command_id='start')
    assert value['state']=='done',value['reason']
    story=env.art(eid,'story');adopted=next(v for v in story['versions'] if v['id']==story['adopted_version_id'])
    assert adopted['reviews'] and adopted['gate']['adoptable']


def test_B47_api_write_wakes_active_run_without_resume(tmp_path):
    from fastapi.testclient import TestClient
    from workbench.app import create_app
    env=Env(tmp_path);_,eid=env.new_episode()
    env.wb.start_continuous(eid,command_id='complete-initial-stages')
    from test_continuous_wake import run_record
    run_record(env,eid)
    app=create_app(env.wb,token='test',inline_worker=True)
    art=env.art(eid,'world')
    content=dict(art['versions'][-1]['content'],notes='新的用户要求')
    with TestClient(app) as client:
        response=client.post('/api/cmd/save_manual',headers={'x-adw-token':'test'},json=dict(
            artifact_id=art['id'],base_version_id=art['adopted_version_id'],content=content,command_id='api-change'))
        assert response.status_code==200,response.text
    assert env.art(eid,'world')['adopted_version_id']==response.json()['versionId']
    assert env.wb.continuous_view(eid)['run']['state']!='paused'


def test_B47_explicit_pause_is_preserved_on_user_edit(tmp_path):
    env,eid,_=repair_fixture(tmp_path,core=True)
    from test_continuous_wake import run_record
    cid=run_record(env,eid)
    env.wb.pause_continuous(cid)
    env.wb._advance_continuous(cid)
    art=env.art(eid,'world')
    env.wb.save_manual(art['id'],art['adopted_version_id'],dict(art['versions'][-1]['content'],notes='暂停期间改稿'),'edit')
    from workbench.app import Worker
    Worker(env.wb,inline=True).continue_changed()
    assert env.wb.continuous_view(eid)['run']['state']=='paused'


def test_B47_user_edit_between_gate_and_adoption_is_not_overwritten(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode();changed=[]
    original=env.wb._adopt
    def adopt(aid,vid,*args,**kwargs):
        art=env.wb._continuous_art(eid,'world')
        if kwargs.get('actor')=='ai' and art and aid==art['id'] and not changed:
            changed.append(env.wb.save_manual(aid,None,dict(rules=[dict(id='manual',rule='用户的规则',choiceImpact='遵循用户选择')]),'race-edit')['versionId'])
        return original(aid,vid,*args,**kwargs)
    env.wb._adopt=adopt
    result=env.wb.start_continuous(eid,command_id='start')
    assert result['state']=='done',result
    assert env.art(eid,'world')['adopted_version_id']==changed[0]
    world_adoptions=[d for d in env.wb.continuous_view(eid)['decisions'] if d['stage']=='world' and d['kind']=='adopt']
    assert [json.loads(d['detail'])['versionId'] for d in world_adoptions]==changed


def test_B47_auto_dialogue_draft_is_adopted_and_continues(tmp_path):
    from workbench.app import Worker
    env=Env(tmp_path);_,eid=env.new_episode();changed=[]
    def reply(req):
        manifest=env.wb.run_manifest(req.run_id)['manifest']
        if manifest['stage']=='world' and not changed:
            changed.append(None)
            art=env.art(eid,'world')
            job=env.wb.create_task(eid,'world','【测试：改稿】改成用户指定的规则',auto_dialogue=True,workspace_revision=art['revision'],command_id='chat-change')
            Worker(env.wb,inline=True).submit(job['runId'])
            changed[0]=env.art(eid,'world')['versions'][-1]['id']
        return directed(req,'codex')
    env.codex.script=[reply]*30
    result=env.wb.start_continuous(eid,command_id='start')
    assert result['state']=='done',result
    assert env.art(eid,'world')['adopted_version_id']==changed[0]
    assert any(d['kind']=='apply_user_change' for d in env.wb.continuous_view(eid)['decisions'])


@pytest.mark.parametrize('change',['unlock','spec'])
def test_B47_unlock_and_spec_changes_reconcile_automatically(tmp_path,change):
    # B4.7：解锁修改和确认开工规格变更也触发接续，保留已采用稿件。
    from workbench.app import Worker
    env=Env(tmp_path);_,eid=env.new_episode()
    env.wb.start_continuous(eid,command_id='complete-initial-stages')
    from test_continuous_wake import run_record
    run_record(env,eid)
    art=env.art(eid,'world')
    if change=='unlock':
        env.wb.set_stage_edit(art['id'],True,art['revision'],'unlock')
    else:
        env.wb.episode_spec_change(eid,'aspectRatio','16:9','spec',confirm=True)
    run=env.wb.continuous_view(eid)['run']
    assert run['state']=='running' and run['cursor']['pendingUserChange']
    Worker(env.wb,inline=True).continue_changed()
    run=env.wb.continuous_view(eid)['run']
    assert run['state']!='paused' and not run['cursor']['pendingUserChange']
    assert env.art(eid,'world')['adopted_version_id']==art['adopted_version_id']


def test_product_code_explicit_description_compatibility():
    from workbench.worksheet import asset_product_code
    from workbench.stages import normalize
    old = dict(id='bedroom', kind='scene', description='床头杯。productCode: 02。')
    assert asset_product_code(old) == '02'
    assert 'productCode' not in old
    assert normalize('asset_design', 'assets', json.dumps([old]))[0]['productCode'] == '02'
    assert asset_product_code(dict(description='商品02，两个杯子')) is None
    assert asset_product_code(dict(description='productCode: 02; productCode: 03')) is None
    assert asset_product_code(dict(old, productCode='03')) == '03'
