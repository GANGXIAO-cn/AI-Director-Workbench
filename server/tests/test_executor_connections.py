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
