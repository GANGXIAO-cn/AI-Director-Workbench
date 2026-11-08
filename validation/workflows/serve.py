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
    elif os.environ.get("ADW_TEST_SHOT_REFS"):
        seed = env.full_episode(segments={"G01":[3000,3000,3000]})
        image = seed["img"]["versionId"]
        env.wb.set_role_baseline(seed["eid"],"阿福",image,"阿福","register")
        env.wb.set_cast(seed["eid"],"G01","阿福","ok",image)
    else:
        seed = env.full_episode(segments={"G01": [8000]})
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
