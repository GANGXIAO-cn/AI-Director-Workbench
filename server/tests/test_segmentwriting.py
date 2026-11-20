import json
from test_continuous_finishing import finishing_fixture
from workbench.executors.fake import draft, sections
from workbench.executors.base import ExecOutcome


def test_repeated_storyboard_times_reach_writer_without_stopping(tmp_path):
    env, case, _, _ = finishing_fixture(tmp_path)
    plan = case['plan']
    note = '首帧（第2—3秒）看见金光，首秒（全片第2—3秒）下摇露出水槽'
    plan['shots'][0]['action'] = note
    env.save_adopt(case['eid'],'plan',plan,'annotated-shot')
    job = env.wb.create_task(case['eid'],'prompts','按段整理',rebuild=True,prompt_images=False,
                             executor={'create':{'provider':'codex'}})
    seen = []
    def write(req):
        packet = sections(req.prompt)
        sid, = json.loads(packet['prompt_scaffold'])
        seen.append(sid)
        if sid == 'G01':
            assert json.loads(packet['plan_layout'])['shots'][0]['action'] == note
            assert json.loads(packet['prompt_scaffold'])[sid]['refs']
            assert '不在正文内另起时间线' in req.prompt
        return draft(req, segments=json.dumps({sid:dict(txt='Shot 1: The camera tilts down to the trough.',sound={})}))
    env.codex.script = [write,write]
    env.drain(job['runId'])
    assert seen == ['G01','G02']
    with env.db() as db:
        assert db.execute('SELECT status FROM run WHERE id=?',(job['runId'],)).fetchone()[0] == 'succeeded'


def test_retry_starts_at_unfinished_segment(tmp_path):
    env, case, _, _ = finishing_fixture(tmp_path)
    wb = env.wb
    job = wb.create_task(case["eid"], "prompts", "逐段起草", rebuild=True, prompt_images=False,
                         executor={"create":{"provider":"codex"}})
    seen = []
    def write(req):
        packet = sections(req.prompt)
        sid, = json.loads(packet["prompt_scaffold"])
        seen.append(sid)
        assert {s["segment"] for s in json.loads(packet["plan_layout"])["shots"]} == {sid}
        return draft(req, segments=json.dumps([{"id":sid, "txt":"Shot 1: The dog steps forward.", "sound":{}}]))
    env.codex.script = [write, lambda req: ExecOutcome(False,error_class="timeout")]
    env.drain(job["runId"])
    with env.db() as db:
        rows = db.execute("SELECT segment FROM prompt_segment_result").fetchall()
        assert [r[0] for r in rows] == ["G01"]
    retry = wb.retry_run(job["runId"])
    env.codex.script = [write]
    env.drain(retry["runId"])
    assert seen == ["G01","G02"]
    with env.db() as db:
        run = db.execute("SELECT status,diagnostic FROM run WHERE id=?", (retry["runId"],)).fetchone()
        assert run["status"] == "succeeded", run["diagnostic"]

def test_merge_same_reminder_across_segments():
    from workbench.segmentwriting import combined_fields
    item = dict(id="P08", reportVersionId="report", owner="prompts", status="applied")
    result = combined_fields([dict(productionNoteResponses=[dict(item,whereIds=[sid],reason=sid)])
                              for sid in ("seg-01","seg-02")])
    merged, = result["productionNoteResponses"]
    assert merged["whereIds"] == ["seg-01","seg-02"]
    assert merged["reason"].splitlines() == merged["whereIds"]


