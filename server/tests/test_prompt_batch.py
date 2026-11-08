from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import pytest

from helpers import Env
from workbench.app import Worker
from workbench.errors import Rejected
from workbench.executors.fake import review, quota_exhausted


def setup_episode(env):
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({sid:[8000] for sid in ("G01","G02","G03")})
    env.save_adopt(eid,"plan",plan)
    content = env.prompts_for(plan,env.image(pid,eid)["versionId"])
    version, _ = env.save_adopt(eid,"prompts",content)
    return eid, version, content


def test_whole_pass_transaction_replay_and_worker_limit(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, version, _ = setup_episode(env)
    def submit(command):
        return wb.start_prompt_review_batch(version,"cold_read",command)
    with ThreadPoolExecutor(max_workers=2) as callers:
        batches = list(callers.map(submit,["tab1","tab2"]))
    assert sorted(len(b["runIds"]) for b in batches) == [0,3]
    full = max(batches,key=lambda b:len(b["runIds"]))
    assert submit("tab1") == batches[0]
    assert wb.pending_runs() == full["runIds"]
    ready, release = Barrier(3), Event()
    def delayed(req):
        ready.wait(timeout=15)
        assert release.wait(timeout=15)
        return review(req)
    env.claude.script = [delayed,delayed]
    worker = Worker(wb)
    try:
        for rid in full["runIds"]:
            worker.submit(rid)
        ready.wait(timeout=15)
        active = wb.prompt_review_status(version)
        assert sorted(r["coldRead"]["activeRun"]["state"] for r in active.values()) == ["queued","running","running"]
        assert not submit("tab3")["runIds"]
        with pytest.raises(Rejected,match="已经|已在"):
            wb.start_prompt_review(version,"G03","cold_read","other-entry")
        with pytest.raises(Rejected,match="整版各段冷读"):
            wb.start_prompt_review_batch(version,"comparison","early")
    finally:
        release.set()
        worker.pool.shutdown(wait=True)
    assert all(v["coldRead"]["state"] == "passed" for v in wb.prompt_review_status(version).values())
    second = wb.start_prompt_review_batch(version,"comparison","ready")
    for rid in second["runIds"]:
        env.drain(rid)
    assert len(second["runIds"]) == 3
    assert all(v["ok"] for v in wb.prompt_review_status(version).values())
    assert not wb.start_prompt_review_batch(version,"comparison","again")["runIds"]


def test_partial_failure_and_invalid_segment(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    eid, version, content = setup_episode(env)
    cold = wb.start_prompt_review_batch(version,"cold_read","initial")["segments"]
    env.claude.script = [quota_exhausted]
    for rid in cold.values():
        env.drain(rid)
    with pytest.raises(Rejected,match="整版各段冷读"):
        wb.start_prompt_review_batch(version,"comparison","before-retry")
    rerun = wb.start_prompt_review_batch(version,"cold_read","manual-retry")
    assert list(rerun["segments"]) == ["G01"]
    env.drain(rerun["runIds"][0])
    assert len(wb.start_prompt_review_batch(version,"comparison","after-retry")["runIds"]) == 3
    del content["segments"]["G02"]
    art = env.art(eid,"prompts")
    partial = wb.save_manual(art["id"],version,content,"partial")["versionId"]
    with pytest.raises(Rejected,match="段号"):
        wb.start_prompt_review_batch(partial,"cold_read","invalid")
    with env.db() as db:
        assert db.execute("SELECT count(*) FROM run WHERE target_version_id=?",(partial,)).fetchone()[0] == 0


def test_stopped_segment_drops_late_result_and_keeps_siblings_queued(tmp_path):
    env = Env(tmp_path)
    _, version, _ = setup_episode(env)
    wb = env.wb
    jobs = wb.start_prompt_review_batch(version,"cold_read","three")["segments"]
    started, release = Event(), Event()
    def delayed(req):
        started.set()
        assert release.wait(15)
        return review(req)
    env.claude.script = [delayed]
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(env.drain,jobs["G01"])
        try:
            assert started.wait(15)
            wb.stop_run(jobs["G01"])
            with env.db() as db:
                assert db.execute("SELECT t.state FROM task t JOIN run r ON t.id=r.task_id WHERE r.id=?",
                                  (jobs["G01"],)).fetchone()[0] == "queued"
        finally:
            release.set()
        future.result()
    state = wb.prompt_review_status(version)
    assert state["G01"]["coldRead"] == {"state":"needed"}
    assert state["G02"]["coldRead"]["activeRun"]["runId"] == jobs["G02"]
    assert list(wb.start_prompt_review_batch(version,"cold_read","resume")["segments"]) == ["G01"]
