import pytest

from helpers import Env
from workbench.errors import Conflict, NotFound, Rejected

PLAN = dict(create={"provider":"claude"}, review={"provider":"codex"})
IDEA = "让主角说出最后一句台词。"
RULE = "全片无对白"


def setup_case(env):
    wb = env.wb
    pid = wb.create_project("_template", "默片开工核对")["projectId"]
    wb.propose_setting_change(pid, "dialogue", RULE, "required", "本片采用默片形式", "silence")
    return dict(project_id=pid, display_code="E01", source_type="idea", idea_text=IDEA)


def report(req):
    assert IDEA in req.prompt and RULE in req.prompt
    return dict(checks=[dict(key="dialogue", verdict="conflict", settingQuote=RULE, requestQuote=IDEA,
                             reason="主角说出台词需要对白。")])


def test_optional_current_cli_and_cache(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    source = setup_case(env)
    local = wb.kickoff_check_view(source)
    assert local["state"] == "local" and not env.claude.calls
    # Accepting one phrase must not clear a later finding about another passage.
    wb.kickoff_check_decide(source,local["basis"],"dialogue","主角",False)
    env.claude.script = [report]
    result = wb.kickoff_check_run(source, local["basis"], "claude")
    assert result["state"] == "done" and result["blocked"]
    assert wb.kickoff_check_run(source, local["basis"], "codex") == result
    assert not env.codex.calls and len(env.claude.calls) == 1
    with pytest.raises(Conflict,match="冲突"):
        wb.start_episode(None, executor_plan=PLAN, **source)
    assert wb.kickoff_check_view(source) == result
    assert wb.list_projects()[0]["episodes"] == 0
    changed = dict(source,idea_text="让主角在纸上写下最后一句话。")
    assert wb.kickoff_check_view(changed)["state"] == "local"
    wb.start_episode(None,executor_plan=PLAN,**changed)
    assert len(env.claude.calls) == 1


def test_manual_and_unverified_cli_output(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    source = setup_case(env)
    basis = wb.kickoff_check_view(source)["basis"]
    with pytest.raises(Rejected,match="原文"):
        wb.kickoff_check_decide(source,basis,"dialogue","杜撰引文",True)
    assert wb.kickoff_check_decide(source,basis,"dialogue",IDEA,True)["blocked"]
    with pytest.raises(Conflict):
        wb.start_episode(None,executor_plan=PLAN,**source)
    def invented(req):
        result = report(req)
        result["checks"][0]["requestQuote"]="主角大喊"
        return result
    env.codex.script = [invented]
    failed = wb.kickoff_check_run(source,basis,"codex",command_id="one")
    assert failed["state"] == "failed" and failed["blocked"] and "引文" in failed["error"]
    assert wb.kickoff_check_run(source,basis,"codex",command_id="one") == failed
    assert len(env.codex.calls) == 1
    wb.propose_setting_change(source["project_id"],"dialogue","允许对白","required","改为有声片","revision")
    with pytest.raises(Conflict):
        wb.kickoff_check_decide(source,basis,"dialogue",IDEA,True)
    assert not wb.kickoff_check_view(source)["blocked"]


def test_import_request_and_ownership(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    source = setup_case(env)
    pid = source["project_id"]
    other = wb.create_project("_template","另一项目")["projectId"]
    imported = wb.import_file(pid, "故事.txt", "从站台开场。".encode(), "story_source")
    source = dict(project_id=pid,display_code="E01",source_type="import",source_import_id=imported["importId"],goal=IDEA)
    view = wb.kickoff_check_view(source)
    assert IDEA in view["request"] and "从站台开场。" in view["request"]
    with pytest.raises(NotFound):
        wb.kickoff_check_view(dict(source,project_id=other))
    wb.kickoff_check_decide(source,view["basis"],"dialogue",IDEA,True)
    with pytest.raises(Conflict):
        wb.start_episode(None,executor_plan=PLAN,**source)
    assert not env.claude.calls


@pytest.mark.parametrize("verdict", ["compatible","uncertain"])
def test_topic_result_and_duplicate_click(tmp_path, verdict):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    env = Env(tmp_path)
    wb = env.wb
    pid = setup_case(env)["project_id"]
    tid = wb.add_topic(pid, "T01", "主角在纸上写下最后一句台词。")["topicId"]
    source = dict(project_id=pid,display_code="E02",source_type="topic",topic_id=tid,goal="按默片方式表现。")
    basis = wb.kickoff_check_view(source)["basis"]
    entered, finish = Event(), Event()
    def delayed(req):
        entered.set()
        assert finish.wait(5)
        return dict(checks=[dict(key="dialogue",verdict=verdict,settingQuote=RULE,
                                 requestQuote="按默片方式表现。",reason="需区分写字与发声。")])
    env.codex.script = [delayed]
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(wb.kickoff_check_run,source,basis,"codex")
        try:
            assert entered.wait(3)
            assert wb.kickoff_check_run(source,basis,"claude")["state"] == "running"
            with pytest.raises(Conflict,match="正在核对"):
                wb.start_episode(None,executor_plan=PLAN,**source)
        finally:
            finish.set()
        result = first.result()
    assert result["checks"][0]["verdict"] == verdict and not result["blocked"]
    wb.start_episode(None,executor_plan=PLAN,**source)
    assert len(env.codex.calls)==1 and not env.claude.calls
