import pytest

from helpers import Env
from workbench.errors import Rejected


def prepare(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode(config="puggy")
    identity = wb.use_fixed_role(eid, "milo", "use-fixed")["versionId"]
    env.adopt_story(eid)
    env.script(eid)
    items = [
        dict(id="milo",kind="character",title="MILO",roleId="milo",stateId="default",imagePrompt="",
             description="复用项目固定身份图",evidenceStatus="verified"),
        dict(id="platform",kind="scene",title="站台",description="空站台",imagePrompt="空站台全景"),
        dict(id="coat",kind="state",title="雨衣",roleId="milo",stateId="wet",description="湿雨衣",
             imagePrompt="湿雨衣正面",dependsOn=["milo","platform"]),
    ]
    env.save_adopt(eid, "asset_design", dict(strategy="lightweight",assets=items),"adopt-worksheet")
    return env, eid, identity, items


def test_adoption_sync_and_revisions(tmp_path):
    env, eid, identity, items = prepare(tmp_path)
    wb = env.wb
    sheet = wb.asset_worksheet(eid)
    assert not sheet["complete"]
    fixed, scene, variant = sheet["rows"]
    assert scene["decision"] == "generate" and scene["generation_requested"]
    assert (fixed["decision"], fixed["adopted_version_id"]) == ("reuse", identity)
    assert variant["depends_on"] == ["milo","platform"]
    replacement = dict(items[1],id="hall",description="空候车厅")
    env.save_adopt(eid, "asset_design", dict(strategy="premium",assets=[items[0],replacement]), "replace-list")
    updated = wb.asset_worksheet(eid)["rows"]
    assert [r["need_key"] for r in updated] == ["milo","hall"]
    assert updated[0]["id"] == fixed["id"] and updated[0]["adopted_version_id"] == identity
    with env.db() as db:
        assert db.execute("SELECT removed FROM asset_need WHERE id=?", (scene["id"],)).fetchone()[0]
    env.save_adopt(eid, "asset_design", dict(strategy="lightweight",assets=items), "restore-list")
    assert wb.asset_worksheet(eid)["rows"][1]["id"] == scene["id"]
    for dependencies in (["unknown"], ["coat"]):
        invalid = dict(strategy="premium",assets=[*items[:2],dict(items[2],dependsOn=dependencies)])
        art = env.art(eid, "asset_design")
        draft = wb.save_manual(art["id"], art["adopted_version_id"], invalid, "bad-"+dependencies[0])
        with pytest.raises(Rejected,match="依赖"):
            wb.adopt(art["id"], draft["versionId"], art["adopted_version_id"], "adopt-"+dependencies[0])


def test_worksheet_batch_receipt_and_explicit_adoption(tmp_path):
    env, eid, identity, items = prepare(tmp_path)
    wb = env.wb
    def rows():
        return {r["need_key"]:r for r in wb.asset_worksheet(eid)["rows"]}
    def decide(key, action, **values):
        return wb.set_asset_need(eid,key,rows()[key]["revision"],action,action+key,**values)
    def batch(key, command):
        return wb.prepare_asset_batch(eid,{key:rows()[key]["revision"]},"fake-image","64x64",command,budget_cents=100)
    def render(key):
        b = batch(key, "prepare-"+key)
        assert rows()[key]["request"]["batchId"] == b["batchId"]
        wb.authorize_batch(b["batchId"],b["summaryHash"],"authorize-"+key)
        wb.execute_batch(b["batchId"])
        data = wb.batch_view(b["batchId"])
        item, = data["spec"]["items"]
        vid, = rows()[key]["candidates"]
        assert item["target"]["needKey"] == key and rows()[key]["adopted_version_id"] is None
        wb.adopt_asset_needs(eid,{key:dict(versionId=vid,revision=rows()[key]["revision"])},"select-"+key)
        return vid, item
    # W-A2: blank reason uses its default; the real image dependency still blocks.
    with pytest.raises(Rejected,match="需要这张参考图"):
        decide("platform","text_only")
    with pytest.raises(Rejected,match="需要这张参考图"):
        decide("platform","text_only",reason="可写在提示词里")
    decide("coat","generate")
    with pytest.raises(Rejected,match="依赖图尚未采用"):
        batch("coat","too-soon")
    decide("platform","generate")
    decide("platform","edit_prompt",prompt="灰墙站台")
    changed = [items[0],dict(items[1],imagePrompt="清晨站台"),items[2]]
    env.save_adopt(eid,"asset_design",dict(strategy="premium",assets=changed),"change-prompt")
    scene = rows()["platform"]
    assert scene["prompt_changed"] and scene["image_prompt"]=="灰墙站台" and scene["source_prompt"]=="清晨站台"
    decide("platform","keep_prompt")
    platform, item = render("platform")
    assert item["prompt"]=="灰墙站台" and item["inputs"]==item["segments"]==[]
    _, item = render("coat")
    assert item["inputs"] == [identity, platform]
    assert wb.asset_worksheet(eid)["complete"]


def test_scene_scopes_follow_later_shot_associations(tmp_path):
    env,eid,_,_ = prepare(tmp_path)
    wb=env.wb
    wb.add_space_item(eid,"出口","出口在柜台左侧","剧本",["场02"],scope="scenes")
    lock, = wb.space_locks(eid)
    assert lock["sceneIds"]==["场02"] and not lock["segments"] and not wb.blocked_segments(eid)
    env.save_adopt(eid,"plan",env.plan86({"G01":[8000]}),"map-scenes")
    assert wb.space_locks(eid)[0]["segments"]==["G01"]
    assert list(wb.blocked_segments(eid))==["G01"]

def test_identity_reuse_rejects_wrong_category_and_role(tmp_path):
    env, eid, identity, _ = prepare(tmp_path)
    wb = env.wb
    fixed = wb.asset_worksheet(eid)["rows"][0]
    with env.db() as db:
        version = wb.asset_version(identity, db)
    with env.db() as db:
        entry = "wrong-kind"
        db.execute("INSERT INTO library_entry(id,project_id,asset_version_id,usage,status,created_at) VALUES(?,?,?,?,?,?)",
                   (entry,version["project_id"],identity,"测试","approved",0))
        db.execute("UPDATE asset SET category='prop' WHERE id=?", (version["asset_id"],))
    with pytest.raises(Rejected, match="类别"):
        wb.set_asset_need(eid,"milo",fixed["revision"],"reuse","reject-category",entry_id=entry)
    with env.db() as db:
        db.execute("UPDATE asset SET category='identity',roles=? WHERE id=?", ('["staff"]',version["asset_id"]))
    with pytest.raises(Rejected, match="角色编号"):
        wb.set_asset_need(eid,"milo",fixed["revision"],"reuse","reject-role",entry_id=entry)


def test_adopted_identity_automatically_follows_visible_roles(tmp_path):
    env, eid, identity, _ = prepare(tmp_path)
    plan = env.plan86({"G01":[8000], "G02":[8000]})
    for index, shot in enumerate(plan["shots"]):
        shot["visibleRoles"] = ["milo"] if index == 0 else []
        shot["propStates"] = {}
    scene = env.image(env.wb.episode_view(eid)["episode"]["project_id"], eid, "空景", "scene")
    plan["shots"][1]["startAssetId"] = scene["versionId"]
    env.save_adopt(eid, "plan", plan, "plan-with-roles")
    matrix = env.wb.cast_matrix(eid)
    assert matrix["roster"]["milo"]["assetVersionId"] == identity
    assert matrix["rows"][0]["cells"]["milo"]["assetVersionId"] == identity
    assert matrix["rows"][0]["cells"]["milo"]["status"] == "ok"
    assert matrix["rows"][1]["cells"]["milo"]["status"] == "absent"
    draft = env.wb.draft_prompts(eid)
    assert identity in [ref["assetVersionId"] for ref in draft["segments"]["G01"]["refs"]]
    assert identity not in [ref["assetVersionId"] for ref in draft["segments"]["G02"]["refs"]]


def test_choose_product_master_replaces_worksheet_image_without_generation(tmp_path):
    env, eid, _, _ = prepare(tmp_path)
    wb = env.wb
    item = dict(id="bag", kind="prop", title="托特包", description="入景商品", imagePrompt="木屋里的托特包")
    env.save_adopt(eid, "asset_design", dict(strategy="lightweight", assets=[item]), "bag-list")
    for code in ("15", "03"):
        current, = wb.asset_worksheet(eid)["rows"]
        wb.set_asset_need(eid, "bag", current["revision"], "reuse", "pick-"+code, product_code=code)
        master = wb.use_project_product(eid, code, "lookup-"+code)["versionId"]
        updated, = wb.asset_worksheet(eid)["rows"]
        assert updated["adopted_version_id"] == master and master in updated["candidates"]
        assert updated["decision"] == "reuse" and not updated["generation_requested"]
    with env.db() as db:
        assert not db.execute("SELECT 1 FROM image_batch WHERE episode_id=?", (eid,)).fetchone()