def test_decision_quotes_are_checked_and_repaired_per_segment(tmp_path):
    env, case, _, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case["eid"]
    code = wb.add_decision(eid,"描写狗的动作","prompts","episode",[],"dog-action")["code"]
    job = wb.create_task(eid,"prompts","逐段落实要求",rebuild=True,prompt_images=False,
                         executor={"create":{"provider":"codex"}})
    seen = []
    lines = {"G01":"The dog steps forward.", "G02":"The dog steps back."}

    def writer(req):
        sid, = json.loads(sections(req.prompt)["prompt_scaffold"])
        seen.append(sid)
        quote = lines["G01"] if len(seen) < 4 else lines[sid]
        note = dict(decision=code,status="applied",where=sid,quote=quote)
        return draft(req,segments=json.dumps({sid:dict(txt="Shot 1: "+lines[sid],sound={})}),
                     decisionNotes=json.dumps([note]))

    env.codex.script = [writer]*3
    env.drain(job["runId"])
    with env.db() as db:
        assert [r[0] for r in db.execute("SELECT segment FROM prompt_segment_result")] == ["G01"]
    env.codex.script = [writer]
    retry = wb.retry_run(job["runId"])
    env.drain(retry["runId"])
    assert seen == ["G01","G02","G02","G02"]
    with env.db() as db:
        run = db.execute("SELECT status,diagnostic FROM run WHERE id=?",(retry["runId"],)).fetchone()
        assert run["status"] == "succeeded", run["diagnostic"]
    notes = env.art(eid,"prompts")["versions"][-1]["content"]["decisionNotes"]
    assert [(n["decision"],n["segmentId"],n["quote"]) for n in notes] == [(code,s,txt) for s,txt in lines.items()]


def test_local_revision_keeps_other_decision_quotes():
    from workbench.decisions import revised_segment_notes, check_decision_notes
    old = [dict(decision="D01",segmentId=sid,status="applied",where=sid,quote="Old.")
           for sid in ("G01","G02")]
    change = dict(decision="D01",status="applied",where="G02",quote="New.")
    updated = revised_segment_notes(old,[change],{"G02"},{"G01","G02"})
    assert updated == [old[0],dict(change,segmentId="G02")]
    content = dict(segments={"G01":{"txt":"Old."},"G02":{"txt":"New."}},decisionNotes=updated)
    check = lambda: check_decision_notes([{"code":"D01"}],"prompts",content,{})
    assert check() == []
    updated.append(updated[-1])
    assert check() == ["G02：决定 D01 的落点重复"]


def test_writer_repairs_only_failed_segment_with_previous_reply(tmp_path):
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case['eid']
    wb.save_episode_expert(eid,'antigravity','chosen-expert')
    job = wb.create_task(eid,'prompts','按段写',rebuild=True,prompt_images=False,
                         executor={'create':{'provider':'codex'}})
    wb._continuous_cursor(cid,stage='prompts')
    before=(len(env.codex.calls),len(env.ag.calls))
    def good(req):
        sid, = json.loads(sections(req.prompt)['prompt_scaffold'])
        return draft(req,segments=json.dumps({sid:{'txt':'Shot 1: The dog steps forward.','sound':{}}}))
    def broken(req):
        return draft(req,segments=json.dumps({'WRONG':{'txt':'A red umbrella.','sound':{}}}))
    def different_error(req):
        assert req.model!='chosen-expert' and 'A red umbrella.' in req.prompt
        return draft(req,segments=json.dumps({'G02':{'txt':'','sound':{}}}))
    def fixed(req):
        assert req.model!='chosen-expert' and 'previousResponse' in req.prompt
        assert list(json.loads(sections(req.prompt)['prompt_scaffold']))==['G02']
        return good(req)
    env.codex.script=[good,broken,broken,different_error,different_error,fixed]
    wb._continuous_execute(cid,job['runId'])
    assert len(env.codex.calls)-before[0]==6 and len(env.ag.calls)==before[1]
    assert wb._continuous_record(cid)['state']=='running'
    assert set(env.art(eid,'prompts')['versions'][-1]['content']['segments'])=={'G01','G02'}


