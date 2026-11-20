import json
import shutil
import subprocess

import pytest

from helpers import Env
from workbench import filmedit
from workbench.videoqueue import VideoQueue


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),reason="requires FFmpeg")
def test_revision_from_chat_reuses_media_and_changes_movie(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    case=env.full_episode(segments={"G01":[8000],"G02":[8000]})
    eid, delivery=case["eid"], case["delivery"]["deliveryId"]
    with env.db() as db:
        db.execute("UPDATE film_start SET state='已开工',delivery=? WHERE episode_id=?", (delivery,eid))
        db.commit()
    data=wb.store.root
    queue=VideoQueue(data)
    folder=queue.root/delivery/"finecut"
    folder.mkdir(parents=True)
    source=folder/"clip.mp4"
    bgm=folder/"music.wav"
    def generate(*args):
        subprocess.run(["ffmpeg","-nostdin","-loglevel","error",*args],check=True)
    generate("-f","lavfi","-i","color=size=96x128:rate=30:duration=1","-c:v","libx264",str(source))
    generate("-f","lavfi","-i","sine=frequency=400:duration=0.4",str(bgm))
    handoff=folder/"inputs.json"
    handoff.write_text(json.dumps(dict(bgm=str(bgm.relative_to(data)),clips=[
        dict(segment=s,video=str(source.relative_to(data)),review=dict(usableStart=0,usableEnd=1)) for s in ["G01","G02"]])))
    with queue.db() as db:
        db.execute("INSERT INTO soundtrack(delivery,state,handoff,brief) VALUES(?,'已就绪',?,'{}')",(delivery,str(handoff.relative_to(data))))
    filmedit.advance(wb,queue)
    def metrics():
        path=folder/"final.mp4"
        from array import array
        raw=subprocess.check_output(["ffmpeg","-loglevel","error","-i",str(path),"-map","0:a","-ac","1","-f","s16le","-"])
        rms=(sum(x*x for x in array("h",raw))/(len(raw)/2))**.5
        return float(filmedit.probe(path)["format"]["duration"]),rms
    duration,volume=metrics()
    assert wb.episode_view(eid)["productionStatus"]["finecut"]
    assert wb.episode_view(eid)["productionStatus"]["package"]
    expert=wb.episode_view(eid)["kickoff"]["executor_plan"]["expert"]["provider"]
    new_ops=[dict(action="trim_head",segment="G02",value=.5),dict(action="bgm_db",segment="",value=-22)]
    wb.executors[expert].script.append(lambda req:{"reply":"收紧第 2 段开头并压低音乐。","edits":new_ops})
    turn=wb.send_free_chat(case["pid"],"第二段开头剪半秒，音乐降 6 分贝",expert,episode_id=eid,stage="finecut",delivery_id=delivery)
    wb.execute_free_chat(turn["turnId"],turn["attemptId"])
    wb.execute_free_chat(turn["turnId"],turn["attemptId"])
    assert not wb.episode_view(eid)["productionStatus"]["finecut"]
    assert len(wb.film_chat_context(eid,"finecut",delivery)["剪辑修改记录"])==1
    filmedit.advance(wb,queue)
    revised,quieter=metrics()
    assert wb.episode_view(eid)["productionStatus"]["finecut"]
    assert revised==pytest.approx(duration-.5,abs=.05)
    assert .43 < quieter/volume < .57
    stamp=(folder/"final.mp4").stat().st_mtime_ns
    filmedit.advance(wb,queue)
    assert (folder/"final.mp4").stat().st_mtime_ns==stamp


@pytest.mark.parametrize("body_seconds, expected_calls", [(58, 1), (60.77, 1), (81, 1), (57, 2)])
def test_planning_range_does_not_trigger_recut(tmp_path, monkeypatch, body_seconds, expected_calls):
    from types import SimpleNamespace
    from workbench import bundledfinecut

    (tmp_path / "03_精剪交接.md").write_text("规划 70–80 秒")
    (tmp_path / "outro-duration.json").write_text("2")
    (tmp_path / "体检").mkdir()
    (tmp_path / "体检/analysis.json").write_text('{"issues":[],"shots":[],"contact_sheets":[]}')
    (tmp_path / "bgm_beats.json").write_text('{"bpm":110,"downbeats":[],"duration":90}')
    (tmp_path / "segments.json").write_text('{"segments":[]}')
    calls = []

    def start(req):
        calls.append(req)
        seconds = body_seconds if len(calls) == 1 else 58
        cut = dict(segment="SEG01", start=0, end=seconds, speed=1, note="保留动作")
        return SimpleNamespace(ok=True, structured=dict(cuts=[cut], summary="", events=[], musicBreaks=[]))

    monkeypatch.setattr(bundledfinecut, "episode_expert", lambda *_: (SimpleNamespace(start=start), {}, {}, tmp_path))
    inputs = dict(clips=[dict(segment="SEG01", review=dict(usableStart=0, usableEnd=90))], bgmSha256="test")
    result = bundledfinecut.editing_plan(tmp_path, "test", tmp_path, inputs)
    assert len(calls) == expected_calls
    assert result["cuts"][0]["end"] + 2 >= 60


def test_adopted_dialogue_overrides_visual_trim_and_planner_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from workbench import bundledfinecut as cut

    (tmp_path/"交接来源").mkdir()
    (tmp_path/"交接来源/剧本.json").write_text(json.dumps({"shootingSegments":[
        {"id":"s1","dialogue":"Wait for me!"}, {"id":"s2","dialogue":"无对白。"}]}))
    (tmp_path/"03_精剪交接.md").write_text("压缩停顿")
    (tmp_path/"outro-duration.json").write_text("2")
    (tmp_path/"体检").mkdir()
    (tmp_path/"体检/analysis.json").write_text('{"issues":[],"shots":[],"contact_sheets":[]}')
    (tmp_path/"bgm_beats.json").write_text('{"bpm":100,"downbeats":[],"duration":90}')
    (tmp_path/"segments.json").write_text(json.dumps({"segments":[
        {"rough_t0":0,"rough_t1":15}, {"rough_t0":15,"rough_t1":66}]}))
    inputs = dict(clips=[dict(segment=s, review=dict(usableStart=1, usableEnd=end)) for s,end in [("s1",14),("s2",50)]], bgmSha256="music")
    calls = []
    def start(req):
        calls.append(req)
        plan = dict(summary="", events=[], musicBreaks=[], cuts=[
            dict(segment="s1",start=1,end=5,speed=1,note="保留反应前半"),
            dict(segment="s1",start=7,end=12,speed=1,note="跳过说话"),
            dict(segment="s2",start=1,end=47,speed=1,note="保留动作")])
        if req.schema is cut.REPLAN:
            # First attempt only describes a change. Second supplies a visual correction.
            plan.update(cutaways=[] if len(calls)==2 else [dict(segment="s1", start=2, end=4, sourceSegment="s2", sourceStart=6)],
                        visualChecks=[dict(before=dict(start=2,end=4),after=dict(start=2,end=4))],
                        reframes=[] if len(calls)==2 else [dict(segment="s1",start=2,end=4,zoom=2,x=0.8,y=0.3)])
        return SimpleNamespace(ok=True, structured=plan)
    monkeypatch.setattr(cut,"episode_expert",lambda *_:(SimpleNamespace(start=start),{},{},tmp_path))
    plan = cut.editing_plan(tmp_path,"test",tmp_path,inputs)
    assert "Wait for me!" in calls[0].prompt and "压缩停顿" in calls[0].prompt
    assert [(c["segment"],c["start"],c["end"],c["speed"]) for c in plan["cuts"]] == [("s1",0,15,1),("s2",1,47,1)]
    assert cut.editing_plan(tmp_path,"test",tmp_path,inputs)==plan and len(calls)==1
    feedback = [dict(text="按剧本修改结尾的接点",edits=[dict(action="replan",segment="",value=0)])]
    revised = cut.editing_plan(tmp_path,"test",tmp_path,inputs, feedback)
    assert revised["cutaways"][0]["sourceSegment"] == "s2" and revised["cuts"][0] == plan["cuts"][0]
    assert "没有可执行" in calls[2].prompt
    assert len(calls)==3 and feedback[0]["text"] in calls[1].prompt


def test_revision_mode_queues_feedback_even_if_expert_only_explains(tmp_path):
    env = Env(tmp_path)
    case = env.full_episode(segments={"G01":[8000],"G02":[8000]})
    with env.db() as db:
        db.execute("UPDATE project SET config_id='puggy' WHERE id=?", (case["pid"],))
        db.commit()
    wb, eid, pid = env.wb, case["eid"], case["pid"]
    delivery = case["delivery"]["deliveryId"]
    provider = wb.episode_view(eid)["kickoff"]["executor_plan"]["expert"]["provider"]
    previous = thread = None
    for mode in ("discuss", "revise"):
        wb.executors[provider].script.append(lambda _: {"reply":"对白剪断了。", "edits":[]})
        turn = wb.send_free_chat(pid,"第一段的对白被截断了",provider,thread_id=thread,previous=previous,
                                 episode_id=eid,stage="finecut",delivery_id=delivery,finecut_mode=mode)
        previous, thread = turn["turnId"], turn["threadId"]
        wb.execute_free_chat(previous, turn["attemptId"])
        latest = wb.free_chat(pid,episode_id=eid,stage="finecut")["turns"][-1]
        assert latest["status"] == "succeeded" and latest["editQueued"] == (mode=="revise")
    changes = wb.film_chat_context(eid, "finecut", delivery)["剪辑修改记录"]
    assert changes == [dict(text="第一段的对白被截断了",edits=[dict(action="replan",segment="",value=0)])]


@pytest.mark.skipif(not shutil.which("ffmpeg"),reason="requires FFmpeg")
def test_visual_revision_changes_only_requested_frames_and_preserves_audio(tmp_path, monkeypatch):
    from workbench.bundledfinecut import SKILL
    monkeypatch.syspath_prepend(str(SKILL/"finecut"))
    from render import Timeline, render_video
    from source_versions import fingerprint
    source = tmp_path/"source.mp4"
    subprocess.run(["ffmpeg","-loglevel","error","-f","lavfi","-i","smptebars=size=160x96:rate=30",
                    "-f","lavfi","-i","sine=frequency=400","-t","1","-c:v","libx264","-c:a","aac",str(source)],check=True)
    (tmp_path/"体检").mkdir()
    (tmp_path/"体检/analysis.json").write_text(json.dumps(dict(source_fingerprint=fingerprint(source))))
    def rendered(fx):
        render_video(Timeline([(0,1,fx,"")]),str(source),str(tmp_path))
        path=str(tmp_path/"cut_raw.mov")
        def decode(*args):
            return subprocess.check_output(["ffmpeg","-v","error","-i",path,*args,"pipe:1"])
        return decode("-an","-pix_fmt","gray","-f","rawvideo"), decode("-vn","-f","s16le")
    old,audio = rendered(None)
    new,new_audio = rendered(dict(reframes=[dict(first=10,stop=20,zoom=2,x=.9,y=.5)]))
    assert audio == new_audio and len(old)==len(new)==30*160*96
    def diff(frame):
        a=frame*160*96; b=a+160*96
        return sum(abs(i-j) for i,j in zip(old[a:b],new[a:b]))/(b-a)
    assert diff(9)<2 and diff(10)>10 and diff(19)>10 and diff(20)<2


def test_failed_feedback_review_retries_and_preserves_published_movie(tmp_path, monkeypatch):
    from workbench import bundledfinecut as cut
    from workbench.storage import file_sha256, sha256_bytes, canonical
    (tmp_path/"inputs.json").write_text("{}")
    revision=sha256_bytes(canonical([file_sha256(tmp_path/"inputs.json"),[]]))
    (tmp_path/"render.json").write_text(json.dumps(dict(published=revision,state="渲染中")))
    (tmp_path/"final.mp4").write_bytes(b"old")
    changes = [dict(text="关门后下一镜不能重新敞开", edits=[dict(action="replan",segment="",value=0)])]
    calls=[]
    monkeypatch.setattr(cut,"run_skill",lambda *_: None)
    def plan(*args):
        calls.append(args[-1])
        return dict(cuts=[], musicBreaks=[])
    monkeypatch.setattr(cut,"editing_plan",plan)
    def rendered(work,*_):
        path=work/"candidate.mp4"
        path.write_bytes(b"candidate")
        return path
    monkeypatch.setattr(cut,"render_plan",rendered)
    def reject(data, delivery, work, before, after, feedback, plan):
        assert before.read_bytes()==b"old" and after.read_bytes()==b"candidate"
        assert feedback==changes[0]["text"]
        raise cut.FeedbackMismatch("新版门仍然打开")
    monkeypatch.setattr(cut,"verify_feedback",reject)
    with pytest.raises(cut.FeedbackMismatch,match="门仍然打开"):
        cut.render(tmp_path,"delivery",dict(clips=[],bgm="bgm.wav",bgmSha256="test"), changes, tmp_path/"rendering.mp4")
    assert calls[0] is None and len(calls)==2 and "新版门仍然打开" in calls[1]["验收意见"]
    assert (tmp_path/"final.mp4").read_bytes()==b"old" and not (tmp_path/"rendering.mp4").exists()


def test_targeted_revision_preserves_unrequested_segments():
    from workbench.bundledfinecut import check_revision
    clips = [dict(segment=s,start=0,end=10,speed=1) for s in ("s1","s2")]
    base = dict(cuts=clips, musicBreaks=[])
    revised = dict(base,cuts=[clips[0],dict(clips[1],end=9)], reframes=[dict(segment="s1",start=2,end=3,zoom=2,x=.5,y=.5)],
                   visualChecks=[dict(before=dict(start=2,end=3),after=dict(start=2,end=3))])
    with pytest.raises(ValueError,match="s2 不在本次反馈"):
        check_revision(revised,base,[dict(out0=0,out1=10),dict(out0=10,out1=20)])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires FFmpeg")
def test_cutaway_uses_selected_frames_and_keeps_original_audio(tmp_path, monkeypatch):
    from workbench.bundledfinecut import SKILL, check_revision
    monkeypatch.syspath_prepend(str(SKILL/"finecut"))
    from render import Timeline, render_video
    from workbench_run import cutaway_frames
    from source_versions import fingerprint

    rough = tmp_path/"rough.mp4"
    subprocess.run(["ffmpeg","-loglevel","error","-f","lavfi","-i",
                    "nullsrc=s=96x64:r=30,geq=lum=20+3*N:cb=128:cr=128",
                    "-f","lavfi","-i","aevalsrc=sin(2*PI*(200*t+80*t*t)):s=48000",
                    "-t","2","-c:v","libx264","-crf","0","-c:a","aac",str(rough)],check=True)
    (tmp_path/"体检").mkdir()
    (tmp_path/"体检/analysis.json").write_text(json.dumps(dict(source_fingerprint=fingerprint(rough))))
    sources = {"A":dict(rough_t0=0,rough_t1=1), "B":dict(rough_t0=1,rough_t1=2)}
    clip = dict(segment="B",start=0,end=1,speed=1)
    replacement = dict(segment="B",start=.2,end=.5,sourceSegment="A",sourceStart=.1)
    edits = [replacement,dict(segment="B",start=.5,end=.6,sourceSegment="B",sourceStart=0)]
    plan = dict(cuts=[clip], reframes=[],cutaways=edits,musicBreaks=[],
                visualChecks=[dict(before=dict(start=.2,end=.6),after=dict(start=.2,end=.6))])
    check_revision(plan,None,source_frames={"A":30,"B":30})
    with pytest.raises(ValueError,match="源素材"):
        check_revision(dict(plan,cutaways=[dict(replacement,sourceStart=.9)]),None,source_frames={"A":30,"B":30})
    def decode(path, *args):
        return subprocess.check_output(["ffmpeg","-v","error","-i",str(path),*args,"pipe:1"])
    def levels(path):
        pixels=decode(path,"-an","-pix_fmt","gray","-f","rawvideo")
        return list(pixels[::96*64])
    movie = tmp_path/"cut_raw.mov"
    render_video(Timeline([(1,2,None,"")]),str(rough),str(tmp_path))
    original_audio = decode(movie,"-vn","-f","s16le")
    frames = cutaway_frames(clip, sources, edits)
    render_video(Timeline([(1,2,dict(cutaways=frames),"")]),str(rough),str(tmp_path))
    assert decode(movie,"-vn","-f","s16le") == original_audio
    original = levels(rough)
    assert levels(movie) == pytest.approx(original[30:36]+original[3:12]+original[30:33]+original[48:60],abs=1)
