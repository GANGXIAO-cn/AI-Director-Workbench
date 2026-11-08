"""Create three adopted segment prompts and run each through both review passes."""
from workbench import readable


def prepare(env):
    pid, eid = env.new_episode(name="提示词定位验收")
    env.adopt_story(eid)
    env.script(eid)
    segments = {sid:[8000] for sid in ("G01","G02","G03")}
    env.save_adopt(eid,"plan",env.plan86(segments))
    image = env.image(pid,eid)
    content = env.wb.draft_prompts(eid)
    repeated = "值班员把收银台上的门禁卡推到玻璃门旁边的黄线以内"
    for sid, entry in content["segments"].items():
        parts = entry["blocks"]
        parts["【开场】"] = repeated + "。"
        if sid == "G01":
            parts["【基本设定】"] += "\n" + repeated + "。"
        if sid == "G03":
            parts["【开场】"] += "\n参考编号 " + image["versionId"]
        entry["txt"] = readable.render(parts)
    version, _ = env.save_adopt(eid,"prompts",content)
    env.review_segments(version,segments)
    return dict(eid=eid, pid=pid, version=version, repeated=repeated)