def test_notes_only_revision_preserves_prompt_and_sibling_notes(tmp_path):
    import pytest
    from workbench.errors import Rejected
    env, case, cid, _ = finishing_fixture(tmp_path)
    wb, eid = env.wb, case['eid']
    writer = {'create':{'provider':'codex'}}
    initial = wb.create_task(eid,'prompts','写两段',rebuild=True,prompt_images=False,executor=writer)
    def first(req):
        sid, = json.loads(sections(req.prompt)['prompt_scaffold'])
        return draft(req,segments=json.dumps({sid:{'txt':'Shot 1: The dog pauses.','sound':{}}}),
                     notes=sid+' 服装见图6')
    env.codex.script=[first,first]
    env.drain(initial['runId'])
    base=env.art(eid,'prompts')['versions'][-1]
    job=wb.create_task(eid,'prompts','只修第一段图号',base_version_id=base['id'],prompt_segments=['G01'],
                       prompt_notes_only=True,prompt_images=False,executor=writer)
    def correct(req):
        packet=sections(req.prompt)
        assert list(json.loads(packet['prompt_scaffold']))==['G01']
        assert 'G02' not in packet['base_version']
        return draft(req,notes='G01 服装见图5')
    env.codex.script=[correct]
    env.drain(job['runId'])
    revised=env.art(eid,'prompts')['versions'][-1]
    assert revised['content']==dict(base['content'],notes='G01 服装见图5\nG02 服装见图6')
    with pytest.raises(Rejected,match='逐段核对'):
        wb.submit_review(revised['id'],'accidental-whole-review')

    # Simulate an old system merge that undid exactly the notes field (E23).
    from workbench.db import transaction
    with transaction(wb.db_path) as db:
        stale=wb._new_version(db,job['artifactId'],revised['id'],'manual',dict(revised['content'],notes=base['content']['notes']))
        db.execute("INSERT INTO review VALUES('legacy',?,?,?,1,'prompts','revise','session','reader','[]',0,'',NULL)",
                   (stale,initial['runId'],'snapshot'))
        loc=json.dumps(dict(anchor='notes 中第一段',objectId='G01'))
        db.execute("INSERT INTO issue VALUES('note','legacy',1,?,'reference',NULL,NULL,'revise','备注图号',1)",(loc,))
        cursor=json.loads(db.execute('SELECT cursor FROM continuous_run WHERE id=?',(cid,)).fetchone()[0])
        cursor.update(stage='prompts',promptCandidate=stale,ownedVersions=[stale],ownedRuns=[job['runId']],pendingUserChange=False)
        db.execute("UPDATE continuous_run SET state='failed',cursor=? WHERE id=?",(json.dumps(cursor),cid))
        cursor['userStamp']=wb._continuous_user_stamp(db,eid,cursor['ownedRuns'])
        db.execute("UPDATE continuous_run SET cursor=? WHERE id=?",(json.dumps(cursor),cid))
    assert wb.restart_continuous_runs()==[cid]
    assert wb._continuous_record(cid)['cursor']['promptCandidate']==revised['id']
    assert wb._resume_prompt_candidate(cid,revised['id'])==revised['id']
    assert wb.restart_continuous_runs()==[]


def test_legacy_unlabelled_notes_use_an_exact_replacement(tmp_path):
    env,case,_,vid=finishing_fixture(tmp_path)
    wb=env.wb
    art=env.art(case['eid'],'prompts')
    base=wb.save_manual(art['id'],vid,{'notes':'开场杯子见图6。第二段保留木门。'},'old-note')['versionId']
    job=wb.create_task(case['eid'],'prompts','开场杯子应用图5',base_version_id=base,prompt_segments=['G01'],
                       prompt_notes_only=True,prompt_images=False,executor={'create':{'provider':'codex'}})
    env.codex.script=[lambda req:draft(req,notes=json.dumps({'before':'开场杯子见图6','after':'开场杯子见图5'}))]
    env.drain(job['runId'])
    revised=env.art(case['eid'],'prompts')['versions'][-1]
    assert revised['content']==dict(wb._continuous_version(base),notes='开场杯子见图5。第二段保留木门。')
