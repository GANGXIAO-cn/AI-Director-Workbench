import json
from threading import Event

import pytest

from workbench import videoplan, videosegment
from workbench.videoqueue import VideoQueue


@pytest.fixture
def setup(tmp_path, monkeypatch):
    q = VideoQueue(tmp_path)
    slots = {
        s: [dict(name="猫", protected=True), dict(name="坡", protected=False)]
        for s in "ABCDE"
    }
    monkeypatch.setattr(videoplan, "material", lambda *_: (slots, None))
    return q


def body(seg, deps=None):
    return dict(segment=seg, dependencies=deps or [])


def account(q, name, capacity, pool=""):
    q.account(
        name,
        "https://www.runninghub.ai",
        "plus",
        capacity,
        "sample-" + name,
        pool,
        2,
    )


def test_shared_limit_paused_dependency_and_resume(setup, monkeypatch):
    q = setup
    account(q, "first", 2, "shared")
    account(q, "second", 2, "shared")
    dep = dict(source="A", slot=2, note="ramp", seconds=1)
    q.enqueue_batch("d", [body(s, [dep] if s == "B" else []) for s in "ABCDE"])
    q.set_workflow("h3", "874532")
    gate = Event()
    seen = []

    def hold(j, acct, *_):
        seen.append((j["segment"], acct["name"], j["workflow"]))
        gate.wait(3)

    monkeypatch.setattr(q, "execute", hold)
    q.pause("d", True)
    q.dispatch()
    assert not q.running
    q.pause("d", False)
    q.dispatch()
    q.dispatch()
    gate.set()
    for t in q.running.values():
        t.join(2)
    assert set(seen) == {("A", "first", "874532"), ("C", "second", "874532")}
    assert [j["segment"] for j in q.view("d")["jobs"] if j["state"] == "生成中"] == [
        "A",
        "C",
    ]
    assert "sample" not in json.dumps(q.view("d"))


def test_batch_validation_is_atomic(setup):
    q = setup
    for dep in (
        dict(source="B", slot=2, note="self"),
        dict(source="A", slot=1, note="identity"),
        dict(source="A", slot=4, note="gap"),
    ):
        with pytest.raises(ValueError):
            q.enqueue_batch("d", [body("A"), body("B", [dep])])
        assert q.view("d")["jobs"] == []


def test_three_local_reworks_and_one_adoption(setup, monkeypatch):
    q = setup
    account(q, "only", 1)
    q.enqueue_batch(
        "d", [body("A"), body("B", [dict(source="A", slot=2, seconds=1, note="ramp")])]
    )
    changes = []

    def prepare(data, d, s, deps, sources, out, fixes):
        out.mkdir(parents=True, exist_ok=True)
        changes.append(list(fixes))
        return (), []

    monkeypatch.setattr(videosegment, "prepare", prepare)
    monkeypatch.setattr(videosegment, "generate", lambda *a, **k: None)
    def review(data, delivery, segment, out, *args):
        record = dict(visualReview=dict(decision="局部返工", reason="落点不连贯", localChange="lower the ramp",
                                        usableStart=0, usableEnd=1))
        (out / "expert").mkdir()
        (out / "expert/visual-review.json").write_text(json.dumps(record))
        return record

    monkeypatch.setattr(videosegment, "review", review)
    for i in range(4):
        q.dispatch()
        for t in q.running.values():
            t.join(2)
    jobs = q.view("d")["jobs"]
    history = jobs[0]["reworks"]
    assert [r["number"] for r in history] == [1, 2, 3]
    assert all(r["review"]["reason"] == "落点不连贯" and r["change"] == "lower the ramp" for r in history)
    assert [len(f) for f in changes] == [0, 1, 2, 3]
    assert [(j["state"], j["round"]) for j in jobs] == [("已止损", 3), ("排队中", 0)]


def test_unknown_submission_reserves_slot(setup, monkeypatch):
    q = setup
    account(q, "first", 1)
    q.enqueue_batch("d", [body("A"), body("B")])
    with q.db() as db:
        db.execute("UPDATE job SET state='待核对',account='first' WHERE segment='A'")
    q.dispatch()
    assert not q.running


def test_different_key_limits_allow_three_concurrent_jobs(setup, monkeypatch):
    q = setup
    account(q, "single", 1)
    account(q, "double", 2)
    q.enqueue_batch("d", [body(s) for s in "ABCDE"])
    gate = Event()
    monkeypatch.setattr(q, "execute", lambda *_: gate.wait(2))
    q.dispatch()
    jobs = q.view("d")["jobs"]
    assert sum(j["account"] == "single" for j in jobs) == 1
    assert sum(j["account"] == "double" for j in jobs) == 2
    gate.set()
    for t in q.running.values():
        t.join(2)


