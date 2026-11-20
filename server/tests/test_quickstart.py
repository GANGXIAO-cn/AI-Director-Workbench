"""E/F numbered acceptance: isolated services and deterministic fake model replies."""
import json
import pytest
from helpers import Env
from workbench.errors import Rejected
from workbench.executors.fake import draft
from workbench.db import dumps

IDEA='夜班学徒送错了蛋糕。'

def directions():
    return dict(analysis='学徒最怕错过末班船，倒计时是看点。', directions=[
        dict(key='A',title='追船',story='送错蛋糕的学徒骑车追船，借蜡烛认出真正的寿星。'),
        dict(key='B',title='误认',story='两位同名乘客都来取货，学徒翻看票根认出收件人。')],
        recommendation='A',reason='渡轮离岸能让观众看见截止时间。')

def reply(req,value=None):
    return dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=json.dumps(value if value is not None else directions(),ensure_ascii=False))

def quick_env(tmp_path):
    env=Env(tmp_path);pid=env.wb.create_project('_template','开拍验收')['projectId']
    env.codex.script=[reply]
    return env,pid

def test_E_T1_F_T1_quick_start_number_and_writer_directions(tmp_path):
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


@pytest.mark.parametrize("from_topic", [False, True])
def test_E_T4_E_T5_conflict_card_before_kickoff_and_adjustment_rechecked(tmp_path, from_topic):
    env,pid=quick_env(tmp_path);rule='全片无对白';idea='主角说出最后一句台词。';new='主角在纸上写下最后一句话。'
    env.wb.propose_setting_change(pid,'dialogue',rule,'required','默片','rule')
    checks=[]
    def check(req):
        checks.append(req.prompt);conflict=len(checks)==1
        return dict(checks=[dict(key='dialogue',verdict='conflict' if conflict else 'compatible',settingQuote=rule,requestQuote=idea if conflict else new,reason='已核对原文')])
    env.codex.script=[check,lambda req:dict(idea=new),check,reply]
    topic_id = env.wb.add_topic(pid, 'T01', idea)['topicId'] if from_topic else None
    result=env.wb.quick_start(pid,'' if from_topic else idea,'start',dict(topicId=topic_id))
    eid=result['episodeId']
    run=env.wb.continuous_view(eid)['run']
    assert len(checks)==2
    assert env.wb.episode_view(eid)['kickoff']['idea_text']==new
    assert idea in run['cursor']['kickoffAdjustment']['before']
    assert run['cursor']['kickoffAdjustment']['after']==new
    if from_topic:
        card, = env.wb.topics(pid)
        assert card['status'] != '候选' and card['episode_id'] == eid
        assert env.wb.episode_view(eid)['kickoff']['topic_id'] == topic_id
        assert env.wb._direction_inputs(eid)['idea'] == new
    assert not any(p['state']=='waiting_choice' for p in run['cursor'].get('pauseHistory',[]))


def test_expert_settles_unstructured_proposal_without_user(tmp_path):
    env,pid=quick_env(tmp_path)
    def prose(req):
        return dict(reply(req),reply="写稿人推荐追船，也可以从认错人展开。")
    env.codex.script=[prose]
    result=env.wb.quick_start(pid,IDEA,'start')
    run=env.wb._continuous_record(result['continuousId'])
    assert run['state']=='done' and env.wb.verify_delivery(run['cursor']['deliveryId'])['ok']
    assert '认错人' in run['cursor']['writerProposal'] and '选 B' in run['cursor']['storyGoal']
    assert not run['choice']


def test_expert_can_overrule_writer_without_user(tmp_path):
    env,pid=quick_env(tmp_path)
    env.ag.script=[reply]
    env.codex.script=[]
    plan=dict(create=dict(provider='antigravity'),expert=dict(provider='codex'))
    result=env.wb.quick_start_prepare(pid,IDEA,'start',dict(executor_plan=plan))
    cid=result['continuousId']
    env.wb._continuous_kickoff(cid)
    env.wb._continuous_direction(cid)
    view=env.wb.continuous_view(result['episodeId'])
    pick,=[json.loads(d['detail']) for d in view['decisions'] if d['kind']=='choose_direction']
    assert '写稿人推荐 A' in pick['proposal'] and '选 B' in pick['decision']
    env.wb._continuous_create(cid,'story')
    assert pick['decision'] in env.ag.calls[-1].prompt
    assert len(env.art(result['episodeId'],'story')['versions'])==1
    assert not view['run']['choice']


def test_fully_described_idea_still_gets_writer_and_expert(tmp_path):
    env,pid=quick_env(tmp_path)
    idea='学徒发现蛋糕送错了，追上末班船，在离岸前找到了真正的寿星。'
    result=env.wb.quick_start_prepare(pid,idea,'start')
    cid=result['continuousId']
    env.wb._continuous_kickoff(cid)
    env.wb._continuous_direction(cid)
    cur=env.wb._continuous_record(cid)['cursor']
    assert cur['directionRunId'] != cur['directionDecisionRunId']
    assert idea in cur['storyGoal'] and '选 B' in cur['storyGoal']


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
    idea='MILO把洗衣房变成鼓乐队。\n选题编号：R20261028-Q25 · v2\n'
    env,pid=quick_env(tmp_path)
    result=env.wb.quick_start(pid,idea,'start')
    run=env.wb.continuous_view(result['episodeId'])['run']
    # 方向通过后连续制作继续往下走；后续阶段不在本用例范围。
    assert '方向' not in (run['reason'] or ''), run['reason']
    assert env.wb.episode_view(result['episodeId'])['artifacts'][0]['versions']
