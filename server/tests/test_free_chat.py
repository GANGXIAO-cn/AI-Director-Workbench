from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest

from helpers import Env
from workbench.errors import Conflict, NotFound, Rejected
from workbench.executors.fake import quota_exhausted
from workbench.service import Workbench


def test_free_workflow_without_episode_and_project_copy(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid = wb.create_project("_template")["projectId"]
    other = wb.create_project("_template")["projectId"]
    body = b"\xef\xbb\xbf# method\r\nstop at the action"
    v = wb.import_custom_method(pid,"SKILL.md",body,"动作停顿")
    args = dict(project_id=pid,text="帮我改一句台词",provider="codex",methods=[v["versionId"]],command_id="first")
    first = wb.send_free_chat(**args)
    assert first == wb.send_free_chat(**args)
    tid, turn = first["threadId"],first["turnId"]
    with pytest.raises(Conflict):
        wb.send_free_chat(pid,"提前续问","codex",tid,turn)
    with pytest.raises(NotFound):
        wb.free_chat(other,tid)
    with pytest.raises(NotFound):
        wb.send_free_chat(other,"另一集","codex",methods=[v["versionId"]])
    wb.execute_free_chat(turn,first["attemptId"])
    data = wb.free_chat(pid,tid)["turns"][0]
    assert data["sources"][0]["sha256"]==sha256(body).hexdigest() and data["reply"]
    assert body.decode() in env.codex.calls[0].prompt
    _, eid = env.new_episode(project_id=pid)
    asset = env.image(pid,eid)["versionId"]
    wb.save_canvas(eid,dict(nodes=[dict(id="a",x=10,y=20,type="text",title="提示",data={"body":"雨打在地上"})],edges=[]),0)
    with pytest.raises(Conflict):
        wb.send_free_chat(pid,"看画布","codex",tid,turn,canvas=eid,canvas_revision=0)
    with pytest.raises(Rejected):
        wb.send_free_chat(pid,"看图","antigravity",tid,turn,images=[asset])
    follow = wb.send_free_chat(pid,"依画布和原图改第二句","codex",tid,turn,images=[asset],canvas=eid,canvas_revision=1)
    env.codex.script.append(quota_exhausted)
    wb.execute_free_chat(follow["turnId"],follow["attemptId"])
    assert wb.free_chat(pid,tid)["turns"][-1]["status"]=="waiting_quota"
    requested = env.codex.calls[-1]
    wb.save_canvas(eid,dict(nodes=[],edges=[]),1)
    assert len(requested.images)==1 and Path(requested.images[0]).read_bytes()==wb.store.read_blob(wb.asset_version(asset)["sha256"])
    assert "雨打在地上" in requested.prompt and data["reply"] in requested.prompt
    retry = wb.retry_free_chat(pid,follow["turnId"],follow["attemptId"],"retry")
    assert retry == wb.retry_free_chat(pid,follow["turnId"],follow["attemptId"],"retry")
    wb.execute_free_chat(retry["turnId"],retry["attemptId"])
    assert requested.prompt == env.codex.calls[-1].prompt
    with pytest.raises(Conflict):
        wb.add_reply_to_canvas(pid,follow["turnId"],eid,1)
    applied = wb.add_reply_to_canvas(pid,follow["turnId"],eid,2)
    assert wb.add_reply_to_canvas(pid,follow["turnId"],eid,2)==applied
    end = wb.free_chat(pid,tid)
    assert wb.canvas_view(eid)["doc"]["nodes"][0]["data"]["body"]==end["turns"][1]["reply"]
    folder = tmp_path/"export"
    wb.export_project_folder(pid,str(folder))
    copy = Workbench(folder, {})
    assert copy.free_chat(pid,tid) == end
    assert copy.free_chat_options(pid)["assets"][0]["id"]==asset
    with env.db() as db:
        assert db.execute("SELECT count(*) FROM version").fetchone()[0]==0


def test_free_chat_racing_sends_stop_retry_and_restart(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid = wb.create_project("_template")["projectId"]
    first = wb.send_free_chat(pid,"一句","codex")
    tid, turn = first["threadId"],first["turnId"]
    wb.execute_free_chat(turn,first["attemptId"])
    def send(n):
        try:
            return wb.send_free_chat(pid,"上一句的不同结尾"+str(n),"codex",tid,turn)
        except Conflict:
            return None
    with ThreadPoolExecutor(2) as pool:
        sent = [r for r in pool.map(send,range(2)) if r]
    assert len(sent)==1
    late = sent[0]
    started,release = Event(), Event()
    def delay(req):
        started.set()
        assert release.wait(5)
        return {"reply":"已停掉的结果"}
    env.codex.script.append(delay)
    with ThreadPoolExecutor(1) as pool:
        wait = pool.submit(wb.execute_free_chat,late["turnId"],late["attemptId"])
        assert started.wait(5)
        wb.stop_free_chat(pid,late["turnId"],late["attemptId"])
        retry = wb.retry_free_chat(pid,late["turnId"],late["attemptId"])
        wb.execute_free_chat(retry["turnId"],retry["attemptId"])
        release.set()
        wait.result()
    assert "已停掉的结果" != wb.free_chat(pid,tid)["turns"][-1]["reply"]
    fresh = wb.send_free_chat(pid,"重新打开","codex",tid,retry["turnId"])
    restarted = Workbench(wb.store.root,wb.executors)
    assert restarted.free_chat(pid,tid)["turns"][-1]["status"]=="failed"
    again = restarted.retry_free_chat(pid,fresh["turnId"],fresh["attemptId"])
    restarted.execute_free_chat(again["turnId"],again["attemptId"])
    assert restarted.free_chat(pid,tid)["turns"][-1]["status"]=="succeeded"
