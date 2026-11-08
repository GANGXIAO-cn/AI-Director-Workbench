"""Create a reviewed three-segment trial delivery with distinct audio and export parameters."""


def prepare(env):
    pid, eid = env.new_episode(name="声音交付验收")
    env.adopt_story(eid)
    env.script(eid)
    segments = {sid:[8000] for sid in ("G01", "G02", "G03")}
    env.save_adopt(eid, "plan", env.plan86(segments))
    env.image(pid, eid)
    content = env.wb.draft_prompts(eid)
    quiet = content["segments"]["G02"]
    quiet["refs"][0]["assetVersionId"] = env.image(pid, eid, name="夜班身份备选")["versionId"]
    quiet["generation_settings"] = dict(audio=True, resolution="768p", instanceType="plus", candidates=2, seed=19)
    music = content["segments"]["G03"]
    prop = env.image(pid, None, name="票价牌", category="prop", rgb=(16, 35, 77))
    library = env.wb.submit_reuse(prop["versionId"], "站台票价牌")["entryId"]
    env.wb.review_library(library, "approved")
    reused = env.wb.copy_to_episode(library, eid)["versionId"]
    music["refs"].append(dict(assetVersionId=reused, label="图片2", kind="image", role="标牌样式"))
    music["txt"] = music["txt"].replace("【参考素材】", "【参考素材】\n图片2：站台票价牌。")
    music["generation_settings"] = dict(audio=False)
    art = env.art(eid, "prompts")
    first = env.wb.save_manual(art["id"], None, content, "first")["versionId"]
    second = env.wb.set_sound(first, "G02", {"silent":True}, "quiet", confirm=True)["versionId"]
    third = env.wb.set_sound(second, "G03", {"bgm":True, "music_description":"节奏舒缓的弦乐"}, "music")["versionId"]
    env.wb.adopt(art["id"], third, None, "adopt", and_continue=True)
    env.review_segments(third, segments)
    delivery = env.wb.deliver(eid, "trial", "delivery", segments=list(segments))
    return dict(pid=pid, eid=eid, delivery=delivery)
