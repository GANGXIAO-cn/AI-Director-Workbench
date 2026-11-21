import json
from types import SimpleNamespace

import pytest
from workbench import h3rework, videosegment


ORIGINAL = (
    "subject_definitions:\n<Subject 1> is the cat in <Picture 1>.\n\nsummary:\nTwo cats.\n\n"
    "retention_analysis:\nTabby fur.\n\ndetailed_description:\n[Shot 1] The door opens.\n"
    "[Shot 2] At 00:04.000, the cat sits up. The scooter passes.\n\noverall_soundscape:\nEngine.\n\nnon_diegetic_music:\nN/A"
)
OLD = "The scooter passes."
NEW = "The scooter rolls beside the dining table."
EDIT = dict(shot=2, before=OLD, after=NEW)


def prior_submission(out, prompt):
    out.mkdir(parents=True, exist_ok=True)
    (out / "trial.json").write_text(json.dumps(dict(task="offline-task", status="downloaded", nodeInfoList=[
        dict(nodeId="263", fieldName="text", fieldValue=prompt)])))


def frozen_board(tmp_path, monkeypatch, windows):
    from workbench.storage import file_sha256
    path = tmp_path / "交接来源/制作方案.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(dict(shots=windows)))
    monkeypatch.setattr(videosegment, "frozen_delivery", lambda *_: (
        tmp_path, dict(files={"交接来源/制作方案.json":file_sha256(path)})))


def test_prepare_rework_replaces_only_the_original_shot_and_reuses_it(
    tmp_path, monkeypatch
):
    calls = []

    class Expert:
        def start(self, req):
            calls.append(req)
            if req.purpose == "prompt_confirmation":
                return SimpleNamespace(ok=True, structured=dict(action="确认提交", reason="符合本轮要求"))
            return SimpleNamespace(ok=True, structured=dict(edits=[EDIT]))

    monkeypatch.setattr(
        videosegment,
        "load_segment",
        lambda *_: (dict(prompt=ORIGINAL, refs=[]), {}, {}),
    )
    monkeypatch.setattr(
        videosegment, "episode_expert", lambda *_: (Expert(), {}, {}, None)
    )
    out = tmp_path / "round-1"
    notes = ["[Shot 2] 餐桌位置错了，需要改走廊反应镜。"]
    prior_submission(tmp_path/"round-0", ORIGINAL)
    frozen_board(tmp_path, monkeypatch, [dict(id=str(i),segment="SEG01",order=i) for i in [1,2]])
    for _ in range(2):
        prepared, _ = videosegment.prepare(
            tmp_path, "delivery", "SEG01", [], {}, out, notes
        )
        assert prepared[0]["prompt"] == ORIGINAL.replace(OLD, NEW)
        assert json.loads((out / "bindings.json").read_text())["changes"] == notes
    assert [req.purpose for req in calls] == ["revise", "prompt_confirmation"] and notes[0] in calls[0].prompt
    original_request = ORIGINAL + "historical request"
    (out / "trial.json").write_text(
        json.dumps(
            dict(
                task="already-sent",
                status="query_error",
                nodeInfoList=[
                    dict(nodeId="263", fieldName="text", fieldValue=original_request)
                ],
            )
        )
    )
    prepared, _ = videosegment.prepare(
        tmp_path, "delivery", "SEG01", [], {}, out, notes
    )
    assert prepared[0]["prompt"] == original_request and len(calls) == 2


@pytest.mark.parametrize(
    "edit",
    [
        dict(EDIT, shot=1),
        dict(EDIT, after="Replace the shot with the scooter."),
        dict(EDIT, after="Fix the background position."),
        dict(EDIT, after="参照前次审核意见"),
        dict(EDIT, after="At 00:08.000, the scooter stops."),
        dict(EDIT, after="The scooter passes <Subject 8>."),
    ],
)
def test_invalid_revisions_never_become_a_video_request(edit):
    with pytest.raises((ValueError, KeyError)):
        h3rework.apply_shot_edits(ORIGINAL, [edit], ["Fix the background position."])


def test_existing_chinese_format_preserves_other_time_windows():
    text = "【镜头与动作】\n00:00.000-00:01.000\n猫进门。\n00:01.000-00:03.000\n猫低头。\n【声音与文字】\n风声。"
    assert h3rework.apply_shot_edits(
        text, [dict(shot=2, before="猫低头。", after="猫抬头。")]
    ) == text.replace("猫低头。", "猫抬头。")


