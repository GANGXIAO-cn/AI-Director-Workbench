import json
from threading import Event

import httpx
import pytest

from workbench import cloudtask
from workbench.h3online import RunningHub


def test_retry_same_task_on_query_and_download_timeout_across_restart(tmp_path):
    calls=[]
    responses=[504,504,804,0,503,0,200]
    now=[1000.]
    class Clock:
        def __init__(self): self.interrupt=True
        def wait(self,seconds):
            if seconds>0 and self.interrupt:return True
            now[0]+=seconds
            return False
    clock=Clock()
    def send(req):
        v=responses.pop(0)
        if req.method=='POST':
            assert req.url.path=='/task/openapi/outputs'
            calls.append(json.loads(req.content)['taskId'])
            if v in (0,804):
                return httpx.Response(200,json=dict(code=v,data=[dict(nodeId='214',fileType='mp4',fileUrl='https://media.invalid/video')]))
        return httpx.Response(v,content=b'complete' if v==200 else b'')
    from unittest.mock import patch
    with httpx.Client(transport=httpx.MockTransport(send)) as client, patch.object(cloudtask,'time') as timer:
        timer.time.side_effect=lambda:now[0]
        hub=RunningHub(client,'https://www.runninghub.ai','sample')
        path=tmp_path/'partial'
        with pytest.raises(cloudtask.PollInterrupted):
            cloudtask.fetch_output(hub,'42',path,'214',submitted_at=900,stop=clock)
        r=cloudtask.timing(tmp_path)
        assert r['startedAt']==900 and r['nextCheck']==1024 and '504' in r['retryMessage']
        clock.interrupt=False
        assert cloudtask.fetch_output(hub,'42',path,'214',file_type='mp4',stop=clock)['code']==0
        r=cloudtask.timing(tmp_path)
        assert r['startedAt']==900 and r['endedAt']==1084 and now[0]==1108
        assert r['retryMessage']=='' and path.read_bytes()==b'complete' and calls==['42']*5


@pytest.mark.parametrize('status',[401,403])
def test_auth_failure_does_not_loop(tmp_path,status):
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(status))) as client:
        hub=RunningHub(client,'https://www.runninghub.ai','sample')
        with pytest.raises(httpx.HTTPStatusError):
            cloudtask.fetch_output(hub,'42',tmp_path/'partial','49',stop=Event())


def test_ambiguous_submission_sent_once(tmp_path,monkeypatch):
    from workbench import videosegment
    count=[]
    def create(*args):
        count.append(True)
        raise httpx.ReadTimeout('gateway')
    monkeypatch.setattr(RunningHub,'graph',lambda *args:{})
    monkeypatch.setattr(videosegment,'overrides',lambda *args,**kwargs:[])
    monkeypatch.setattr(RunningHub,'submit',create)
    args=(tmp_path,'d','S',tmp_path,dict(key='sample',site='https://www.runninghub.ai'))
    kwargs=dict(workflow='1',prepared=(dict(params=dict(seed=1,instanceType='plus')),{},{}))
    with pytest.raises(httpx.ReadTimeout):videosegment.generate(*args,**kwargs)
    with pytest.raises(ValueError,match='此前已提交'):videosegment.generate(*args,**kwargs)
    assert len(count)==1 and json.loads((tmp_path/'trial.json').read_text())['status']=='submitting'


def test_restart_after_h3_acceptance_resumes_original_task(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess
    from workbench import videosegment
    from workbench.production_steps import observe
    submissions=[]
    queries=[]
    steps=[]
    monkeypatch.setattr(RunningHub,"graph",lambda *_:{})
    monkeypatch.setattr(videosegment,"overrides",lambda *_args,**_kw:[])
    def submit(*_):
        submissions.append(True)
        return {"code":0,"data":{"taskId":"accepted-job"}}
    monkeypatch.setattr(RunningHub,"submit",submit)
    def fetch(hub,task,pending,*args,**kwargs):
        queries.append(task)
        if len(queries)==1:
            raise cloudtask.PollInterrupted()
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=s=32x32:d=0.2",
                        "-f","mp4",str(pending)],check=True)
        return {"code":0}
    monkeypatch.setattr(videosegment,"fetch_output",fetch)
    args=(tmp_path,"d","S",tmp_path,dict(key="sample",site="https://www.runninghub.ai"))
    kwargs=dict(workflow="1",prepared=(dict(params=dict(seed=1,instanceType="plus")),{},{}))
    with observe(steps.append):
        with pytest.raises(cloudtask.PollInterrupted):
            videosegment.generate(*args,**kwargs)
        assert json.loads((tmp_path/"trial.json").read_bytes())["task"]=="accepted-job"
        videosegment.generate(*args,**kwargs)
    assert len(submissions)==1 and queries==["accepted-job"]*2
    assert "正在向 H3 提交，本轮接单待确认" in steps
    assert json.loads((tmp_path/"trial.json").read_bytes())["status"]=="downloaded"
