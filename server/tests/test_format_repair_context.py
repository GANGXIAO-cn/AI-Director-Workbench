"""Format repair has the previous reply, even with a fresh executor session."""
import json
from pathlib import Path
import pytest
from test_document_reply import setup_plan, create_plan, run_row
from workbench.executors.base import ExecOutcome, ExecRequest
from workbench.executors.fake import draft
from workbench.executors import cli


@pytest.mark.parametrize('mode',['schema','document','structured'])
def test_repair_includes_complete_previous_reply(tmp_path,mode):
    env,eid=setup_plan(tmp_path);wb=env.wb
    content=dict(env.plan86(),notes='原稿结尾要保留')
    original={}
    provider=env.codex if mode=='structured' else env.ag
    def first(req):
        value=draft(req,**{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in content.items()}) if mode=='structured' else dict(kind='draft',fields=content)
        # A known invalid field is the only actual error; preserve every shot and segment.
        if mode=='structured':value['changes'].append(dict(targetId=value['changes'][0]['targetId'],field='notWritable',value='bad'))
        else:value['fields']=dict(content,notWritable='bad')
        original['reply']=json.dumps(value,ensure_ascii=False)
        if mode=='schema':
            original['reply']=original['reply'][:-1]
            (req.workdir.parent/'raw-response-1.txt').write_text(original['reply'])
            return ExecOutcome(False,error_class='schema',diagnostic='Expecting delimiter')
        return value
    def second(req):
        assert req.attempt==2
        payload=json.loads(next(line for line in req.prompt.splitlines() if line.startswith('{"previousResponse":')))
        assert payload['previousResponse']==original['reply']
        assert payload['errors']
        assert '完整修正版' in req.prompt
        if mode=='structured':return draft(req,**{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in content.items()})
        return dict(kind='draft',fields=content)
    provider.script=[first,second]
    task=wb.create_task(eid,'plan','完整镜头稿',executor={'create':{'provider':'codex'}}) if mode=='structured' else create_plan(env,eid)
    wb.execute_run(task['runId'])
    assert run_row(env,task['runId'])['status']=='succeeded'
    directory=wb.store.run_dir(task['runId'])
    assert (directory/'raw-response-1.txt').read_text()==original['reply']
    assert (directory/'raw-response-2.txt').exists()
    assert json.loads((directory/'validation-attempt-1.json').read_text())['problems']
    assert not json.loads((directory/'validation-attempt-2.json').read_text())['problems']


@pytest.mark.parametrize('second',['invalid','network','exception'])
def test_both_failures_remain_visible_without_third_call(tmp_path,second):
    env,eid=setup_plan(tmp_path)
    def fail(req):
        if second=='exception':raise RuntimeError('second transport failed')
        if second=='network':return ExecOutcome(False,error_class='network',diagnostic='second network failed')
        return dict(kind='draft',fields={'notes':'second incomplete reply'})
    env.ag.script=[lambda req:dict(kind='draft',fields={'unknown':'first invalid field'}),fail]
    rid=create_plan(env,eid)['runId'];env.wb.execute_run(rid)
    row=run_row(env,rid)
    assert row['status']=='failed' and row['format_attempts']==1
    assert '第 1 次' in row['diagnostic'] and 'unknown' in row['diagnostic']
    assert '第 2 次' in row['diagnostic']
    if second=='network':assert row['reason']=='network'
    directory=env.wb.store.run_dir(rid)
    assert json.loads((directory/'validation-attempt-1.json').read_text())['problems']
    assert json.loads((directory/'validation-attempt-2.json').read_text())['problems']
    assert len(env.ag.calls)==2


def test_claude_saves_each_reply_without_transport_envelope(tmp_path,monkeypatch):
    adapter=cli.ClaudeExecutor();monkeypatch.setattr(adapter,'binary',lambda:Path('/test/claude'))
    monkeypatch.setattr(cli,'invoke',lambda *a,**kw:(0,json.dumps(dict(is_error=False,subtype='success',structured_output={'fields':{'notes':'original'}})),''))
    req=ExecRequest('r','create','draft','prompt',{},'new',tmp_path/'work')
    req.workdir.mkdir()
    assert adapter.start(req).ok
    assert json.loads((tmp_path/'raw-response-1.txt').read_text())=={'fields':{'notes':'original'}}


def test_saved_reproduction_reply_is_not_truncated():
    from workbench.service import Workbench
    path=Path(__file__).resolve().parents[2]/'output/validation/plan-codex-20261113-2/raw-response-1.txt'
    if not path.exists():pytest.skip('local reproduction is optional')
    reply=path.read_text()
    prompt=Workbench._format_repair_prompt(None,'original frozen input',reply,['seg03：时间句缺少 4–8 秒'])
    assert json.loads(prompt.split('\n')[-1])['previousResponse']==reply
