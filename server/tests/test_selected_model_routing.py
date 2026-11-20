"""Selected roles survive schema retries, image input and recovery from an old failed rebuild."""
import json

import httpx

from helpers import Env
from test_continuous_wake import run_record
from test_image_assess import fixture, assessment, reply
from workbench.db import transaction
from workbench.executors.fake import draft, STORY


def test_production_page_and_worker_use_same_saved_roles(tmp_path):
    from workbench.videosegment import episode_expert

    env = Env(tmp_path)
    wb = env.wb
    case = env.full_episode(segments={"G01": [6000]})
    eid = case["eid"]
    delivery = case["delivery"]["deliveryId"]
    wb.save_stage_executor(env.art(eid, "prompts")["id"], "create", "codex", "original-writer")
    assert wb.film_executors(eid, "package")["create"]["model"] == "original-writer"
    wb.save_episode_expert(eid, "codex", "episode-expert")
    for stage in ("package", "finecut"):
        for role in ("create", "review"):
            wb.save_film_executor(eid, stage, role, "antigravity", stage + role)
    for stage in ("package", "finecut"):
        shown = wb.film_executors(eid, stage)
        for role, model in (("create", stage + "create"), ("review", stage + "review"), ("expert", "episode-expert")):
            executor, selection, _, _ = episode_expert(wb.store.root, delivery, role, stage)
            assert executor is wb.executors[shown[role]["provider"]]
            assert selection["model"] == shown[role]["model"] == model
    with env.db() as db:
        assert wb._stage_executors(db, eid, "prompts")["create"]["model"] == "original-writer"


def test_legacy_recovery_submits_complete_draft_to_selected_reviewer(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode(plan=dict(create=dict(provider='codex'), review=dict(provider='antigravity',model='review-model')))
    cid = run_record(env, eid, stage='story')
    owned = []
    versions = []
    for fields in [dict(title='A',synopsis='A'),dict(title='B',synopsis='B'),STORY,dict(synopsis='尚未起标题的恢复草稿')]:
        env.codex.script = [lambda req, fields=fields: draft(req, **fields)]
        job = wb.create_task(eid, 'story', '写稿', rebuild=True)
        owned.append(job['runId'])
        wb._continuous_cursor(cid,ownedRuns=owned)
        wb.execute_run(job['runId'])
        versions.append(env.art(eid,'story')['versions'][-1]['id'])
    wb._continuous_cursor(cid,ownedRuns=owned,runId=owned[-1],repairHistory={'story:story':[dict(method='rebuild',reason='会话隔离未验证')]})
    wb._continuous_update(cid,state='failed')
    with transaction(wb.db_path) as db:
        wb._claim_resume(db,cid)
    wb._continuous_stage(cid,'story')
    art=env.art(eid,'story')
    assert art['adopted_version_id']==versions[2] and len(art['versions'])==4
    assert env.ag.calls[-1].model=='review-model' and env.ag.calls[-1].session_mode=='ephemeral'
    assert len(env.codex.calls)==5
    assert '退回写稿人' in env.codex.calls[-1].prompt


def test_image_assessment_uses_episode_expert_and_actual_image(tmp_path):
    env,eid,vid=fixture(tmp_path)
    env.ag.reads_images=True
    env.wb.save_episode_expert(eid,'antigravity','vision-expert')
    env.ag.script=[reply([assessment(vid)])]
    result=env.wb.assess_images(eid,[vid],'chosen-expert',need_key='bell')
    assert result[0]['versionId']==vid
    assert env.ag.calls[-1].model=='vision-expert' and env.ag.calls[-1].images


def test_configured_api_draft_through_normal_dispatch(tmp_path,monkeypatch):
    wb=Env(tmp_path).wb
    pid=wb.save_provider('测试服务','http://localhost:8022/v1','local')['id']
    provider='api:'+pid
    sent=[]
    real_client=httpx.Client

    def handle(request):
        sent.append(json.loads(request.content))
        assert request.url.path=='/v1/chat/completions'
        body=json.dumps({'changes':[{'field':key,'value':value} for key,value in STORY.items()]},ensure_ascii=False)
        return httpx.Response(200,json={'model':'reported-test-model','choices':[{'message':{'content':body}}]})

    monkeypatch.setattr(httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw))
    project=wb.create_project('_template','测试')['projectId']
    eid=wb.create_episode(project,'E01')['episodeId']
    slot={'provider':provider,'model':'my/chosen-model','effort':'high'}
    wb.start_episode(eid,'idea',{'create':slot,'review':slot,'expert':slot},idea_text='雨伞')
    job=wb.create_task(eid,'story','写故事')
    wb.execute_run(job['runId'])
    with transaction(wb.db_path) as db:
        run=db.execute('select status,provider,requested_model,reported_model from run where id=?',(job['runId'],)).fetchone()
    assert tuple(run)==('succeeded',provider,'my/chosen-model','reported-test-model')
    assert len(sent)==1 and sent[0]['model']==slot['model'] and sent[0]['reasoning_effort']=='high'


def test_saved_keys_survive_a_fresh_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    from workbench.videoqueue import VideoQueue

    env = Env(tmp_path)
    wb = env.wb
    queue = VideoQueue(wb.store.root)
    queue.account("studio", "https://www.runninghub.cn", "plus", 1, "running-test-key")
    ident = wb.save_provider("云模型", "https://api.example/v1", "bearer")["id"]
    wb.set_provider_key(ident, 1, "model-test-key")
    assert wb.credentials.path.stat().st_mode & 0o777 == 0o600
    verify = """
import sys
from pathlib import Path
from workbench.service import Workbench
from workbench.videoqueue import VideoQueue
from workbench.executors.chat_api import ChatAPIExecutor
data = Path(sys.argv[1])
wb = Workbench(data, {})
api = wb.api_providers()[0]
assert api['keyConfigured']
executor = wb.executors['api:' + api['id']]
assert executor.key(executor.config()) == 'model-test-key'
queue = VideoQueue(data)
assert queue.accounts()[0]['connected']
with queue.db() as db:
    assert queue.key(db.execute('select * from account').fetchone()) == 'running-test-key'
"""
    subprocess.run([sys.executable, "-c", verify, str(wb.store.root)], check=True,
                   env={**os.environ, "PYTHONPATH":str(Path(__file__).resolve().parents[1])})
