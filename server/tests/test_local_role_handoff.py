"""Role handoffs through the local workbench, isolated from providers and production data."""
import json
import pytest

from helpers import Env
from workbench import filmauto
from workbench.videoqueue import VideoQueue


@pytest.mark.parametrize("pause_before_encoding", [False, True])
def test_expert_runs_from_story_to_registered_film_without_opening_pages(tmp_path, monkeypatch, pause_before_encoding):
    env = Env(tmp_path)
    wb = env.wb
    roles = {role: dict(provider="codex", model=name) for role, name in
             (("create", "writer"), ("review", "reader"), ("expert", "director"))}
    _, eid = env.new_episode(plan=roles)
    wb.set_cooperation_mode(eid, "expert", "advanced")
    value = wb.start_continuous(eid, target_stage="delivery", command_id="start")
    assert value["state"] == "done", value["reason"]
    assert value["choice"] is None
    delivery = value["cursor"]["deliveryId"]
    assert wb.verify_delivery(delivery)["ok"]
    with env.db() as db:
        versions = json.loads(db.execute("SELECT versions FROM delivery WHERE id=?", (delivery,)).fetchone()[0])
        reviews = dict(db.execute("SELECT a.stage, count(*) FROM review r JOIN version v ON v.id=r.version_id "
                                  "JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=? AND r.scope='' GROUP BY a.stage", (eid,)))
        assert reviews == {"story":1, "plan":1}
        for stage in ("story", "characters", "world", "treatment", "script", "stress", "asset_design", "plan", "publish"):
            assert db.execute("SELECT 1 FROM ai_decision WHERE episode_id=? AND stage=? AND kind='expert_judgment' AND ref=?",
                              (eid, stage, versions[stage])).fetchone()
        assert db.execute("SELECT publish FROM film_start WHERE episode_id=?", (eid,)).fetchone()[0] == versions["publish"]
    assert all(r.model == "reader" for r in env.codex.calls if r.kind == "review" or r.purpose == "image_assess" and "只有 blocked" in r.prompt)
    assert any(r.model == "writer" and r.purpose=="create" for r in env.codex.calls)
    verdicts = [r for r in env.codex.calls if "退回写稿人" in r.prompt]
    assert verdicts and all(r.model == "director" for r in verdicts)
    assert any(r.model=="director" and "独立评审。reply 按每个 versionId" in r.prompt for r in env.codex.calls)
    before=len(env.codex.calls)
    first=wb.delivery_checklist(eid)
    assert first == wb.delivery_checklist(eid) and not first["problems"]
    assert len(env.codex.calls)==before
    assert wb.delivery_view(delivery)["versions"]["publish"] == versions["publish"]

    # Replace the two remote media outputs with tiny playable files. The queue,
    # time-coded frame extraction, adoption, soundtrack handoff and final render are real.
    import subprocess
    from workbench import videosegment, productionmusic, filmedit
    from workbench.executors.base import ExecOutcome
    from workbench.storage import file_sha256
    queue=VideoQueue(wb.store.root)
    filmauto.advance(wb,queue)
    with queue.db() as db:
        job=dict(db.execute("SELECT * FROM job WHERE delivery=?",(delivery,)).fetchone())
    def generate(data, delivery, segment, directory, *args, **kwargs):
        directory.mkdir(parents=True,exist_ok=True)
        movie=directory/"result.mp4"
        subprocess.run(["ffmpeg","-nostdin","-loglevel","error","-f","lavfi","-i","color=size=96x128:rate=12:duration=1","-c:v","libx264",str(movie)],check=True)
        (directory/"trial.json").write_text(json.dumps(dict(status="downloaded",sha256=file_sha256(movie))))
    monkeypatch.setattr(videosegment,"generate",generate)
    film_writer, video_reader, cut_reader = "writer", "reader", "reader"
    if pause_before_encoding:
        film_writer, video_reader, cut_reader = "cut-writer", "clip-reviewer", "cut-reviewer"
        wb.save_film_executor(eid, "package", "review", "codex", video_reader)
        wb.save_film_executor(eid, "finecut", "review", "codex", cut_reader)
        wb.save_film_executor(eid, "finecut", "create", "codex", film_writer)
    expert=env.codex
    native=expert.start
    retry_final=[True]
    def review_clip(req):
        if req.purpose=="finecut_plan" and pause_before_encoding:
            assert queue.view(delivery)["cut"]["action"]=="写稿 AI 正在拟剪辑表"
            queue.pause(delivery,True)
        if req.purpose=="finecut_review" and retry_final:
            retry_final.pop()
            return ExecOutcome(ok=False,error_class="timeout",diagnostic="隔离测试模拟临时超时")
        if req.purpose=="video_review":
            assert req.model==video_reader and req.images and req.images[0].is_file()
            return ExecOutcome(ok=True,structured=dict(decision="采用",reason="首尾均有画面",observations=["0–1秒为测试色板"],
                usableStart=0,usableEnd=1,localChange="",stateSecond=None,affectedShots=[]))
        return native(req)
    monkeypatch.setattr(expert,"start",review_clip)
    queue.execute(dict(job,state="生成中",workflow="offline"),{},[],{})
    audio=queue.root/delivery/"offline-bgm.wav"
    subprocess.run(["ffmpeg","-nostdin","-loglevel","error","-f","lavfi","-i","sine=frequency=400:duration=1",str(audio)],check=True)
    if pause_before_encoding:
        queue.pause(delivery,True)
    with queue.db() as db:
        actual=db.execute("SELECT state,error FROM job WHERE delivery=?",(delivery,)).fetchone()
        assert actual["state"]=="已采用",dict(actual)
        db.execute("UPDATE soundtrack SET state='已就绪',audio=? WHERE delivery=?",(str(audio.relative_to(queue.data)),delivery))
        productionmusic.advance(queue,db)
    folder=queue.root/delivery/"finecut"
    if pause_before_encoding:
        queue=VideoQueue(wb.store.root)
        assert queue.view(delivery)["paused"] and not queue.view(delivery)["music"]["handoff"]
        filmedit.advance(wb,queue)
        assert not (folder/"render.json").exists()
        queue.pause(delivery,False)
        with queue.db() as db:
            productionmusic.advance(queue,db)
        filmedit.advance(wb,queue)
        assert filmedit.status(folder)["state"]=="等待恢复精剪" and not (folder/"rendering.mp4").exists()
        queue=VideoQueue(wb.store.root)
        queue.pause(delivery,False)
    filmedit.advance(wb,queue)
    pending=filmedit.status(folder)
    assert pending["state"]=="等待自动重试" and not (folder/"final.mp4").exists()
    encoded=(folder/"rendering.mp4").stat().st_mtime_ns
    now=pending["retryAt"]+1
    monkeypatch.setattr(filmedit.time,"time",lambda:now)
    filmedit.advance(wb,queue)
    assert filmedit.status(folder)["state"]=="已出片",filmedit.status(folder)
    assert (folder/"final.mp4").stat().st_mtime_ns==encoded
    assert sum(r.purpose=="finecut_plan" for r in expert.calls)==1
    assert {(r.purpose,r.model) for r in expert.calls} >= {("video_decision","director"),("finecut_plan",film_writer),("finecut_review",cut_reader),("finecut_decision","director")}
    assert filmedit.status(folder)["finalReview"]["audio"]=="未试听音频"
    before=len(expert.calls)
    filmauto.advance(wb,queue)
    filmedit.advance(wb,queue)
    assert len(expert.calls)==before


