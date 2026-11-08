"""A two-shot episode with a selected scene image, a costume and a registered identity original."""
def prepare(env):
    wb=env.wb
    pid,eid=env.new_episode(name="场景对照验证")
    env.adopt_story(eid)
    env.script(eid)
    state=env.image(pid,eid,"柜台状态","state",rgb=(39,79,108),size=(160,90))
    anchor=env.image(pid,eid,"柜台锁定","scene",rgb=(94,82,62),size=(160,90))
    identity=env.image(pid,eid,"值班员身份","identity",rgb=(168,96,42),size=(90,160))
    costume=env.image(pid,eid,"蓝围裙","costume",rgb=(55,77,124),size=(90,160))
    unused=env.image(pid,eid,"未采用的柜台","scene")
    plan=env.plan86({"G01":[4000,4000]})
    plan["shots"][0]["startAssetId"]=state["versionId"]
    env.save_adopt(eid,"plan",plan)
    draft=env.prompts_for(plan,state["versionId"])
    draft["segments"]["G01"]["refs"][0]["role"]="柜台状态"
    draft["segments"]["G01"]["refs"].append(dict(assetVersionId=costume["versionId"],label="图片2",kind="image",role="围裙款式"))
    env.save_adopt(eid,"prompts",draft)
    wb.set_role_baseline(eid,"afu",identity["versionId"],"值班员","identity")
    wb.set_cast(eid,"G01","afu","ok",identity["versionId"])
    lock=wb.add_space_item(eid,"柜台","背靠北墙","剧本",["G01"],status="confirmed",lock_asset_version_id=anchor["versionId"])
    return dict(pid=pid,eid=eid,state=state,anchor=anchor,identity=identity,costume=costume,unused=unused,lock=lock)
