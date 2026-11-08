import json

from helpers import Env
from workbench.executors.fake import draft


def test_missing_inputs_and_format_retry_terminate_in_records(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid,eid = env.new_episode()
    task = wb.create_task(eid,"story","先改动作")
    original = wb.store.run_dir(task["runId"])/"input-manifest.json"
    original.rename(original.with_suffix(".offline"))
    assert wb.execute_run(task["runId"])==[]
    record = wb.task_records(project_id=pid)[0]
    assert record["state"]==record["runs"][0]["status"]=="failed"
    assert record["runs"][0]["manifestError"] and not env.codex.calls
    def crash(request):
        raise RuntimeError("fixture interrupted conversion")
    env.codex.script.extend([lambda req:{},crash])
    second = wb.create_task(eid,"story","续写")
    wb.execute_run(second["runId"])
    run = next(t for t in wb.task_records(project_id=pid) if t["id"]==second["taskId"])["runs"][0]
    assert run["status"]=="failed" and run["format_attempts"]==1
    assert "fixture interrupted" in run["diagnostic"] and len(env.codex.calls)==2
    again = wb.retry_run(second["runId"])
    wb.execute_run(again["runId"])
    assert len(env.art(eid,"story")["versions"])==1


def test_result_failure_after_reply_and_wrapped_correction(tmp_path, monkeypatch):
    env = Env(tmp_path)
    wb = env.wb
    pid,eid = env.new_episode()
    failed = wb.create_task(eid,"story","入库故障")
    def db_failure(*args):
        raise RuntimeError("fixture write failure")
    with monkeypatch.context() as m:
        m.setattr(wb,"_ingest",db_failure)
        wb.execute_run(failed["runId"])
    saved = wb.store.run_dir(failed["runId"])/"result.json"
    assert json.loads(saved.read_bytes())["kind"]=="draft"
    assert wb.task_records(project_id=pid)[0]["state"]=="failed"
    env.codex.script.extend([lambda req:{"result":{}},lambda req:{"result":draft(req)}])
    new = wb.create_task(eid,"story","修正带选区的回包",selection={"field":"synopsis"})
    wb.execute_run(new["runId"])
    records=wb.task_records(project_id=pid)
    run=next(r for t in records for r in t["runs"] if r["id"]==new["runId"])
    assert (run["status"],run["format_attempts"])==("succeeded",1)