def test_adoption_adds_missed_binding_only_to_unsent_automatic_targets(setup):
    q = setup
    q.enqueue_batch("d", [body("A"), dict(**body("B"), automatic=True), body("C")])
    future = q.future_refs("d", "A")
    assert list(future) == ["B"]
    note = dict(slot=2, note="use the striped ramp", seconds=1.25)
    report = dict(
        usableStart=0,
        usableEnd=4,
        carryForward=[dict(target="B", **note), dict(target="C", **note)],
    )
    q.finish(dict(delivery="d", segment="A"), q.directory("d", "A", 0), report, future)
    jobs = q.view("d")["jobs"]
    assert jobs[0]["state"] == "已采用"
    assert jobs[1]["deps"] == [dict(source="A", **note)]
    assert jobs[2]["deps"] == []


def test_queue_releases_dependent_after_adopted_source(setup, monkeypatch):
    q = setup
    account(q, "only", 1)
    binding = dict(source="A", slot=2, note="use the ramp", seconds=1)
    q.enqueue_batch("d", [body("A"), body("B", [binding])])
    seen = []

    def prepare(data, delivery, segment, deps, sources, out, changes):
        seen.append((segment, list(sources), deps))
        out.mkdir(parents=True, exist_ok=True)
        return (), []

    monkeypatch.setattr(videosegment, "prepare", prepare)
    monkeypatch.setattr(videosegment, "generate", lambda *a, **k: None)
    monkeypatch.setattr(
        videosegment, "review", lambda *a, **k: dict(visualReview=dict(decision="采用"))
    )
    for _ in range(3):
        q.dispatch()
        for t in q.running.values():
            t.join(2)
    assert seen == [("A", [], []), ("B", ["A"], [binding])]
    assert all(j["state"] == "已采用" for j in q.view("d")["jobs"])


def test_publish_then_parallel_music_and_video_then_one_finecut(tmp_path, monkeypatch):
    from workbench import productionmusic

    q = VideoQueue(tmp_path)
    ep = {
        "scope": "full",
        "segments": [{"id": "A"}, {"id": "B"}],
        "workbench": {
            "adopted": {"publish": "v1"},
            "page": {
                "publish": {
                    "fields": [
                        ["title", "", "", "film"],
                        ["suno-style", "", "", "90 BPM"],
                    ]
                }
            },
        },
    }
    monkeypatch.setattr(
        videoplan, "material", lambda *_: ({"A": [], "B": []}, lambda _: ep)
    )
    account(q, "both", 2)
    ep["workbench"]["adopted"]["publish"] = None
    with pytest.raises(ValueError, match="发布文案"):
        q.enqueue_batch("d", [body("A"), body("B")], whole_episode=True)
    ep["workbench"]["adopted"]["publish"] = "v1"
    gate = Event()
    monkeypatch.setattr(q, "execute", lambda *_: gate.wait(2))
    monkeypatch.setattr(productionmusic, "generate", lambda *_: gate.wait(2))
    q.enqueue_batch("d", [body("A"), body("B")], whole_episode=True)
    q.enqueue_batch("d", [body("A"), body("B")], whole_episode=True)
    q.dispatch()
    assert set(q.running) == {("d", None), ("d", "A")}
    gate.set()
    for t in q.running.values():
        t.join(2)
    audio = tmp_path / "music.wav"
    audio.write_bytes(b"fixture")
    with q.db() as db:
        db.execute("UPDATE soundtrack SET audio='music.wav',state='已就绪'")
        productionmusic.advance(q, db)
        assert db.execute("SELECT handoff FROM soundtrack").fetchone()[0] is None
        for seg in ("A", "B"):
            folder = q.directory("d", seg, 0)
            (folder / "expert").mkdir(parents=True)
            (folder / "result.mp4").write_bytes(b"sample")
            (folder / "expert/visual-review.json").write_text(
                json.dumps(
                    {
                        "evidence": {"sha256": "sample"},
                        "visualReview": {"usableStart": 0, "usableEnd": 2},
                    }
                )
            )
            db.execute(
                "UPDATE job SET state='已采用',adopted=? WHERE segment=?",
                (str(folder.relative_to(q.data)), seg),
            )
        productionmusic.advance(q, db)
        handoff = db.execute("SELECT handoff FROM soundtrack").fetchone()[0]
        assert [
            c["segment"] for c in json.loads((q.data / handoff).read_bytes())["clips"]
        ] == ["A", "B"]
        productionmusic.advance(q, db)
        assert db.execute("SELECT handoff FROM soundtrack").fetchone()[0] == handoff


