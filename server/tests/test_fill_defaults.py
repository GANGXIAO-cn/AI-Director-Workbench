"""W-A1–W-A6 / W-B1–W-B4: defaults and real service side effects."""
import json
import pytest
from helpers import Env
from workbench.db import dumps
from workbench.executors.fake import draft
from workbench.imagechoices import validate_request

@pytest.mark.parametrize('reason',['','用户自己的说法'])
def test_W_A1_required_image_default(reason):
    result=validate_request(dict(reason=reason),None)
    assert result['reason']==(reason or '用户指定必用') and result['selectedBy']=='user'
    assert result['fitness']=='not_tested'

@pytest.mark.parametrize('reason',['','用户自己的说法'])
def test_W_A2_text_only_default(tmp_path,reason):
    from test_asset_worksheet import prepare
    env,eid,_,_=prepare(tmp_path)
    need=next(r for r in env.wb.asset_worksheet(eid)['rows'] if r['need_key']=='coat')
    env.wb.set_asset_need(eid,'coat',need['revision'],'text_only','text',reason=reason)
    row=next(r for r in env.wb.asset_worksheet(eid)['rows'] if r['need_key']=='coat')
    assert row['decision_reason']==(reason or '导演选择只用文字')

@pytest.mark.parametrize('reason',['','用户自己的说法'])
def test_W_A3_optional_stage_default(tmp_path,reason):
    env=Env(tmp_path);_,eid=env.new_episode()
    env.wb.set_optional_stage(eid,'world',True,reason)
    with env.db() as db:value=db.execute('SELECT reason FROM optional_stage_choice WHERE episode_id=? AND stage=?',(eid,'world')).fetchone()[0]
    assert value==(reason or '导演选择跳过本步')

@pytest.mark.parametrize('explicit',[False,True])
def test_W_A4_conflict_suggestion_and_user_quote(tmp_path,explicit):
    from test_kickoff_checks import setup_case,report,IDEA
    env=Env(tmp_path);source=setup_case(env);env.codex.script=[report]
    view=env.wb.kickoff_check_view(source)
    result=env.wb.kickoff_check_run(source,view['basis'],'codex')
    suggested=result['checks'][0]['suggested']
    quote='主角' if explicit else suggested['requestQuote']
    saved=env.wb.kickoff_check_decide(source,view['basis'],suggested['settingKey'],quote,True)
    assert saved['decisions']['dialogue']['requestQuote']==('主角' if explicit else IDEA)


def test_W_A4_non_substring_has_no_suggestion(tmp_path):
    from test_kickoff_checks import setup_case
    env=Env(tmp_path);source=setup_case(env);view=env.wb.kickoff_check_view(source)
    with env.db() as db:
        db.execute('INSERT INTO kickoff_check(basis,project_id,display_code,checks) VALUES(?,?,?,?)',(view['basis'],source['project_id'],'E01',dumps([dict(key='dialogue',verdict='conflict',requestQuote='杜撰内容')])));db.commit()
    assert 'suggested' not in env.wb.kickoff_check_view(source)['checks'][0]

@pytest.mark.parametrize('reason',['','用户自己的说法'])
def test_W_A5_setting_reason_default(tmp_path,reason):
    env=Env(tmp_path);pid=env.wb.create_project('_template','默认理由')['projectId']
    default='连续制作：两份候选都与此设定冲突，导演选择修改设定'
    env.wb.propose_setting_change(pid,'dialogue','允许对白','variable',reason,'setting',reason_default=default)
    assert env.wb.project_view(pid)['settings'][0]['user_reason']==(reason or default)

@pytest.mark.parametrize('usage',['','用户自己的说法'])
def test_W_A6_library_usage_default(tmp_path,usage):
    from workbench.images import png_bytes
    env=Env(tmp_path);pid=env.wb.create_project('_template','默认用途')['projectId']
    result=env.wb.upload_library_image(pid,'图片.png',png_bytes(16,16),'state',usage)
    with env.db() as db:actual=db.execute('SELECT usage FROM library_entry WHERE id=?',(result['entryId'],)).fetchone()[0]
    assert actual==(usage or '导演上传')