def test_reports_all_embedded_shot_labels_but_accepts_bound_picture_labels():
    edits = [
        dict(shot=1, before="The door opens.", after="The door from [Shot 2] opens."),
        dict(EDIT, after="The scooter stops at the door from [Shot 1] in <Picture 2>."),
    ]
    with pytest.raises(ValueError) as error:
        h3rework.apply_shot_edits(ORIGINAL, edits, slots=[2])
    assert "第 1 镜含 [Shot 2]" in str(error.value)
    assert "第 2 镜含 [Shot 1]" in str(error.value)
    assert "<Picture 2>" not in str(error.value)


def test_reference_note_becomes_visible_geometry_in_the_relevant_shot():
    note = "Use this frame for the ramp geometry."
    edited = (
        "The magazine in <Picture 9> slopes from the floor to the stationary vacuum."
    )
    with pytest.raises(ValueError, match="承接图"):
        h3rework.apply_shot_edits(ORIGINAL, [EDIT], [note], [9])
    actual = h3rework.apply_shot_edits(
        ORIGINAL, [dict(EDIT, after=edited)], [note], [9]
    )
    assert actual == ORIGINAL.replace(OLD, edited)


def test_unclear_planned_state_preserves_adopted_references(tmp_path, monkeypatch):
    from workbench import videoreview
    from workbench.storage import file_sha256
    source=tmp_path/'previous'
    (source/'expert').mkdir(parents=True)
    (source/'result.mp4').write_bytes(b'reviewed')
    (source/'expert/visual-review.json').write_text(json.dumps(dict(
        visualReview=dict(usableStart=0,usableEnd=3),evidence=dict(sha256=file_sha256(source/'result.mp4')))))
    seg=dict(prompt=ORIGINAL, refs=[dict(assetId='original')])
    assets={'original':dict(kind='image',name='local reference')}
    monkeypatch.setattr(videosegment,'load_segment',lambda *_:(seg,assets,{}))
    monkeypatch.setattr(videoreview,'pick_source_time',lambda *_:None)
    dependency=dict(source='SEG01',slot=1,seconds=None,note='look for a cable')
    out=tmp_path/'segment'/'round-0'
    prepared,bindings=videosegment.prepare(tmp_path,'episode','SEG06',[dependency],{'SEG01':source},out)
    assert prepared[0]['prompt']==ORIGINAL and prepared[0]['refs']==[{'assetId':'original'}]
    assert not bindings
    assert json.loads((out/'bindings.json').read_bytes())['skipped'][0]['slot']==1


@pytest.mark.parametrize("no_interval", [False, True])
def test_second_append_uses_first_free_port_when_first_frame_missing(tmp_path, monkeypatch, no_interval):
    from workbench import videoreview
    from workbench.storage import file_sha256
    sources = {}
    for sid in ("A", "B"):
        folder = tmp_path / sid
        (folder / "expert").mkdir(parents=True)
        (folder / "result.mp4").write_bytes(sid.encode())
        (folder / "state.png").write_bytes(sid.encode())
        (folder / "expert/visual-review.json").write_text(json.dumps(dict(
            visualReview=dict(usableStart=0, usableEnd=0 if sid=="A" and no_interval else 3),
            evidence=dict(sha256=file_sha256(folder/"result.mp4")))))
        sources[sid] = folder
    seg = dict(prompt=ORIGINAL, refs=[dict(assetId="grid")])
    monkeypatch.setattr(videosegment, "load_segment", lambda *_: (seg, {"grid": dict(kind="image")}, {}))
    monkeypatch.setattr(videoreview, "pick_source_time", lambda *_: None)
    monkeypatch.setattr(videoreview, "state_frame", lambda *_: dict(file="state.png", imageHash="B", sourceHash="B", seconds=1))
    monkeypatch.setattr(h3rework, "compile_revision", lambda data, delivery, prompt, changes, bindings, directory:
                        h3rework.apply_shot_edits(prompt, [dict(EDIT, after="The scooter passes <Picture 2>.")],
                                                 slots=[b["slot"] for b in bindings]))
    deps = [dict(source="A", slot=2, seconds=None, note="地面"), dict(source="B", slot=3, seconds=1, note="餐盘")]
    prepared, bindings = videosegment.prepare(tmp_path, "d", "C", deps, sources, tmp_path/"out")
    assert [r["assetId"] for r in prepared[0]["refs"]] == ["grid", "B"]
    assert bindings[0]["plannedSlot"] == 3 and bindings[0]["slot"] == 2