def test_suno_uses_published_fields_and_fetches_audio(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from io import BytesIO
    import wave
    import httpx
    from pathlib import Path
    from workbench import productionmusic

    q = VideoQueue(tmp_path)
    spec = dict(
        title="Test",
        workflow="874533",
        style="100 BPM",
        exclude="vocals",
        seed=20,
        segments=["A"],
        publish="v1",
    )
    with q.db() as db:
        db.execute(
            "INSERT INTO soundtrack(delivery,brief,state,account) VALUES('d',?,'生成中','both')",
            (json.dumps(spec),),
        )
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        w.writeframes(bytes(1600))
    graph = json.loads(
        (Path(__file__).resolve().parents[2] / "工作流/suno v5.5_api.json").read_bytes()
    )
    calls = []

    def handler(req):
        if req.url.path.endswith("getJsonApiFormat"):
            return httpx.Response(200, json={"data": {"prompt": json.dumps(graph)}})
        if req.url.path.endswith("create"):
            body = json.loads(req.content)
            calls.append(body)
            return httpx.Response(200, json={"code": 0, "data": {"taskId": "21"}})
        if req.url.path.endswith("outputs"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {"nodeId": "49", "fileUrl": "https://media.invalid/sample.wav"}
                    ],
                },
            )
        return httpx.Response(200, content=buf.getvalue())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(productionmusic.httpx, "Client", lambda **kw: client)
    productionmusic.generate(
        q,
        dict(delivery="d"),
        dict(site="https://www.runninghub.ai", key="example-key", instance="default"),
    )
    music = q.view("d")["music"]
    assert (
        music["state"] == "已就绪"
        and (q.data / music["audio"]).read_bytes() == buf.getvalue()
    )
    assert (
        len(calls) == 1
        and calls[0]["workflowId"] == spec["workflow"]
        and "workflow" not in calls[0]
    )
    assert calls[0]["nodeInfoList"][0]["fieldValue"] == "100 BPM\nAvoid: vocals"


@pytest.mark.parametrize("count", [1, 2, 7])
def test_concurrent_rounds_allow_one_first_and_three_reworks_per_segment(tmp_path, count):
    from concurrent.futures import ThreadPoolExecutor
    from workbench import videobudget

    queue = VideoQueue(tmp_path)
    segments = [f"S{i}" for i in range(count)]
    with queue.db() as db:
        videobudget.register(queue, db, "first", segments)
        db.execute(
            "INSERT INTO video_scope SELECT 'alternate',episode,segments FROM video_scope"
        )

    def spend(delivery, seg, n):
        with queue.db() as db:
            return videobudget.reserve(db, delivery, seg, n)

    for seg in segments:
        assert spend("first", seg, 0) == ""
    with ThreadPoolExecutor(max_workers=3) as pool:
        for n in range(1, 4):
            assert list(pool.map(lambda seg: spend("first", seg, n), segments)) == [""] * count
    with VideoQueue(tmp_path).db() as db:
        budget = videobudget.limits(db, "alternate")
        assert budget["used"] == 0
        assert budget["reserved"] == budget["limit"] == count * 4
        assert budget["reworkLimit"] == count * 3
        assert budget["reworkUsed"] == 0
        assert videobudget.reserve(db, "first", segments[0], 4)
        assert videobudget.reserve(db, "alternate", segments[0], 0)
        assert not videobudget.reserve(db, "first", segments[0], 0)
        assert videobudget.limits(db, "first") == budget
        for seg in segments:
            for n in range(4):
                videobudget.mark(db, "first", seg, n, "success")
        completed = videobudget.limits(db, "alternate")
        assert completed["used"] == count * 4
        assert completed["reworkUsed"] == count * 3
        assert completed["reserved"] == 0


