"""E/F numbered acceptance: isolated services and deterministic fake model replies."""
import json
import pytest
from helpers import Env
from workbench.errors import Rejected
from workbench.executors.fake import draft
from workbench.db import dumps

IDEA='夜班学徒送错了蛋糕。'

def directions(complete=False):
    return dict(completeStory=complete,directions=[dict(key=key,title='夜班'+key,premise='学徒寻找蛋糕的主人'+key,
        story=('学徒提着蛋糕赶到渡口，发现名单上的名字与收件人不符。他沿着票根寻找失主，却把同名的两个人认错了。船即将离岸，他用盒上的店章查到留言，把蛋糕交给守夜的老人。老人把蜡烛点亮，原来这份生日惊喜是学徒自己遗忘的约定。' * 2)[:220]+key,
        hook='错误不断升级',ending='迟到的生日兑现',difference='用误认推动结尾',mechanisms=[],sources=[],recommended=key=='A',reason='未提供机制库；离线结构夹具') for key in ('A',) if complete] if complete else [dict(key=key,title='夜班'+key,premise='寻找蛋糕的主人'+key,
        story=('学徒提着蛋糕赶到渡口，发现名单上的名字与收件人不符。他沿着票根寻找失主，却把同名的两个人认错了。船即将离岸，他用盒上的店章查到留言，把蛋糕交给守夜的老人。老人把蜡烛点亮，原来这份生日惊喜是学徒自己遗忘的约定。'*2)[:220]+key,
        hook='错误不断升级',ending='迟到的生日兑现',difference='与近期故事不同的误认',mechanisms=[],sources=[],recommended=key=='A',reason='未提供机制库；离线结构夹具') for key in ('A','B','C')])

def reply(req,value=None):
    return dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps(value if value is not None else directions(),ensure_ascii=False))

def quick_env(tmp_path):
    env=Env(tmp_path);pid=env.wb.create_project('_template','开拍验收')['projectId']
    env.codex.script=[reply]
    return env,pid

def test_E_T1_F_T1_quick_start_number_and_three_directions(tmp_path):
    env,pid=quick_env(tmp_path)
    env.wb.create_episode(pid,'E09');env.wb.create_episode(pid,'特别篇')
    result=env.wb.quick_start(pid,IDEA,'start')
    assert env.wb.quick_start(pid,IDEA,'start')==result
    view=env.wb.episode_view(result['episodeId']);run=env.wb.continuous_view(result['episodeId'])['run']
    assert view['episode']['display_code']=='E10' and view['kickoff']
    assert run['target_stage']=='delivery' and run['cursor']['directionDone']
    assert directions()['directions'][0]['story'] in run['cursor']['storyGoal']
    assert not any(p['state']=='waiting_choice' for p in run['cursor'].get('pauseHistory',[]))
    # 专家 AI 第 1 步：开工参数包含专家，并补齐 model/effort 字段（默认分工未设置时三项都是 Codex）。
    slot={'provider':'codex','model':None,'effort':None}
    assert view['kickoff']['executor_plan']=={'create':slot,'review':slot,'expert':slot}
    assert view['cooperationMode']=='advanced'


def test_E_T2_missing_default_is_reported(tmp_path):
    env,pid=quick_env(tmp_path)
    with env.db() as db:db.execute("UPDATE project SET spec_preset='{}' WHERE id=?",(pid,));db.commit()
    with pytest.raises(Rejected,match='缺少开工默认项：aspectRatio'):env.wb.quick_start(pid,IDEA,'start')
    assert not env.wb.project_view(pid)['episodes']


def test_E_T3_F_T4_explicit_import_style_spec_and_attachment(tmp_path):
    from workbench.images import png_bytes
    env,pid=quick_env(tmp_path);env.codex.script=[]
    imp=env.wb.import_file(pid,'原文.md','一位学徒送错蛋糕，最终找到生日的主人。'.encode(),'story_source')['importId']
    result=env.wb.quick_start(pid,'按原文拍','start',dict(source_import_id=imp,source_intent='adapt',spec_overrides={'aspectRatio':'16:9','style':'墨绿夜景'},executor_plan={'create':{'provider':'codex'},'review':{'provider':'claude'}}),files=[('夜色.png',png_bytes(16,16))])
    view=env.wb.episode_view(result['episodeId']);art=env.art(result['episodeId'],'story')
    assert art['adopted_version_id']
    manifest=env.wb.run_manifest(art['versions'][0]['source_run_id'])['manifest']
    assert not any(env.wb.run_manifest(r['id'])['manifest'].get('direction') for t in art['tasks'] for r in t['runs'])
    assert view['kickoff']['source_import_id']==imp and view['kickoff']['spec']['aspectRatio']=='16:9' and view['kickoff']['spec']['style']=='墨绿夜景'
    assert any(i['role']=='image' and i['sha256']==__import__('hashlib').sha256(png_bytes(16,16)).hexdigest() for i in manifest['inputs'])
    frozen='\n'.join(env.wb.store.read_blob(i['sha256']).decode(errors='replace') for i in manifest['inputs'])
    assert '墨绿夜景' in frozen and '一位学徒送错蛋糕' in frozen