def test_end_time_uses_an_actual_frame_inside_reviewed_interval(tmp_path, monkeypatch):
    from workbench import videoreview
    media = tmp_path / "video.mp4"
    media.write_bytes(b"media")
    def run(args):
        if args[0]=="ffprobe":
            return json.dumps(dict(streams=[dict(start_time="0",duration="1.0")],
                frames=[dict(best_effort_timestamp_time=v) for v in [0,.5,.96]])).encode()
        return b"\x89PNG\r\n\x1a\n"+b"frame"
    monkeypatch.setattr(videoreview.subprocess,"check_output",run)
    assert videoreview.state_frame(media,tmp_path,1,.5,1)["seconds"] == .96
    assert videoreview.state_frame(media,tmp_path,1,.99,1) is None
    with pytest.raises(ValueError):
        videoreview.state_frame(media,tmp_path,1.5)


@pytest.mark.parametrize("end,valid", [(9.417, True), (9.416667, True), (9.416, True), (9.418, False), (3.9, False)])
def test_review_at_millisecond_label_keeps_actual_video_endpoint(tmp_path, monkeypatch, end, valid):
    from workbench import videoreview
    video = tmp_path / "result.mp4"
    video.write_bytes(b"fixture")
    duration = 9.416667
    monkeypatch.setattr(videoreview, "frame_sheets", lambda *_: (
        {"format": {"duration": str(duration)}}, [0, 4, 9.25], []))
    def review(req):
        assert "原片 9.416667 秒" in req.prompt
        return SimpleNamespace(ok=True, session_id="test", structured=dict(
            decision="采用并附剪点", reason="动作完成", observations=[], usableStart=4, usableEnd=end,
            localChange="", stateSecond=end, carryForward=[dict(target="next", slot=2, note="道具", seconds=end)]))
    args = (video, tmp_path / "expert", SimpleNamespace(reads_images=True, start=review),
            {}, {}, {"future": ["next"]})
    if not valid:
        with pytest.raises(ValueError, match="实际素材范围"):
            videoreview.visual_review(*args)
        return
    record = videoreview.visual_review(*args)["visualReview"]
    assert record["usableEnd"] == record["stateSecond"] == record["carryForward"][0]["seconds"] == min(duration, end)
    assert record["usableStart"] == 4 and record["decision"] == "采用并附剪点"


def test_required_label_is_checked_per_shot_including_partial_edits(tmp_path, monkeypatch):
    source = ORIGINAL.replace("The door opens.", 'The sign reads "EXIT".').replace(OLD, 'The scooter passes "EXIT".')
    edits = [dict(shot=n, before="EXIT", after="HERE") for n in (1, 2)]
    with pytest.raises(ValueError) as e:
        h3rework.apply_shot_edits(source, edits)
    assert '第 1 镜缺少 "EXIT"' in str(e.value) and '第 2 镜缺少 "EXIT"' in str(e.value)
    def start(req):
        assert json.dumps({"1": ['"EXIT"'], "2": ['"EXIT"']}) in req.prompt
        return SimpleNamespace(ok=True, structured=dict(edits=[dict(shot=1,before="sign",after="door sign")]))
    monkeypatch.setattr(videosegment, "episode_expert", lambda *_:(SimpleNamespace(start=start), {}, {}, None))
    assert h3rework.compile_revision(tmp_path,"d",source,[],[],tmp_path) == source.replace("The sign", "The door sign")


def test_writer_repairs_missing_sign_on_the_named_shot(tmp_path,monkeypatch):
    original=ORIGINAL.replace(OLD,'The scooter passes the "CAR WASH" sign.')
    old='The scooter passes the "CAR WASH" sign.'
    replies=['The scooter stops beside the sign.', 'The scooter stops beside the "CAR WASH" sign.']
    seen=[]
    def start(request):
        seen.append(request.prompt)
        return SimpleNamespace(ok=True,structured=dict(edits=[dict(shot=2,before=old,after=replies[len(seen)-1])]))
    def selected(data,delivery,role,stage):
        assert (role,stage)==("create","package")
        return SimpleNamespace(start=start),{}, {}, None
    monkeypatch.setattr(videosegment,"episode_expert",selected)
    for _ in range(2):
        revised=h3rework.compile_revision(tmp_path,"delivery",original,["[Shot 2] 停在招牌旁边"],[],tmp_path)
        assert revised==original.replace(old,replies[-1])
    assert len(seen)==2 and '第 2 镜缺少' in seen[-1] and 'CAR WASH' in seen[-1]


