from hashlib import sha256

import pytest

from helpers import Env
from workbench.errors import Conflict, NotFound, Rejected
from workbench.custommethods import MAX_BYTES


def test_uploaded_revision_pins_and_run_readback(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, old = env.new_episode()
    first = b"\xef\xbb\xbf---\r\nname: door\r\n---\r\n# Door\r\nKnock twice.\r\n"
    second = first.replace(b"twice", b"once")
    a = wb.import_custom_method(pid, "SKILL.md", first, "敲门")
    one = a["versionId"]
    wb.set_custom_methods(pid, "story", [one], [])
    _, pinned = env.new_episode(code="E02", project_id=pid)
    b = wb.import_custom_method(pid, "SKILL.md", second, method_id=a["methodId"], expected_revision=one)
    two = b["versionId"]
    assert wb.import_custom_method(pid,"SKILL.md",second,method_id=a["methodId"],expected_revision=one)==b
    wb.set_custom_methods(pid,"story",[two],[one])
    _, fresh = env.new_episode(code="E03", project_id=pid)
    assert wb.custom_methods(pid,"story",old)["selected"]==[]
    assert wb.custom_methods(pid,"story",pinned)["selected"]==[one]
    assert wb.custom_methods(pid,"script",fresh)["selected"]==[]
    assert wb.custom_methods(pid,"story",fresh)["selected"]==[two]
    def inspect(run):
        return [r for r in wb.run_methods(run)["sections"] if (r["source"] or {}).get("methodId")]
    run = wb.create_task(pinned,"story","写故事")
    env.drain(run["runId"])
    part, = inspect(run["runId"])
    assert part["sha256"]==sha256(first).hexdigest() and part["text"].encode()==first
    assert part["source"]["versionId"]==one and part["source"]["path"]=="SKILL.md"
    assert "Knock twice." in env.codex.calls[0].prompt
    version = env.art(pinned,"story")["versions"][-1]
    review = wb.submit_review(version["id"],"custom-skill-review")["runId"]
    env.drain(review)
    assert not inspect(review) and "Knock twice." not in env.claude.calls[-1].prompt
    wb.set_custom_methods(pid,"story",[two],[one],episode_id=pinned)
    later = wb.create_task(pinned,"story","再写一个版本")["runId"]
    assert inspect(later)[0]["text"].encode()==second and inspect(run["runId"])==[part]
    wb.set_custom_methods(pid,"story",[],[two],episode_id=pinned)
    assert not inspect(wb.create_task(pinned,"story","停用后写")["runId"])
    assert wb.custom_method_text(pid,two,one)["difference"].count("Knock")==2


def test_upload_scope_conflicts_and_rejected_files(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    pid,eid=env.new_episode()
    other,_=env.new_episode(name="其他项目")
    for filename,data in (("SKILL.md",b"\xef\xff"),("../SKILL.md",b"data"),("run.py",b"data"),
                          ("SKILL.md",b" \t"),("SKILL.md",b"a"*(MAX_BYTES+1))):
        with pytest.raises(Rejected):
            wb.import_custom_method(pid,filename,data,"测试")
    a=wb.import_custom_method(pid,"SKILL.md",b"version one","测试")
    b=wb.import_custom_method(pid,"SKILL.md",b"version two",method_id=a["methodId"],expected_revision=a["versionId"])
    with pytest.raises(Conflict):
        wb.import_custom_method(pid,"SKILL.md",b"stale",method_id=a["methodId"],expected_revision=a["versionId"])
    with pytest.raises(NotFound):
        wb.custom_method_text(other,a["versionId"])
    with pytest.raises(NotFound):
        wb.set_custom_methods(other,"story",[a["versionId"]],[])
    with pytest.raises(Rejected):
        wb.set_custom_methods(pid,"story",[a["versionId"],b["versionId"]],[])
    with pytest.raises(Rejected):
        wb.set_custom_methods(pid,"cold_read",[a["versionId"]],[])
    wb.set_custom_methods(pid,"story",[a["versionId"]],[],eid)
    with pytest.raises(Conflict):
        wb.set_custom_methods(pid,"story",[b["versionId"]],[],eid)
    wb.store.blob_path(sha256(b"version one").hexdigest()).write_bytes(b"changed in test")
    with pytest.raises(Rejected,match="核对上传技能"):
        wb.create_task(eid,"story","起草")


def test_each_writing_stage_uses_its_own_selection_and_retry_keeps_bytes(tmp_path):
    from workbench.custommethods import STAGES
    from workbench.executors.fake import quota_exhausted

    env = Env(tmp_path)
    wb = env.wb
    project = env.full_episode(segments={"G01":[8000]},deliver=False)
    pid, eid = project["pid"], project["eid"]
    for stage in STAGES:
        data = ("自定义方法专用测试：" + stage).encode()
        method = wb.import_custom_method(pid,"SKILL.md",data,stage)
        vid = method["versionId"]
        wb.set_custom_methods(pid,stage,[vid],[],episode_id=eid)
        task = wb.create_task(eid,stage,"测试各阶段实际使用的自定义技能")
        env.codex.script = [quota_exhausted]
        env.drain(task["runId"])
        snap = wb.run_methods(task["runId"])
        chosen = [s for s in snap["sections"] if (s["source"] or {}).get("versionId")]
        assert len(chosen)==1 and chosen[0]["text"].encode()==data
        assert chosen[0]["source"]["versionId"]==vid
        wb.set_custom_methods(pid,stage,[],[vid],episode_id=eid)
        replay = wb.retry_run(task["runId"],command_id="again-"+stage)["runId"]
        assert wb.run_methods(replay)["sections"]==snap["sections"]