def supplements_fixture(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode();env.adopt_story(eid)
    items=[dict(subject='手机.位置',statement='手机在左侧口袋',scenes=['场01'],reason='明确位置'),dict(subject='门.颜色',statement='门是蓝色',scenes=['场01'],reason='明确颜色')]
    vid,_=env.save_adopt(eid,'script',dict(scenes=[dict(id='场01',heading='夜',action='他拿出手机。')],supplements=items),'script')
    advance=env.wb._advance_continuous
    env.wb._advance_continuous=env.wb._continuous_record
    cid=env.wb.start_continuous(eid,command_id='prepare')['id']
    env.wb._advance_continuous=advance
    return env,eid,cid,items,vid


def test_W_B1_nonconflicting_supplements_confirmed(tmp_path):
    env,eid,cid,_,_=supplements_fixture(tmp_path)
    env.wb._continuous_confirm_supplements(cid,'script')
    assert [r['state'] for r in env.wb.production_supplements(eid)]==['confirmed','confirmed']
    assert not any('未经导演确认' in r['problem'] for r in env.wb.delivery_problems(eid)[0])
    assert len([r for r in env.wb.continuous_view(eid)['decisions'] if r['kind']=='confirm_supplement'])==2


def test_W_B4_manual_supplements_stay_proposed(tmp_path):
    env,eid,_,_,_=supplements_fixture(tmp_path)
    assert all(r['state']=='proposed' for r in env.wb.production_supplements(eid))


def conflict_fixture(tmp_path):
    env,eid,cid,items,vid=supplements_fixture(tmp_path)
    old=env.wb.production_supplements(eid)[0];env.wb.decide_supplement(old['id'],'confirm',command_id='confirm')
    new=dict(items[0],statement='手机在手袋中')
    env.codex.script=[lambda req:draft(req,supplements=json.dumps([new]))]
    env.wb._continuous_create(cid,'script',base=vid)
    return env,eid,cid,old,new


def test_W_B2_conflict_keeps_existing_rejects_new(tmp_path):
    env,eid,cid,old,new=conflict_fixture(tmp_path)
    env.codex.script=[lambda req:dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps(dict(conflict=True,keep='existing',reason='已采用剧本写口袋')))]
    candidate=env.art(eid,'script')['versions'][-1]['id']
    env.wb._continuous_adopt(cid,'script',candidate)
    records=env.wb.production_supplements(eid)
    assert next(r for r in records if r['id']==old['id'])['state']=='confirmed'
    assert next(r for r in records if r['statement']==new['statement'])['state']=='rejected'


def test_W_B3_invalid_judgment_blocks_with_both_statements(tmp_path):
    from workbench.continuous import Halt
    env,eid,cid,old,new=conflict_fixture(tmp_path)
    env.codex.script=[lambda req:dict(draft(req),kind='discussion',changes=[],directorRequest='',reply='无效')]*2
    before=len(env.codex.calls)
    candidate=env.art(eid,'script')['versions'][-1]['id']
    with pytest.raises(Halt):env.wb._continuous_adopt(cid,'script',candidate)
    run=env.wb._continuous_record(cid)
    assert run['state']=='blocked' and old['statement'] in run['reason'] and new['statement'] in run['reason']
    assert len(env.codex.calls)-before==2


def test_W_B1_continuous_adoption_automatically_confirms(tmp_path):
    env=Env(tmp_path);_,eid=env.new_episode();env.adopt_story(eid)
    item=dict(subject='手机.位置',statement='手机在左侧口袋',scenes=['场01'],reason='明确位置')
    env.codex.script=[lambda req:draft(req,scenes=json.dumps([dict(id='场01',heading='夜',action='他拿出手机。')]),supplements=json.dumps([item]))]
    result=env.wb.start_continuous(eid,command_id='start')
    assert result['state']=='done',result['reason']
    assert env.wb.production_supplements(eid)[0]['state']=='confirmed'
    assert not any('未经导演确认' in r['problem'] for r in env.wb.delivery_problems(eid)[0])


def test_W_B2_new_statement_replaces_existing_before_adoption(tmp_path):
    env,eid,cid,old,new=conflict_fixture(tmp_path)
    env.codex.script=[lambda req:dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps(dict(conflict=True,keep='new',reason='新稿明确改为手袋')))]
    candidate=env.art(eid,'script')['versions'][-1]['id']
    assert any('说法不同' in error for error in env.wb.adoption_gate(candidate)['formatErrors'])
    env.wb._continuous_adopt(cid,'script',candidate)
    records=env.wb.production_supplements(eid)
    assert next(r for r in records if r['id']==old['id'])['state']=='superseded'
    assert next(r for r in records if r['statement']==new['statement'])['state']=='confirmed'
    assert env.art(eid,'script')['adopted_version_id']==candidate
    assert not env.wb.adoption_gate(candidate)['formatErrors']


def test_W_B2_different_scene_is_not_assumed_conflicting(tmp_path):
    env,eid,cid,old,new=conflict_fixture(tmp_path)
    specific=dict(new,subject='场02手机.位置',scenes=['场02'])
    env.codex.script=[lambda req:dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps(dict(conflict=False,keep='both',reason='不同场次可以同时成立'))),
                      lambda req:draft(req,supplements=json.dumps([specific]))]
    candidate=env.art(eid,'script')['versions'][-1]['id']
    env.wb._continuous_adopt(cid,'script',candidate)
    records=env.wb.production_supplements(eid)
    assert next(r for r in records if r['id']==old['id'])['state']=='confirmed'
    assert next(r for r in records if r['subject']==specific['subject'])['state']=='confirmed'