def test_pause_while_selecting_frame_reuses_expert_choice_on_restart(tmp_path,monkeypatch):
    from workbench import production_steps, videoreview
    from workbench.cloudtask import PollInterrupted
    from workbench.storage import file_sha256
    from workbench.videoqueue import VideoQueue

    q=VideoQueue(tmp_path)
    q.pause("d",False)
    source=tmp_path/"source"
    (source/"expert").mkdir(parents=True)
    (source/"result.mp4").write_bytes(b"reviewed-source")
    (source/"expert/visual-review.json").write_text(json.dumps(dict(frameTimes=[1],
        visualReview=dict(usableStart=0,usableEnd=2),evidence=dict(sha256=file_sha256(source/"result.mp4")))))
    calls=[]
    def pick(req):
        calls.append(req)
        q.pause("d",True)
        return SimpleNamespace(ok=True,structured={"seconds":1})
    monkeypatch.setattr(videosegment,"episode_expert",lambda *_:(SimpleNamespace(start=pick),{}, {},source))
    monkeypatch.setattr(videosegment,"load_segment",lambda *_:(dict(prompt=ORIGINAL,refs=[]),{},{}))
    frames=[]
    monkeypatch.setattr(videoreview,"state_frame",lambda *args:frames.append(args[2]))
    labels=[]
    def report(label):
        q.check_dispatch("d")
        labels.append(label)
    args=(tmp_path,"d","SEG06",[dict(source="SEG05",slot=1,seconds=None,note="桶身")],
          {"SEG05":source},tmp_path/"round-0")
    with production_steps.observe(report):
        with pytest.raises(PollInterrupted):
            videosegment.prepare(*args)
        assert len(calls)==1 and not frames and "SEG05" in labels[0]
        q=VideoQueue(tmp_path)
        assert q.view("d")["paused"]
        q.pause("d",False)
        videosegment.prepare(*args)
    assert len(calls)==1 and frames==[1] and len(labels)==2


def test_prepare_translates_review_windows_to_frozen_prompt_shots(tmp_path, monkeypatch):
    from workbench.storage import file_sha256

    windows = [dict(id=f"S01-0{i}", segment="A", order=i,
                    continuation="same" if i == 3 else "cut") for i in range(1, 7)]
    board = tmp_path / "交接来源/制作方案.json"
    board.parent.mkdir()
    board.write_text(json.dumps(dict(shots=windows)))
    source = ORIGINAL.replace("\n\noverall_soundscape:", "\n[Shot 3] Three.\n[Shot 4] Four.\n[Shot 5] Five.\n\noverall_soundscape:")
    notes = ["[Shot 1] [Shot 4] [Shot 5] [Shot 6] 修正点名动作"]
    seen = []
    monkeypatch.setattr(videosegment, "load_segment", lambda *_: (dict(prompt=source, refs=[]), {}, {}))
    monkeypatch.setattr(videosegment, "frozen_delivery", lambda *_: (tmp_path, dict(files={"交接来源/制作方案.json": file_sha256(board)})))
    def revise(data, delivery, text, changes, bindings, directory, **kwargs):
        seen.extend(changes)
        return text
    monkeypatch.setattr(h3rework, "compile_revision", revise)
    out = tmp_path / "round-1"
    from workbench import videoreview
    monkeypatch.setattr(videoreview, "media_verdict", lambda *args: dict(result=dict(action="确认提交",reason="已核对")))
    prior_submission(tmp_path/"round-0", source)
    videosegment.prepare(tmp_path, "delivery", "A", [], {}, out, notes)
    assert seen[0].startswith("[Shot 1] [Shot 3] [Shot 4] [Shot 5] ")
    assert "S01-04 对应提示词第 3 镜" in seen[0]
    assert json.loads((out / "bindings.json").read_bytes())["changes"] == notes
    assert h3rework.writer_changes(source, windows, ["[Shot 4] 修正点名动作"])[0].startswith("[Shot 3] ")
    with pytest.raises(ValueError, match="无法对应"):
        h3rework.writer_changes(source, windows[:2], notes)