def test_E_T4_E_T5_conflict_card_before_kickoff_and_adjustment_rechecked(tmp_path):
    env,pid=quick_env(tmp_path);rule='全片无对白';idea='主角说出最后一句台词。';new='主角在纸上写下最后一句话。'
    env.wb.propose_setting_change(pid,'dialogue',rule,'required','默片','rule')
    checks=[]
    def check(req):
        checks.append(req.prompt);conflict=len(checks)==1
        return dict(checks=[dict(key='dialogue',verdict='conflict' if conflict else 'compatible',settingQuote=rule,requestQuote=idea if conflict else new,reason='已核对原文')])
    env.codex.script=[check,lambda req:dict(idea=new),check,reply]
    result=env.wb.quick_start(pid,idea,'start');eid=result['episodeId'];run=env.wb.continuous_view(eid)['run']
    assert len(checks)==2
    assert env.wb.episode_view(eid)['kickoff']['idea_text']==new
    assert run['cursor']['kickoffAdjustment']==dict(before=idea,after=new)
    assert not any(p['state']=='waiting_choice' for p in run['cursor'].get('pauseHistory',[]))


@pytest.mark.parametrize('bad',['count','sources'])
def test_invalid_direction_output_falls_back_to_topic_then_delivers(tmp_path,bad):
    env,pid=quick_env(tmp_path);value=directions()
    if bad=='count':value['directions']=value['directions'][:2]
    else:value['directions'][0]['sources']=['不存在的编号']
    env.codex.script=[lambda req:reply(req,value)]*2
    result=env.wb.quick_start(pid,IDEA,'start');run=env.wb._continuous_record(result['continuousId'])
    assert run['state']=='done' and env.wb.verify_delivery(run['cursor']['deliveryId'])['ok']
    assert IDEA in run['cursor']['storyGoal'] and not run['choice'] and not run['cursor'].get('pauseHistory')
    assert len([r for r in env.codex.calls if r.purpose=='image_assess'])==1


def test_recommended_direction_selected_once_without_user(tmp_path):
    env,pid=quick_env(tmp_path);result=env.wb.quick_start(pid,IDEA,'start');eid=result['episodeId']
    view=env.wb.continuous_view(eid)
    assert view['run']['state']=='done'
    picks=[json.loads(d['detail']) for d in view['decisions'] if d['kind']=='choose_direction']
    assert [d['key'] for d in picks]==['A']
    assert directions()['directions'][0]['story'] in view['run']['cursor']['storyGoal']


def test_F_T4b_complete_story_bypasses_card_preserves_original_input(tmp_path):
    env,pid=quick_env(tmp_path);env.codex.script=[lambda req:reply(req,directions(True))]
    result=env.wb.quick_start(pid,IDEA,'start');eid=result['episodeId'];run=env.wb.continuous_view(eid)['run']
    assert run['choice'] is None and env.art(eid,'story')['adopted_version_id'],run['reason']
    assert '用户原话：\n'+IDEA in env.codex.calls[1].prompt
    assert any(d['kind']=='decide_skip' for d in env.wb.continuous_view(eid)['decisions'])


def test_F_T6_F_T7_topic_similarity_and_knowledge_citations(tmp_path):
    env,pid=quick_env(tmp_path)
    env.wb.add_topic(pid,'T01',IDEA)
    hit=env.wb.import_knowledge(pid,[dict(name='蛋糕参考.md',kind='story',data=IDEA.encode())],'knowledge')[0]
    assert env.wb.similar_topics(pid,IDEA)[0]['code']=='T01'
    result=env.wb.quick_start(pid,IDEA,'start');run=env.wb.continuous_view(result['episodeId'])['run']
    with env.db() as db:citations=[dict(r) for r in db.execute('SELECT * FROM citation')]
    assert len(citations)==1 and citations[0]['knowledge_id']==hit['id'] and citations[0]['reason']=='AI 自动检索：与想法相似'
    manifest=env.wb.run_manifest(run['cursor']['directionRunId'])['manifest']
    assert any(i['role']=='citation' and '蛋糕参考' in i['name'] for i in manifest['inputs'])
    assert any(i['name']=='topic.md' for i in manifest['inputs'])


@pytest.mark.parametrize('existing',['','导演已有标题'])
def test_title_backfill_preserves_director_title(tmp_path,existing):
    env,pid=quick_env(tmp_path);result=env.wb.quick_start_prepare(pid,IDEA,'start');eid=result['episodeId']
    with env.db() as db:db.execute('UPDATE episode SET title=? WHERE id=?',(existing,eid));db.commit()
    env.wb._advance_continuous(result['continuousId'])
    assert env.wb.episode_view(eid)['episode']['title']==(existing or '第一次值夜班')


