"""Serve an isolated local workbench for the browser workflow audit. All providers are simulated."""
import os
import sys
import tempfile
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "server/tests")]
from helpers import Env
from workbench.app import create_app

if __name__ == "__main__":
    folder = Path(tempfile.mkdtemp(prefix="adw-workflows-"))
    if os.environ.get("ADW_TEST_INTERRUPTION"):
        from interrupted_history import prepare
        prepare(folder/"data")
    env = Env(folder)
    if os.environ.get("ADW_TEST_INTERRUPTION"):
        # Historical receipt inspection must not dispatch unrelated queued jobs from the source snapshot.
        for rid in env.wb.pending_runs():
            env.wb.stop_run(rid)
    elif os.environ.get("ADW_TEST_TOPIC_RESEARCH"):
        from topic_research_fixture import prepare
        prepare(env)
    elif os.environ.get("ADW_TEST_STORY_SKILL"):
        env.wb.create_project("puggy", "PUGGY 故事技能验收")
    elif os.environ.get("ADW_TEST_METHOD_SYNC"):
        import json
        from method_sync import prepare
        from workbench import methodpack
        fixture = env.wb.store.root.parent
        methodpack.METHODS, impact = prepare(fixture)
        broken = methodpack.METHODS/"short-film/v9"
        broken.mkdir()
        (broken/"manifest.json").write_text("invalid test manifest")
        pid, eid = env.new_episode(name="方法同步验收",code="旧集")
        env.adopt_story(eid)
        first = env.wb.create_task(eid,"script","切换前的原版任务")["runId"]
        env.drain(first)
        (fixture/"method-test.json").write_text(json.dumps(dict(impact=impact,first=first,pid=pid,eid=eid)))
    elif os.environ.get("ADW_TEST_LAYOUT_CONTROLS"):
        pid,eid=env.new_episode(name="站台灯光与人物轮廓")
        env.image(pid,eid,"侧光参考")
    elif os.environ.get("ADW_TEST_STAGE_CHECKLIST"):
        pid,eid=env.new_episode(name="车站检查清单")
        wb=env.wb
        wb.save_step_config(eid,"story",enhancements=["00-A"])
        env.adopt_story(eid)
        wb.save_step_config(eid,"story",enhancements=["08-C"],expected_revision=1)
        env.script(eid)
        plan=env.plan86({"G01":[5000],"G02":[6000]})
        env.save_adopt(eid,"plan",plan)
        image=env.image(pid,eid)["versionId"]
        vid,_=env.save_adopt(eid,"prompts",env.prompts_for(plan,image))
        env.review_segments(vid,["G01"])
        wb.set_optional_stage(eid,"space",True,"本集使用已有站台布局")
        deliver=wb.deliver(eid,"trial","trial-station",segments=["G01"])
        wb.package(deliver["deliveryId"],["G01"],"zip-station")
    elif os.environ.get("ADW_TEST_ACTIVITY_LOG"):
        pid,eid=env.new_episode(name="回执核对")
        from workbench.images import ImageOutcome
        provider=env.images
        submit=provider.submit
        provider.script=["ok","insufficient","timeout"]
        def reply(request):
            result=submit(request)
            if result.state=="unknown":
                return ImageOutcome("unknown",error="timeout: Authorization: Bearer FAKE_LOG_TOKEN_12345678 /Users/PRIVATE_LOG_NAME")
            return result
        provider.submit=reply
        batch=env.wb.create_batch(eid,provider.name,"generate",[dict(prompt="画信号灯"+str(i)) for i in range(3)],
                                  "64x64",budget_cents=100)
        env.wb.authorize_batch(batch["batchId"],batch["summaryHash"],"approve")
        env.wb.execute_batch(batch["batchId"])
        task=env.wb.create_task(eid,"story","写开场")
        env.drain(task["runId"])
        turn=env.wb.send_free_chat(pid,"检查场景连续","codex",command_id="log-chat")
        env.wb.execute_free_chat(turn["turnId"],turn["attemptId"])
    elif os.environ.get("ADW_TEST_LIBRARY_IMAGES"):
        pid,eid = env.new_episode(name="资产版本核对")
        for i in range(5):
            image = env.image(pid,eid,"站台参考"+str(i+1),"scene",rgb=(50,10+i,60))
            env.wb.submit_reuse(image["versionId"],"站台空间")
    elif os.environ.get("ADW_TEST_EMPTY_PROJECT"):
        env.wb.create_project("_template","新建空项目")
    elif os.environ.get("ADW_TEST_GRID_CELLS"):
        from workbench.images import png_bytes
        pid,eid = env.new_episode(name="站台九格")
        env.adopt_story(eid)
        env.script(eid)
        env.save_adopt(eid,"plan",env.plan86({"G01":[3000,5000]},{"G01":"grid"}))
        env.wb.register_asset(pid,"站台九格",png_bytes(90,90),"grid",episode_id=eid,grid=True,
                              cells=[dict(cell=3,moment="列车进站")])
    elif os.environ.get("ADW_TEST_SCRIPT_METHODS"):
        for profile in ["puggy","_template"]:
            pid,eid = env.new_episode(config=profile,name=profile,code="九格")
            env.adopt_story(eid)
            env.script(eid)
            env.save_adopt(eid,"plan",env.plan86({"G01":[8000]},{"G01":"grid"}))
            env.image(pid,eid,"开场九格","grid",grid=True)
    elif os.environ.get("ADW_TEST_BASELINES"):
        pid,eid = env.new_episode(config="puggy",name="制作基准核对",code="J33")
        env.adopt_story(eid)
        env.script(eid)
        plan = env.plan86({"G01":[8000],"G02":[8000]})
        env.save_adopt(eid,"plan",plan)
        img = env.image(pid,eid)
        prompts = env.prompts_for(plan,img["versionId"])
        block = next(b for d in env.wb.project_baselines(pid) for b in d["blocks"] if b["key"]=="blocks.grid_reference")
        prompts["segments"]["G02"]["txt"] += block["text"]
        env.save_adopt(eid,"prompts",prompts)
        env.codex.script = [lambda req: {"reply":"核对九宫格各格的动作方向，再按本段时窗安排镜头。"}]
    elif os.environ.get("ADW_TEST_DELIVERY_PAGE"):
        episode = env.full_episode(segments={"G01":[3000,5000],"G02":[7000]}, deliver=False)
        pid, eid = episode["pid"], episode["eid"]
        env.image(pid,eid,name="未采用的柜台",category="scene",rgb=(100,40,20))
        prompts = env.prompts_for(episode["plan"], episode["img"]["versionId"])
        prompts["segments"]["G01"]["txt"] = "\n00:00.000-00:08.000 图片1 里的阿福安静值班。\n他抬头看向门边。"
        env.wb.set_role_baseline(eid,"阿福",episode["img"]["versionId"],"阿福安静值班","role-base")
        env.wb.set_cast(eid,"G01","阿福","ok",episode["img"]["versionId"])
        env.wb.set_cast(eid,"G02","阿福","absent")
        pv, _ = env.save_adopt(eid,"prompts",prompts,"delivery-multiline")
        env.review_segments(pv,["G01","G02"])
        env.wb.deliver(eid,"full",eid+"-delivery-page")
    elif os.environ.get("ADW_TEST_STORY_COMPARE"):
        from workbench.executors.fake import draft
        plan = {"create":{"provider":"claude","model":"fixture-author","effort":"low"},
                "review":{"provider":"codex","model":"fixture-reviewer","effort":"high"}}
        seed = env.full_episode(segments={"G01":[8000]},plan=plan)
        env.claude.script = [lambda req: draft(req,synopsis="阿福循着湿脚印找回门后的雨伞。故事结束。",handoff="近景看湿脚印。")]
        env.codex.script = [lambda req: draft(req,synopsis="阿福推开门，雨伞滑到他的脚边。故事结束。",handoff="用门缝的光交代伞的位置。")]
    elif os.environ.get("ADW_TEST_PUBLISH_HANDOFF"):
        seed=env.full_episode(segments={"G01":[8000],"G02":[7000]})
        eid=seed["eid"]
        env.wb.save_step_config(eid,"plan",keep="保留抽屉弹回时的停顿。")
        env.wb.create_group(seed["pid"],"夜班交接",[dict(episodeId=eid,chapter="关门",ending="收好钥匙")],
                            shared_limits="收银台位置沿用本集设定")
        env.wb.mark_image(eid,seed["img"]["versionId"],"required",["S01"],
                          requirement=dict(targetSegments=["G01"],reason="看清角色耳缘",fitness="limited",limitations="只用正面"))
        env.review_segments(seed["prompts"],["G01","G02"],"with-required-image")
    elif os.environ.get("ADW_TEST_H3_SOUND"):
        from h3_sound_fixture import prepare
        seed = prepare(env)
    elif os.environ.get("ADW_TEST_SIGNALS"):
        from prompt_signals_fixture import prepare
        seed = prepare(env)
    elif os.environ.get("ADW_TEST_CONTACT"):
        from contact_fixture import prepare
        seed = prepare(env)
    elif os.environ.get("ADW_TEST_HISTORY"):
        from workbench.executors.fake import review
        sample = env.full_episode(segments={"G01":[8000]})
        pid = sample["pid"]
        _, older = env.new_episode(project_id=pid, code="E02")
        env.drain(env.wb.create_task(older, "story", "另一集专属的夜班记录")["runId"])
        _, eid = env.new_episode(project_id=pid, code="E03", plan={"create":{"provider":"claude"},"review":{"provider":"codex"}})
        first = env.adopt_story(eid)
        env.drain(env.wb.create_task(eid, "script", "将抽屉关上的动作放在第二场")["runId"])
        env.drain(env.wb.create_task(eid, "story", "改写本集开场动作")["runId"])
        second = env.art(eid,"story")["versions"][-1]["id"]
        env.codex.script = [lambda req: review(req,"return",[{"reason":"缺角色反应"}])]
        rejected = env.wb.submit_review(second, "returned")["runId"]
        env.drain(rejected)
        with env.db() as db:
            db.execute("UPDATE message SET text=? WHERE run_id=? AND kind='review'", ("诊断包含 pass，本次有意见", rejected))
            db.execute("UPDATE message SET kind='text',text=? WHERE run_id IN (SELECT run_id FROM review WHERE version_id=?) AND kind='review'",
                       ("独立审查（会话隔离）结论：通过", first))
        env.wb.create_group(pid,"连续夜班",[{"episodeId":eid,"chapter":"先交班"},{"episodeId":older,"chapter":"再巡店"}])
    elif os.environ.get("ADW_TEST_SHOT_WALL"):
        from helpers import png_bytes
        pid, eid = env.new_episode()
        env.wb.set_publish_settings(pid, {"additional_fields":[]})
        env.adopt_story(eid)
        env.script(eid)
        plan = env.plan86({"G01":[3000,5000], "G02":[8000]})
        first = env.image(pid, eid, name="值班员")
        new = env.wb.register_asset(pid, "值班员", png_bytes(48,80,(2,70,90)), "identity",
                                    episode_id=eid, asset_id=first["assetId"])["versionId"]
        plan["shots"][0]["startAssetId"] = new
        env.save_adopt(eid,"plan",plan)
        env.wb.set_cast(eid,"G01","值班员","ok",new)
        env.wb.set_cast(eid,"G02","值班员","absent")
        prompts = env.prompts_for(plan, new)
        prompts["segments"]["G01"]["refs"][0]["shots"] = ["S02"]
        pv, _ = env.save_adopt(eid,"prompts",prompts)
        env.review_segments(pv,["G01","G02"])
        env.save_adopt(eid,"publish",dict(title="Night Shift",body="The night shift begins.",tags=["night"]))
        delivery = env.wb.deliver(eid,"full","wall-delivery")
        packed = env.wb.package(delivery["deliveryId"],["G01","G02"],"wall-package")
        assert len(packed["project"]["assets"]) == 1 and packed["project"]["assets"][0]["sha256"] == env.wb.asset_version(new)["sha256"]
    elif os.environ.get("ADW_TEST_SHOT_REFS"):
        seed = env.full_episode(segments={"G01":[3000,3000,3000]})
        image = seed["img"]["versionId"]
        env.wb.set_role_baseline(seed["eid"],"阿福",image,"阿福","register")
        env.wb.set_cast(seed["eid"],"G01","阿福","ok",image)
    else:
        seed = env.full_episode(segments={"G01": [8000]})
    if os.environ.get("ADW_TEST_PROJECT_CONTEXT"):
        second_pid, second_eid = env.new_episode(name="码头项目")
        for pid,eid,usage,color in [(seed["pid"],seed["eid"],"守夜",(10,20,50)),(second_pid,second_eid,"登船",(20,60,80))]:
            asset = env.image(pid,eid,name="同名道具",category="prop",rgb=color)
            entry = env.wb.submit_reuse(asset["versionId"],usage)
            if usage=="守夜":
                env.wb.review_library(entry["entryId"],"approved")
            env.wb.add_topic(pid,"卡01",usage+"时发现雨伞")
        env.wb.create_batch(seed["eid"],"fake-image","generate",
            [dict(prompt="门牌号",target=dict(name="门牌视图",category="scene"))],"64x64")
    if os.environ.get("ADW_TEST_CAST"):
        from manuscript_samples import pdf_sample, word_sample
        fixture = env.wb.store.root.parent
        (fixture / "站台分镜.docx").write_bytes(word_sample())
        (fixture / "umbrella.pdf").write_bytes(pdf_sample(["Platform", "Umbrella"]))
        (fixture / "scan.pdf").write_bytes(pdf_sample([None]))
        pid, eid = env.new_episode(project_id=seed["pid"],code="角色验证")
        env.adopt_story(eid)
        env.script(eid)
        env.save_adopt(eid,"plan",env.plan86({"G01":[8000]},{"G01":"grid"}))
        env.image(pid,eid,"值班员身份图","identity",rgb=(65,92,97))
        env.image(pid,eid,"G01 开场九宫格","grid",grid=True,rgb=(130,150,170))
    print("Workflow fixture", env.wb.store.root, flush=True)
    uvicorn.run(create_app(env.wb, inline_worker=True), host="127.0.0.1", port=int(os.environ.get("ADW_TEST_PORT", "18768")), log_level="warning")