def test_seg01_consecutive_retakes_only_submit_the_current_expert_approved_edit(tmp_path, monkeypatch):
    from workbench import videoplan
    from workbench.videoqueue import VideoQueue
    from workbench.h3online import RunningHub
    from workbench.executors.base import ExecOutcome

    queue = VideoQueue(tmp_path)
    delivery, segment = "offline-delivery", "SEG01"
    monkeypatch.setattr(videoplan, "material", lambda *_: ({segment:[]}, None))
    queue.enqueue_batch(delivery, [dict(segment=segment, dependencies=[])])
    queue.account("offline", "https://www.runninghub.ai", "plus", 1, "dummy-offline")
    windows = [dict(id=f"S01-0{i}", segment=segment, order=i, continuation="same" if i==3 else "cut")
               for i in range(1,7)]
    frozen_board(tmp_path, monkeypatch, windows)
    base = ORIGINAL.replace("\n\noverall_soundscape:", "\n[Shot 3] Three.\n[Shot 4] Four.\n[Shot 5] Five.\n\noverall_soundscape:")
    monkeypatch.setattr(videosegment, "load_segment", lambda *_: (dict(prompt=base, refs=[], params=dict(seed=42,instanceType="plus")), {}, {}))
    changes = ["[Shot 1] 第一轮调整开门", "[Shot 4] 第二轮调整走位", "[Shot 6] 第三轮调整收尾"]
    first = base.replace("The door opens.", "The red door opens.")
    second = first.replace("Three.", "Three steps forward.")
    final = second.replace("Five.", "Five steps back.")
    submissions, approvals, events = [], [], []
    rejected = []
    def writer(req):
        assert req.purpose=="revise"
        material = json.JSONDecoder().raw_decode(req.prompt.split("以下为原稿及工作台记录。\n")[1])[0]
        task = material["本轮返工任务"]
        n = task["round"]
        assert material["source"] == submissions[-1]
        assert len(material["reviews"]) == 1 and material["reviews"][0].startswith(f"[Shot {[1,3,5][n-1]}]")
        assert all(c not in material["reviews"][0] for c in changes[:n-1])
        # In round three, first try to rewrite a previously approved shot too.
        edits = [dict(shot=1,before="The door opens.",after="The red door opens.")] if n==1 else [
            dict(shot=3,before="Three.",after="Three steps forward.")] if n==2 else [
            dict(shot=5,before="Five.",after="Five steps back." if rejected else "Five stays still.")]
        if n==3 and not rejected and "上次答复" not in req.prompt:
            edits.append(dict(shot=1,before="red",after="blue"))
        if rejected:
            assert task["expertFeedback"]=="收尾要后退" and "Five stays still." in task["previousCandidate"]
        events.append(("write",n))
        return ExecOutcome(ok=True, structured=dict(edits=edits))
    def expert(req):
        assert req.purpose=="prompt_confirmation"
        task = json.loads(req.prompt.split("\n",1)[1])
        assert task["source"]==submissions[-1]
        assert task["expertReason"] == f"本轮仅修指定窗口 {task['round']}"
        if "Five stays still." in task["candidate"]:
            rejected.append(True)
            return ExecOutcome(ok=True, structured=dict(action="退回补修", reason="收尾要后退"))
        approvals.append(task["candidate"])
        events.append(("approve",task["round"]))
        return ExecOutcome(ok=True, structured=dict(action="确认提交", reason="已落实且其余镜头沿用"))
    def select(data,d,role,stage):
        assert stage=="package" and role in ("create","expert")
        return SimpleNamespace(start=writer if role=="create" else expert), dict(provider=role), {}, None
    monkeypatch.setattr(videosegment, "episode_expert", select)
    monkeypatch.setattr(RunningHub,"graph",lambda *_:{})
    monkeypatch.setattr(videosegment,"overrides",lambda graph,seg,*args,**kw:[
        dict(nodeId="263",fieldName="text",fieldValue=seg["prompt"])])
    def submit(hub, workflow, nodes, instance):
        text = nodes[0]["fieldValue"]
        if submissions:
            assert approvals[-1] == text and events[-1]==("approve",len(submissions))
        events.append(("submit",len(submissions)))
        submissions.append(text)
        return dict(code=0,data=dict(taskId=f"fake-{len(submissions)}"))
    monkeypatch.setattr(RunningHub,"submit",submit)
    def fetch(hub, task, path, *args, **kwargs):
        path.write_bytes(b"local test")
        return {"code":0}
    monkeypatch.setattr(videosegment,"fetch_output",fetch)
    monkeypatch.setattr(videosegment.subprocess,"check_output",lambda *_:b'{"format":{"duration":1},"streams":[{"codec_type":"video"}]}')
    def review(data,delivery,segment,out,*args):
        n=int(out.name.split("-")[1])
        verdict=dict(decision="局部返工" if n<3 else "采用", reason=f"本轮仅修指定窗口 {n+1}",
                     localChange=changes[n] if n<3 else "")
        record=dict(visualReview=verdict)
        (out/"expert").mkdir(exist_ok=True)
        (out/"expert/visual-review.json").write_text(json.dumps(record))
        return record
    monkeypatch.setattr(videosegment,"review",review)
    for _ in range(5):
        queue.dispatch()
        for t in queue.running.values():
            t.join(5)
        if rejected and len(submissions)==3:
            with queue.db() as db:
                assert db.execute("SELECT state,round FROM job").fetchone()[:] == ("排队中",3)
                db.execute("UPDATE media_retry SET until=0")  # Advance the local retry clock.
    assert submissions == [base, first, second, final]
    assert approvals == submissions[1:]
    assert events.count(("write",3)) == 3
    assert queue.view(delivery)["jobs"][0]["state"] == "已采用"
    # Recovering an already submitted task reads its request, without another model call.
    before=list(events)
    recovered,_=videosegment.prepare(tmp_path,delivery,segment,[],{},queue.directory(delivery,segment,3),changes)
    assert recovered[0]["prompt"] == final and events==before


