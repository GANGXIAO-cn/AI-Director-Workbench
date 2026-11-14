import json
from pathlib import Path

from helpers import Env
from workbench import acting, stages


def line(text="Wait right here", mode="words", **values):
    return dict(speaker="站务员", text=text, direction="压低声音", mode=mode, rate=120, **values)


def test_line_timing_windows_and_invalid_inputs(tmp_path):
    env = Env(tmp_path)
    plan = env.plan86({"G01":[2000, 3000], "G02":[8000]})
    shots = plan["shots"]
    shots[0]["dialogueCues"] = [line(offset_ms=1000, pause_ms=500, throughShot="S02")]
    shots[1]["dialogueCues"] = [line("等一下。", "reading", read_ms=950)]
    shots[2]["dialogueCues"] = [line("请稍等，马上来。", "characters", pause_ms=250)]
    data = acting.report(plan)
    assert not data["errors"] and data["warnings"] == ["S02 第1句 · 与同一说话者的另一句发声重叠"]
    a,b,c = data["rows"]
    assert (a["count"], a["start_ms"], a["available_ms"], a["speech_ms"], a["margin_ms"]) == (3,1000,4000,1500,2000)
    assert b["count"] is None and b["speech_ms"] == 950
    assert (c["count"],c["speech_ms"],c["margin_ms"]) == (6,3000,4750)
    for overrides, msg in [({"rate":False},"安全整数"), ({"pause_ms":-1},"安全整数"), ({"offset_ms":2000},"起声点"),
                           ({"text":"夜班"},"发音词数"), ({"throughShot":"S03"},"本段"), ({"mode":"reading"},"试读毫秒"),
                           ({"direction":[]},"语气"), ({"text":" "},"台词原文")]:
        shots[0]["dialogueCues"] = [dict(line(), **overrides)]
        assert msg in stages.check_content("plan",plan)[0]
    shots[0]["dialogueCues"] = [line(text="等，wait…", count=2)]
    assert acting.report(plan)["rows"][0]["count"] == 2
    shots[0]["dialogueCues"] = [line("A B C D E F")]
    assert "少 1 秒" in acting.report(plan)["warnings"][0]
    assert not acting.report(plan,{"G02"})["warnings"]


def test_acting_reaches_draft_reviews_and_frozen_delivery(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01":[3000,5000], "G02":[6000]})
    original,_ = env.save_adopt(eid, "plan", plan)
    changed = {"S01":dict(performance="指尖压住票根", dialogueCues=[line(offset_ms=2500, throughShot="S02",pause_ms=250)])}
    preview = wb.preview_acting(original,changed)
    assert not wb.acting_review(original)["rows"] and preview["rows"][0]["margin_ms"] == 3750
    plan["shots"][0].update(changed["S01"])
    revised,_ = env.save_adopt(eid,"plan",plan,"apply-timing")
    assert wb.acting_review(revised) == preview
    env.image(pid,eid)
    content = wb.draft_prompts(eid)
    text = content["segments"]["G01"]["txt"]
    assert "指尖压住票根" in text and "Wait right here" in text and text.count("<scenetrans>") == 2
    assert content["segments"]["G01"]["sound"]["dialogue"] == ["Wait right here"]
    assert not content["segments"]["G02"]["sound"]["dialogue"]
    run = wb.create_task(eid,"prompts","按读秒表写分段提示词")["runId"]
    def entries(run):
        return wb.store.manifest_texts(wb.run_manifest(run)["manifest"])
    assert json.loads(next(e["text"] for e in entries(run) if e["role"] == "dialogue_timing")) == preview
    pv,_ = env.save_adopt(eid,"prompts",content)
    first = content["segments"]["G01"]
    tagged = first['txt'].replace('Wait right here', '<speaker1>Wait</speaker1> <speaker1>right here</speaker1>')
    assert not acting.prompt_errors(plan, {'G01':dict(first, txt=tagged)})
    tagged = first['txt'].replace('Wait right here', '<speaker_1>Wait</speaker_1>，转身后继续说：<speaker_1>right here</speaker_1>')
    assert not acting.prompt_errors(plan, {'G01':dict(first, txt=tagged)})
    shifted = first["txt"].replace("Wait right here","等一下") + " Wait right here"
    assert "起声处缺少采用台词" in acting.prompt_errors(plan, {"G01":dict(first,txt=shifted)})[0]
    assert "全段静音" in acting.prompt_errors(plan, {"G01":dict(first,sound={"silent":True})})[0]
    for seg in ("G01", "G02"):
        for stage in ("cold_read","comparison"):
            run = wb.start_prompt_review(pv,seg,stage,seg+stage)["runId"]
            if stage == "cold_read":
                assert not any(e["role"] in {"dialogue_timing","plan_segment"} for e in entries(run))
            else:
                document = json.loads(next(e["text"] for e in entries(run) if e["role"]=="plan_segment"))
                assert document.get("dialogueTiming") == (preview if seg == "G01" else None)
            env.drain(run)
    result=wb.deliver(eid,"trial","acting-trial",["G01"])
    archived=json.loads((Path(result["path"])/"分段与时长预算.json").read_text())
    assert archived["dialogueTiming"] == preview and archived["planVersion"] == revised
    assert wb.verify_delivery(result["deliveryId"])["ok"]


def test_silent_roles_are_project_bound(tmp_path):
    env = Env(tmp_path)
    for profile, allowed in (("puggy",False),("_template",True)):
        _,eid=env.new_episode(config=profile)
        env.adopt_story(eid)
        env.script(eid)
        plan=env.plan86({"G01":[8000]})
        plan["shots"][0]["dialogueCues"]=[dict(line(),speaker="MILO")]
        v=env.wb.save_manual(env.art(eid,"plan")["id"],None,plan,eid+"new")["versionId"]
        gate=env.wb.adoption_gate(v)
        assert gate["adoptable"] == allowed
        if not allowed:
            assert "无对白角色" in gate["formatErrors"][0]