def test_E_T1_F_T3_decision_migration_preserves_existing_receipts(tmp_path):
    import sqlite3
    from workbench.db import SCHEMA, initialize, connect
    path=tmp_path/'legacy.sqlite'
    # 旧库：既没有九阶段新增的决定类型，也没有专家 AI 第 1 步新增的类型。
    old=SCHEMA.replace(",'confirm_supplement','choose_image','regenerate_image','confirm_lock','fill_observation','waive_segment','decide_skip','choose_direction','package','expert_judgment','provisional_use','accept_issue'",'')
    assert "'expert_judgment'" not in old.split('CREATE TABLE IF NOT EXISTS ai_decision(')[1].split(';')[0]
    with sqlite3.connect(path) as db:
        db.executescript(old)
        db.execute("INSERT INTO project VALUES('p','项目','','_template','short-film@v7',0,'{}','{}',1,0)")
        db.execute("INSERT INTO episode(id,project_id,display_code,created_at) VALUES('e','p','E01',0)")
        db.execute("INSERT INTO continuous_run(id,episode_id,target_stage,state,created_at,updated_at) VALUES('c','e','stress','done',0,0)")
        db.execute("INSERT INTO ai_decision VALUES('old','c','e','story','adopt','v','保留旧记录','{}',NULL,0)")
    initialize(path);initialize(path)
    with connect(path) as db:
        assert db.execute("SELECT reason FROM ai_decision WHERE id='old'").fetchone()[0]=='保留旧记录'
        # 专家 AI 第 1 步起，ai_decision 的存档表改名为 ai_decision_before_progress_expert；旧记录仍须保留。
        assert db.execute('SELECT id FROM ai_decision_before_progress_expert').fetchone()[0]=='old'
        db.execute("INSERT INTO ai_decision VALUES('new','c','e','story','choose_direction','B','新方向','{}',NULL,1)")
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert len(list((tmp_path/'backups').glob('*.sqlite')))==1


def test_field_111_failed_kickoff_check_reruns_after_resume(tmp_path,monkeypatch):
    """实测 111：执行器登录失效导致开工核对失败，重新登录后点继续应重跑核对，并显示可读原因。"""
    from workbench.executors.base import ExecOutcome
    env,pid=quick_env(tmp_path)
    env.wb.propose_setting_change(pid,'dialogue','全片无对白','required','默片','rule')
    def auth(req):
        return ExecOutcome(ok=False,error_class='authentication',diagnostic='{"result": "Failed to authenticate: OAuth token revoked.", "api_error_status": 401}')
    def compatible(req):
        return dict(checks=[dict(key='dialogue',verdict='compatible',settingQuote='全片无对白',requestQuote=IDEA,reason='可同时满足')])
    env.codex.script=[auth,compatible,reply]
    result=env.wb.quick_start(pid,IDEA,'start');cid=result['continuousId']
    run=env.wb.continuous_view(result['episodeId'])['run']
    assert run['state']=='waiting_external' and '登录已失效' in run['reason'] and 'OAuth' not in run['reason']
    assert not env.wb.episode_view(result['episodeId'])['kickoff']
    monkeypatch.setattr(env.wb,'executor_identity',lambda provider:dict(authenticated=True))
    assert cid in env.wb.changed_continuous_runs()
    env.wb._advance_continuous(cid)
    run=env.wb.continuous_view(result['episodeId'])['run']
    assert env.wb.episode_view(result['episodeId'])['kickoff']
    assert run['choice'] is None and run['cursor']['directionDone']


def test_field_112_direction_may_cite_code_written_in_idea(tmp_path):
    """实测 112：导演粘贴的选题卡里写有“选题编号”，方向引用该编号不应被判为编造来源。"""
    from workbench.quickstart import validate_directions
    value=directions(complete=True);value['directions'][0]['sources']=['R20261028-Q25 · v2']
    idea='MILO把洗衣房变成鼓乐队。\n选题编号：R20261028-Q25 · v2\n'
    assert validate_directions(value,set(),idea)[0]['sources']==['R20261028-Q25 · v2']
    with pytest.raises(Rejected):validate_directions(value,set(),'没有编号的想法')
    env,pid=quick_env(tmp_path)
    env.codex.script=[lambda req:reply(req,value)]
    result=env.wb.quick_start(pid,idea,'start')
    run=env.wb.continuous_view(result['episodeId'])['run']
    # 方向通过后连续制作继续往下走；后续阶段不在本用例范围。
    assert '方向' not in (run['reason'] or ''), run['reason']
    assert env.wb.episode_view(result['episodeId'])['artifacts'][0]['versions']