def test_retake_cannot_use_the_delivery_draft_if_previous_prompt_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(videosegment,"load_segment",lambda *_:(dict(prompt=ORIGINAL,refs=[]),{},{}))
    out = tmp_path/"round-2"
    prior_submission(tmp_path/"round-0", ORIGINAL)
    with pytest.raises(ValueError, match="上一版 H3 已接单"):
        videosegment.prepare(tmp_path,"offline","SEG01",[],{},out,["[Shot 2] 本轮"])
    assert not (out/"trial.json").exists()


def test_pending_expert_confirmation_resumes_without_rewriting_or_resubmitting(tmp_path, monkeypatch):
    from workbench.executors.base import ExecOutcome
    from workbench.videoreview import MediaCallError
    from workbench.filmconversation import messages
    from workbench.videoqueue import VideoQueue

    q = VideoQueue(tmp_path)
    out = q.directory("d","SEG01",1)
    prior_submission(out.parent/"round-0", ORIGINAL)
    frozen_board(tmp_path, monkeypatch, [dict(id=str(n),segment="SEG01",order=n) for n in [1,2]])
    monkeypatch.setattr(videosegment,"load_segment",lambda *_:(dict(prompt="different from the receipt",refs=[]),{},{}))
    roles=[]
    def selected(data,delivery,role,stage):
        def start(req):
            roles.append(role)
            if role=="create":
                return ExecOutcome(ok=True,structured=dict(edits=[EDIT]))
            if roles.count("expert")==1:
                return ExecOutcome(ok=False,error_class="timeout",diagnostic="fixture")
            return ExecOutcome(ok=True,structured=dict(action="确认提交",reason="符合本轮要求"))
        return SimpleNamespace(start=start),dict(provider=role),{},None
    monkeypatch.setattr(videosegment,"episode_expert",selected)
    args=(tmp_path,"d","SEG01",[],{},out,["[Shot 2] 修正位置"])
    with pytest.raises(MediaCallError):
        videosegment.prepare(*args)
    assert not (out/"trial.json").exists()
    for _ in range(2):
        prepared,_=videosegment.prepare(*args)
        assert prepared[0]["prompt"]==ORIGINAL.replace(OLD,NEW)
    assert roles==["create","expert","expert"]
    feed=messages(q,"d","package",dict(jobs=[dict(segment="SEG01",round=1)]))
    assert any(m["role"]=="专家" and "提示词确认 · 确认提交" in m["title"] for m in feed)