def test_publish_triggers_once_without_browser_and_music_cannot_regenerate(
    tmp_path, monkeypatch
):
    from helpers import Env
    from workbench import filmauto, videoapi
    from workbench.errors import Rejected

    env = Env(tmp_path)
    case = env.full_episode(segments={"G01": [8000], "G02": [8000]}, deliver=False)
    wb = env.wb
    q = VideoQueue(wb.store.root)
    with env.db() as db:
        assert (
            db.execute(
                "SELECT state FROM film_start WHERE episode_id=?", (case["eid"],)
            ).fetchone()[0]
            == "待开工"
        )
    monkeypatch.setattr(videoplan, "plan", lambda *args: [])
    filmauto.advance(wb, q)
    filmauto.advance(wb, q)
    with env.db() as db:
        start = db.execute(
            "SELECT * FROM film_start WHERE episode_id=?", (case["eid"],)
        ).fetchone()
        assert start["state"] == "已开工", start["error"]
    d = start["delivery"]
    assert len(q.view(d)["jobs"]) == 2 and q.view(d)["music"]["state"] == "排队中"
    with q.db() as db:
        db.execute("UPDATE soundtrack SET state='云端失败',task='original',attempted=1")
    route = next(
        r for r in videoapi.router(q, False).routes if r.path.endswith("/bgm/retry")
    )
    with pytest.raises(Rejected):
        route.endpoint(d)
    with q.db() as db:
        assert tuple(
            db.execute("SELECT state,task,attempted FROM soundtrack").fetchone()
        ) == ("云端失败", "original", 1)