def test_skipping_script_diagnosis_reviews_the_script(tmp_path):
    env=Env(tmp_path)
    _,eid=env.new_episode()
    env.wb.set_optional_stage(eid,"stress",True,reason="仅运行剧本独审")
    result=env.wb.start_continuous(eid,command_id="skip-diagnosis")
    assert result["state"]=="done",result["reason"]
    with env.db() as db:
        row=db.execute("SELECT r.pass,a.adopted_version_id,r.version_id FROM review r JOIN version v ON v.id=r.version_id "
                       "JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=? AND a.stage='script'",(eid,)).fetchone()
        assert row and row["pass"]=="script" and row["adopted_version_id"]==row["version_id"]


def test_video_preparation_pause_restart_and_source_release(tmp_path, monkeypatch):
    """Replay the E21 two-shot lettering fault before releasing the next segment."""
    from types import SimpleNamespace
    from workbench import h3rework, videoplan, videosegment, production_steps
    from test_h3rework import ORIGINAL
    from test_videoqueue import body, account

    q=VideoQueue(tmp_path)
    a,b="seg-05","seg-06"
    monkeypatch.setattr(videoplan,"material",lambda *_: ({a:[],b:[]},None))
    account(q,"mock",1)
    q.enqueue_batch("d",[body(a),body(b,[dict(source=a,slot=1,seconds=1,note="承接桶身")])])
    text=ORIGINAL.replace("The door opens.",'The jug reads "CAR WASH".').replace(
        "\n\noverall_soundscape:", '\n[Shot 3] The door opens.\n[Shot 4] The pail reads "CAR WASH".\n\noverall_soundscape:')
    prompts=[]
    def write(req):
        prompts.append(req.prompt)
        return SimpleNamespace(ok=True,structured=dict(edits=[
            dict(shot=n,before=f'The {name} reads "CAR WASH".',
                 after=f'The blue {name} tips.' if len(prompts)==1 else f'The blue {name} reads "CAR WASH".')
            for n,name in [(1,"jug"),(4,"pail")]]))
    monkeypatch.setattr(videosegment,"episode_expert",lambda *_:(SimpleNamespace(start=write),{}, {},None))
    submitted=[]
    def prepare(data,delivery,segment,deps,sources,out,changes):
        out.mkdir(parents=True,exist_ok=True)
        if segment==a:
            revised=h3rework.compile_revision(data,delivery,text,['[Shot 1] [Shot 4] 保留桶身字'],[],out.parent)
            assert revised==text.replace('The jug reads','The blue jug reads').replace('The pail reads','The blue pail reads')
            if not paused:
                assert q.view("d")["jobs"][1]["action"]=="等待 seg-05 采用"
                paused.append(True)
                q.pause("d",True)
        else:
            assert list(sources)==[a]
        production_steps.step("工作台正在上传参考素材")
        return (),[]
    paused=[]
    monkeypatch.setattr(videosegment,"prepare",prepare)
    monkeypatch.setattr(videosegment,"generate",lambda data,delivery,segment,*args,**kw:submitted.append(segment))
    monkeypatch.setattr(videosegment,"review",lambda *args:dict(visualReview=dict(decision="采用")))
    def tick():
        q.dispatch()
        for thread in q.running.values():
            thread.join(3)
    tick()
    assert paused and not submitted and len(prompts)==2
    assert '第 1 镜缺少' in prompts[1] and '第 4 镜缺少' in prompts[1]
    q=VideoQueue(tmp_path)
    tick()
    assert q.view("d")["paused"] and not submitted
    q.pause("d",False)
    tick()
    tick()
    tick()
    assert submitted==[a,b] and len(prompts)==2
    assert [j["state"] for j in q.view("d")["jobs"]]==["已采用","已采用"]
