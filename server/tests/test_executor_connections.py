import pytest

from helpers import Env
from workbench.errors import Conflict
from workbench.executors.fake import quota_exhausted


def test_queued_and_retried_request_retain_cli_and_options(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    wb.save_executor_connection("codex", 0, model="gpt-6.1-sol", effort="medium", proxy="off")
    task = wb.create_task(eid, "story", "完善开场", executor={"create":{"provider":"codex","connectionRevision":1}})
    wb.save_executor_connection("codex", 1, model="gpt-6.1-sol", effort="low")
    env.codex.script = [quota_exhausted]
    env.drain(task["runId"])
    original, = env.codex.calls
    assert original.connection == dict(model="gpt-6.1-sol",effort="medium",proxy="off")
    assert original.effort == "medium" and original.connection["proxy"] == "off"
    assert not env.claude.calls
    retry = wb.retry_run(task["runId"], command_id="retry-original")
    env.drain(retry["runId"])
    assert env.codex.calls[-1].connection == original.connection
    with pytest.raises(Conflict):
        wb.post_message(task["taskId"], "加一句对白", "outdated", create_executor={"provider":"codex","connectionRevision":1})
    follow = wb.post_message(task["taskId"], "加一句对白", "follow", create_executor={"provider":"codex","connectionRevision":2})
    env.drain(follow["runId"])
    assert env.codex.calls[-1].effort == "low"


def test_cli_status_and_launch_share_native_login(tmp_path,monkeypatch):
    import json
    from pathlib import Path
    from workbench.executors import cli
    from workbench.executors.base import ExecRequest
    profiles = tmp_path/"native"
    profiles.mkdir()
    login = profiles/"auth.json"
    login.write_text('{"fixture":true}')
    monkeypatch.setenv("CODEX_HOME",str(profiles))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR",str(profiles))
    seen = []
    def respond(argv,cwd,stdin,timeout,run_id=None,*,extra_env=None):
        seen.append(argv[1:])
        if "status" in argv:
            if "auth" in argv:
                assert extra_env["CLAUDE_CONFIG_DIR"] == str(profiles)
                return 0,json.dumps(dict(loggedIn=True,email="test@example.invalid",token="never-return")),""
            assert extra_env["CODEX_HOME"] == str(profiles)
            return 0,"Logged in using ChatGPT",""
        assert extra_env["HTTPS_PROXY"] == "http://localhost:7890"
        if "exec" in argv:
            assert (Path(extra_env["CODEX_HOME"])/"auth.json").resolve() == login
        elif "--restricted" in argv:
            assert extra_env["CLAUDE_CONFIG_DIR"] == str(profiles)
        else:
            assert "HOME" not in extra_env
        return 1,"","fixture finished"
    monkeypatch.setattr(cli,"invoke",respond)
    for adapter in (cli.CodexExecutor(),cli.ClaudeExecutor(),cli.AntigravityExecutor()):
        monkeypatch.setattr(adapter,"binary",lambda:Path("/test/executable"))
        info = adapter.native_identity(adapter.binary(),cli.launch_env(adapter.name,{}))
        if adapter.name == "claude":
            assert info == dict(authenticated=True,accountLabel="test@example.invalid")
        if adapter.name == "codex":
            assert info == dict(authenticated=True,accountLabel="ChatGPT")
        req = ExecRequest("fixture","create","draft","test",{},"new",tmp_path,connection={"proxy":"http://localhost:7890"})
        req.workdir=tmp_path/"work"
        req.workdir.mkdir(exist_ok=True)
        adapter.start(req)
    assert all("logout" not in args and ("login" not in args or args==["login","status"]) for args in seen)


def test_codex_reconnect_notice_does_not_discard_completed_turn(tmp_path,monkeypatch):
    """实测 135：Codex 断线后自行重连并完成，结果有效；重连用尽才算失败。"""
    import json
    from pathlib import Path
    from workbench.executors import cli
    from workbench.executors.base import ExecRequest
    notice=json.dumps({"type":"error","message":"Reconnecting... 2/5 (stream disconnected before completion)"})
    def lines(*events):return "\n".join([json.dumps({"type":"thread.started","thread_id":"t"}),notice,*events])
    done=lines(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps({"ok":1})}}),json.dumps({"type":"turn.completed"}))
    failed=lines(json.dumps({"type":"turn.failed","error":{"message":"stream disconnected"}}))
    adapter=cli.CodexExecutor();monkeypatch.setattr(adapter,"binary",lambda:Path("/test/executable"))
    for out,ok in ((done,True),(failed,False)):
        monkeypatch.setattr(cli,"invoke",lambda *a,**k:(0,out,"failed to refresh available models: request timed out"))
        work=tmp_path/str(ok)/"work";work.mkdir(parents=True)
        result=adapter.start(ExecRequest("fixture","create","draft","test",{},"ephemeral",work))
        assert result.ok is ok
        if ok:assert result.structured=={"ok":1}