@pytest.mark.parametrize("failure", ["timeout", "network", "authentication"])
def test_expert_film_start_recovers_from_planning_failure(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from helpers import Env
    from workbench import filmauto
    from workbench.executors.base import ExecOutcome

    env = Env(tmp_path)
    case = env.full_episode(segments={"G01": [8000], "G02": [8000]}, deliver=False)
    eid = case["eid"]
    env.wb.set_cooperation_mode(eid, "expert", "advanced")
    queue = VideoQueue(env.wb.store.root)
    calls = []
    def request(req):
        calls.append(req)
        assert "阿福发现抽屉" in req.prompt and "G02" in req.prompt
        if len(calls) == 1:
            return ExecOutcome(False, error_class=failure, diagnostic=failure)
        return ExecOutcome(True, structured={"dependencies": []})
    monkeypatch.setattr(videoplan, "episode_expert", lambda *_: (SimpleNamespace(start=request), {}, {}, None))
    clock = [1000]
    monkeypatch.setattr(filmauto, "time", SimpleNamespace(time=lambda: clock[0]))
    filmauto.advance(env.wb, queue)
    with env.db() as db:
        receipt = db.execute("SELECT * FROM film_start_recovery WHERE episode_id=?", (eid,)).fetchone()
        state = db.execute("SELECT state FROM film_start WHERE episode_id=?", (eid,)).fetchone()[0]
    assert receipt["attempts"] == 1
    delivery = receipt["delivery"]
    assert not queue.view(delivery)["jobs"]
    # Reopen the queue as after restart. Unchanged invalid inputs must not resubmit.
    queue = VideoQueue(env.wb.store.root)
    filmauto.advance(env.wb, queue)
    assert len(calls) == 1
    if failure == "authentication":
        assert state == "开工受阻" and receipt["retry_at"] is None
        clock[0] += 1000
        filmauto.advance(env.wb, queue)
        assert len(calls) == 1
        return
    assert state == "等待自动重试" and receipt["retry_at"] > clock[0]
    clock[0] = receipt["retry_at"]
    if failure == "network":
        queue.pause(delivery, True)
        filmauto.advance(env.wb, queue)
        assert len(calls) == 1
        queue.pause(delivery, False)
    filmauto.advance(env.wb, queue)
    filmauto.advance(env.wb, queue)
    assert len(calls) == 2
    with env.db() as db:
        assert tuple(db.execute("SELECT state,error,delivery FROM film_start WHERE episode_id=?", (eid,)).fetchone()) == ("已开工", "", delivery)
    with queue.db() as db:
        assert db.execute("SELECT count(*) FROM job WHERE delivery=?", (delivery,)).fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM soundtrack WHERE delivery=?", (delivery,)).fetchone()[0] == 1


def test_old_account_binding_migrates_and_subsequent_key_edits_are_independent(
    tmp_path,
):
    import sqlite3
    from workbench.storage import sha256_bytes

    q = VideoQueue(tmp_path)
    q.root.mkdir()
    with sqlite3.connect(q.root / "queue.sqlite") as db:
        db.execute(
            "CREATE TABLE account(name TEXT PRIMARY KEY,site TEXT,workflow TEXT,instance TEXT,"
            "capacity INTEGER,pool TEXT,pool_capacity INTEGER,fingerprint TEXT UNIQUE)"
        )
        db.execute(
            "INSERT INTO account VALUES('legacy','https://www.runninghub.ai','101','plus',1,'',1,?)",
            (sha256_bytes(b"sample")[:16],),
        )
    assert q.workflows()[0]["workflow"] == "101"
    q.set_workflow("h3", "202")
    account(q, "new", 2)
    q.account("legacy", "https://www.runninghub.ai", "plus", 3)
    assert all("workflow" not in a for a in q.accounts())
    assert VideoQueue(tmp_path).workflows()[0]["workflow"] == "202"


def test_downloaded_h3_round_uses_original_workflow_after_switch(tmp_path):
    from workbench.storage import sha256_bytes, canonical

    seg = {"params": {"seed": 1}}
    account = {"key": "sample", "site": "https://www.runninghub.ai"}
    video = b"prior download"
    (tmp_path / "result.mp4").write_bytes(video)
    binding = dict(
        site=account["site"],
        workflow="101",
        keyFingerprint=sha256_bytes(b"sample")[:16],
        delivery="d",
        segment="A",
        inputHash=sha256_bytes(canonical([seg, {}])),
    )
    (tmp_path / "trial.json").write_text(
        json.dumps(
            dict(
                binding=binding,
                task="original",
                seed=1,
                status="downloaded",
                sha256=sha256_bytes(video),
            )
        )
    )
    videosegment.generate(
        tmp_path, "d", "A", tmp_path, account, workflow="202", prepared=(seg, {}, {})
    )
    assert json.loads((tmp_path / "trial.json").read_bytes())["task"] == "original"


def test_suno_description_counts_exclusions_and_keeps_final_cue():
    from workbench.productionmusic import description

    spec=dict(style="132 BPM, pizzicato strings, " + "playful groove, "*24 + "warm comedic outro",
              exclude="vocals, lyrics, dark horror")
    text=description(spec)
    assert len(text)<=400 and text.startswith("132 BPM") and "warm comedic outro" in text and text.endswith("dark horror")


def test_only_successful_rounds_count_and_unknown_submissions_keep_their_slot(tmp_path):
    from workbench import videobudget as budget

    q = VideoQueue(tmp_path)
    with q.db() as db:
        budget.register(q, db, "d", ["A", "B"])
        assert budget.reserve(db, "d", "A", 0) == ""
        assert budget.limits(db, "d")["used"] == 0
        assert budget.limits(db, "d")["reserved"] == 1
        budget.mark(db, "d", "A", 0, "failed")
        assert budget.limits(db, "d")["reserved"] == budget.limits(db, "d")["used"] == 0
        assert budget.reserve(db, "d", "A", 0) == ""
        budget.mark(db, "d", "A", 0, "success")
        budget.mark(db, "d", "A", 0, "success")
        assert budget.limits(db, "d")["used"] == 1
        assert budget.limits(db, "d")["reserved"] == 0
        assert budget.reserve(db, "d", "A", 1) == ""
        assert budget.reserve(db, "d", "A", 2) == ""
        assert budget.reserve(db, "d", "A", 3) == ""
        assert budget.reserve(db, "d", "A", 4)
        assert budget.reserve(db, "d", "B", 0) == ""


def test_continuity_plan_and_queue_accept_consecutive_appends_in_any_order(setup):
    slots = {s: [dict(name="九宫格", protected=True)] for s in "ABC"}
    deps = [dict(source=s, target="C", slot=n, note="状态") for s, n in [("B", 3), ("A", 2)]]
    assert videoplan.validate_plan(slots, deps) == deps
    for invalid in [[dict(deps[0], slot=1)], [deps[0]], [deps[1], deps[1]], [dict(deps[1], source="C")],
                    [dict(deps[1], slot=10)], [dict(deps[1], target="missing")]]:
        with pytest.raises(ValueError):
            videoplan.validate_plan(slots, invalid)
    assert videoplan.validate_plan(slots, []) == []


def test_late_multiple_appends_and_existing_reservations(setup):
    q = setup
    q.enqueue_batch("d", [body("A"), body("B"), dict(**body("C", [
        dict(source="A", slot=3, note="已预排")]), automatic=True)])
    future = q.future_refs("d", "B")
    assert future["C"]["planned"][0]["slot"] == 3
    review = dict(usableStart=0, usableEnd=2, carryForward=[
        dict(target="C", slot=n, seconds=1, note="状态") for n in (5, 4, 3)])
    q.finish(dict(delivery="d", segment="B"), q.directory("d", "B", 0), review, future)
    deps = q.view("d")["jobs"][-1]["deps"]
    assert [(d["source"], d["slot"]) for d in deps] == [("A", 3), ("B", 4), ("B", 5)]


def test_planner_asks_expert_once_for_invalid_slot_then_reuses_reply(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    def reply(req):
        calls.append(req)
        return SimpleNamespace(ok=True, structured={"dependencies":[
            dict(source="A",target="B",slot=2 if len(calls)==2 else 4,note="门") ]})
    monkeypatch.setattr(videoplan, "material", lambda *_: ({"A":[],"B":[dict(name="grid",protected=True)]},
        lambda _: dict(shootingSegments=[],shots=[])))
    monkeypatch.setattr(videoplan, "episode_expert", lambda *_: (SimpleNamespace(start=reply),{}, {},None))
    for _ in range(2):
        assert videoplan.plan(tmp_path,"d",tmp_path)[0]["slot"]==2
    assert len(calls)==2 and "连续追加" in calls[1].prompt and calls[1].attempt==2


def test_limit_selects_an_actual_version_and_can_preserve_full_dialogue(setup,monkeypatch):
    from workbench import videoreview
    from workbench.storage import file_sha256
    q=setup
    q.enqueue_batch("d",[body("A")])
    folder=q.directory("d","A",0)
    (folder/"expert").mkdir(parents=True)
    (folder/"result.mp4").write_bytes(b"reviewed-original")
    review=dict(decision="局部返工",reason="图像承接不佳",usableStart=0,usableEnd=0)
    receipt=folder/"expert/visual-review.json"
    receipt.write_text(json.dumps(dict(media={"format":{"duration":"3.5"}},
        evidence=dict(sha256=file_sha256(folder/"result.mp4"),brief=dict(script=[{"dialogue":"Wait!"}])),
        visualReview=review,independentReview=review)))
    def choose(data,delivery,role,purpose,folder,prompt,schema):
        assert role=="expert" and purpose=="limit_decision" and "承接不佳" in prompt
        return dict(result=dict(round=0,action="保留全段交精剪",reason="保留这句对白，精剪处理转场"))
    monkeypatch.setattr(videoreview,"media_verdict",choose)
    q.decide_final_version(dict(delivery="d",segment="A",round=0),"返工名额已用完")
    with q.db() as db:
        row=db.execute("SELECT state,adopted FROM job").fetchone()
    assert row['state']=="已采用" and q.data/row['adopted']==folder
    saved=json.loads(receipt.read_text())
    assert saved['visualReview']['usableEnd']==3.5 and saved['independentReview']['usableEnd']==0


def test_downloaded_review_waits_for_its_retry_deadline(setup):
    import time
    q=setup
    q.enqueue_batch("d",[body("A")])
    job=dict(delivery="d",segment="A",round=0)
    q.defer_media(job,"审片中","评审连接超时")
    q.dispatch()
    assert not q.running
    with q.db() as db:
        assert db.execute("SELECT state FROM job").fetchone()[0]=="审片中"
        assert db.execute("SELECT until FROM media_retry").fetchone()[0]>time.time()


def test_music_handoff_recovers_without_regenerating_or_ignoring_pause(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from workbench import productionmusic
    q=VideoQueue(tmp_path)
    now=[100]
    calls=[]
    def handoff(queue,db,row):
        calls.append((row["task"],row["audio"]))
        if len(calls)==1:
            raise OSError("暂时无法读取配乐文件")
        db.execute("UPDATE soundtrack SET state='已就绪',handoff='finecut/inputs.json' WHERE delivery='d'")
    monkeypatch.setattr(productionmusic,"prepare_handoff",handoff)
    monkeypatch.setattr(productionmusic,"time",SimpleNamespace(time=lambda:now[0]))
    with q.db() as db:
        db.execute("INSERT INTO soundtrack(delivery,brief,state,audio,task) VALUES('d','{}','已就绪','original.wav','original-task')")
        productionmusic.advance(q,db)
        productionmusic.advance(q,db)
    now[0]+=120
    q=VideoQueue(tmp_path)
    q.pause("d",True)
    with q.db() as db:
        productionmusic.advance(q,db)
    assert calls==[("original-task","original.wav")]
    q.pause("d",False)
    with q.db() as db:
        productionmusic.advance(q,db)
        productionmusic.advance(q,db)
        assert db.execute("SELECT handoff FROM soundtrack").fetchone()[0]=="finecut/inputs.json"
    assert calls==[("original-task","original.wav")]*2