def test_antigravity_text_reply_is_schema_error_and_json_text_is_accepted(tmp_path,monkeypatch):
    """实测 143：Antigravity 成功但只回文字时归为格式错误；文字本身是 JSON 对象则直接采用。"""
    import json
    from pathlib import Path
    from workbench.executors import cli
    from workbench.executors.base import ExecRequest
    adapter=cli.AntigravityExecutor();monkeypatch.setattr(adapter,"binary",lambda:Path("/test/executable"))
    for response,ok in (("这是普通文字回复",False),(json.dumps({"reply":"好"}),True)):
        out=json.dumps({"conversation_id":"c","status":"SUCCESS","response":response})
        monkeypatch.setattr(cli,"invoke",lambda *a,**k:(0,out,""))
        work=tmp_path/str(ok)/"work";work.mkdir(parents=True)
        result=adapter.start(ExecRequest("fixture","create","draft","test",{},"ephemeral",work))
        assert result.ok is ok
        if ok:assert result.structured=={"reply":"好"}
        else:assert result.error_class=="schema" and "这是普通文字回复" not in result.diagnostic


@pytest.mark.parametrize('response,ok,diagnostic', [
    ('```json\n{"kind":"draft","fields":{}}\n```', True, ''),
    ('```\n{"kind":"draft","fields":{}}\n```', True, ''),
    ('以下是稿件：\n\n```json\n{"kind":"draft","fields":{}}\n```\n以上为稿件。', True, ''),
    ('前言\n```json\n{"fields":{}}\n```\n```json\n{"fields":{}}\n```', False, '不是可解析的 JSON'),
    ('[]', False, '回复须为 JSON 对象'),
    ('{"fields":{"notes":"a "b""}}', False, '第 1 行第'),
])
def test_antigravity_document_response(tmp_path, monkeypatch, response, ok, diagnostic):
    import json
    from pathlib import Path
    from workbench.executors import cli
    from workbench.executors.base import ExecRequest
    adapter = cli.AntigravityExecutor()
    monkeypatch.setattr(adapter, 'binary', lambda: Path('/test/executable'))
    calls = []
    def invoke(args, *a, **kw):
        calls.append(args)
        return 0, json.dumps({'status':'SUCCESS','response':response}), ''
    monkeypatch.setattr(cli, 'invoke', invoke)
    work = tmp_path / 'work'
    work.mkdir()
    request = ExecRequest('fixture','create','draft','test',None,'new',work)
    outcome = adapter.start(request)
    assert outcome.ok is ok
    assert '--json-schema' not in calls[0]
    if not ok:
        assert outcome.error_class == 'schema' and diagnostic in outcome.diagnostic
    assert (tmp_path / 'raw-response-1.txt').read_text() == response
    request.attempt = 2
    adapter.start(request)
    assert (tmp_path / 'raw-response-2.txt').read_text() == response
    assert (tmp_path / 'raw-response-1.txt').read_text() == response


def test_antigravity_structured_output_keeps_priority(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from workbench.executors import cli
    from workbench.executors.base import ExecRequest
    adapter = cli.AntigravityExecutor()
    monkeypatch.setattr(adapter, 'binary', lambda: Path('/test/executable'))
    def invoke(args, *a, **kw):
        assert '--json-schema' in args
        return 0, json.dumps({'status':'SUCCESS','structured_output':{'ok':1},'response':''}), ''
    monkeypatch.setattr(cli, 'invoke', invoke)
    work = tmp_path / 'work'
    work.mkdir()
    assert adapter.start(ExecRequest('fixture','create','draft','test',{},'new',work)).structured == {'ok':1}
