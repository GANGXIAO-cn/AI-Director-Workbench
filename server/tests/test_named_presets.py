import json
from pathlib import Path
import zipfile

import pytest
from helpers import Env
from workbench.db import connect
from workbench.errors import Conflict, Rejected
from workbench.images import png_bytes
from workbench.specs import frame_geometry
from workbench.storage import sha256_bytes


def test_named_presets_pin_versions_and_scope(tmp_path):
    env = Env(tmp_path/"data")
    wb = env.wb
    pid, old = env.new_episode()
    original = wb.project_presets(pid)["presets"][0]
    pic = "方形短片"
    copy = dict(project_id=pid, values={"aspectRatio":"1:1","h3Candidates":"2"}, copy_as=pic, expected_revision=0, command_id="copy")
    created = wb.update_preset(**copy)
    assert wb.update_preset(**copy) == created
    assert wb.kickoff_preview(project_id=pid)["spec"] == original["values"]
    with pytest.raises(Conflict):
        wb.update_preset(**dict(copy, command_id="duplicate"))
    plan=dict(create={"provider":"codex"}, review={"provider":"claude"})
    args=dict(episode_id=None, source_type="idea",executor_plan=plan, project_id=pid, display_code="E02",
              idea_text="盒子打开",preset_name=pic,preset_revision=1, spec_overrides={"sound":"只有雨声"},command_id="start")
    result=wb.start_episode(**args)
    wb.update_preset(pid,{"aspectRatio":"16:9"},"named-update",name=pic,expected_revision=1)
    assert wb.start_episode(**args)==result
    with pytest.raises(Conflict):
        wb.start_episode(**dict(args,display_code="E03",command_id="stale"))
    with pytest.raises(Conflict):
        wb.update_preset(pid,{"sound":"丢失更新"},"stale-edit",name=pic,expected_revision=1)
    other=wb.create_project("_template","另一项目")["projectId"]
    with pytest.raises(Rejected):
        wb.start_episode(**dict(args,project_id=other,display_code="E03",command_id="foreign"))
    overview=wb.project_presets(pid)
    e1,e2=overview["episodes"]
    assert e1["id"]==old and e1["effective"]==original["values"]
    assert (e2["name"],e2["rev"],e2["base"])==(pic,1,created["preset"])
    assert e2["effective"]==dict(created["preset"],sound="只有雨声")
    assert e2["base"]["aspectRatio"]=="1:1" and overview["presets"][1]["values"]["aspectRatio"]=="16:9"
    with pytest.raises(Rejected):
        wb.update_preset(pid,{"h3Candidates":3},"invalid",name=pic)
    folder=tmp_path/"single-project"
    wb.export_project_folder(pid,str(folder))
    with connect(folder/"workbench.sqlite") as db:
        assert db.execute("SELECT name,rev FROM kickoff_preset WHERE episode_id=?",(result["episodeId"],)).fetchone()["name"]==pic
        assert db.execute("SELECT count(*) FROM preset WHERE project_id=?",(pid,)).fetchone()[0]==2


def test_episode_h3_defaults_are_in_exported_zip(tmp_path):
    env=Env(tmp_path)
    result=env.full_episode(segments={"G01":[8000],"G02":[8000]},deliver=False)
    wb=env.wb
    eid=result["eid"]
    for key,value in {"h3Resolution":"768p","h3InstanceType":"plus","h3Candidates":2}.items():
        wb.episode_spec_change(eid,key,value,"set-"+key,confirm=True)
    delivery=wb.deliver(eid,"full","delivery")
    did=delivery["deliveryId"]
    checks=wb.package_preview(did,["G01","G02"],{"resolution":"480p"},{"G02":{"candidates":4}})
    assert checks["segments"][0]["origins"]["instanceType"]=="本集有效规格"
    assert checks["segments"][0]["origins"]["resolution"]=="本次覆盖"
    assert checks["segments"][1]["origins"]["candidates"]=="本次单段覆盖"
    wb.episode_spec_change(eid,"h3Candidates",1,"after-delivery",confirm=True)
    packed=wb.package(did,["G01","G02"],"zip",overrides={"resolution":"480p"},segment_overrides={"G02":{"candidates":4}})
    with zipfile.ZipFile(packed["path"]) as archive:
        rows=json.loads(archive.read("project.json"))["segments"]
    assert [s["params"]["candidates"] for s in rows]==[2,4]
    assert all(s["params"]["instanceType"]=="plus" and s["params"]["resolution"]=="480p" for s in rows)


def test_grid_header_alone_cannot_prove_dimensions():
    data=png_bytes(90,160)[:33]
    asset=dict(id="board",type="image",category="grid",path="原图/末班车.png",sha256=sha256_bytes(data))
    manifest=dict(assets=[asset],aspect_ratio="9:16",segments=[dict(id="G02",strategy="grid",references=[dict(asset_id="board")])])
    with pytest.raises(ValueError,match=r"G02.*末班车.png.*无法解码"):
        frame_geometry(manifest,lambda _:data)


def test_aspect_override_uses_actual_dependencies_at_confirmation(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    r=env.full_episode(segments={"G01":[8000],"G02":[8000]},deliver=False)
    pid,eid=r["pid"],r["eid"]
    candidate=env.image(pid,eid,name="候选状态",category="state")
    unused=env.image(pid,eid,name="未选九宫格",category="grid",grid=True)
    identity=env.image(pid,eid,name="主角正面",category="identity")
    wb.mark_image(eid,identity["versionId"],"required")
    def preview():
        return wb.episode_spec_change(eid,"aspectRatio","1:1","preview",confirm=False)
    before=preview()
    assert not any(i.get("versionId") in (unused["versionId"],candidate["versionId"]) for i in before["preview"]["impacts"])
    assert "主角正面 v1" in before["preview"]["kept"]
    wb.mark_image(eid,candidate["versionId"],"required")
    with pytest.raises(Conflict,match="重新预览"):
        wb.episode_spec_change(eid,"aspectRatio","1:1","stale",True,before["fingerprint"])
    check=preview()
    assert any(i.get("versionId")==candidate["versionId"] for i in check["preview"]["impacts"])
    assert all(i.get("versionId")!=unused["versionId"] for i in check["preview"]["impacts"])
    assert check["fingerprint"]!=before["fingerprint"]
    args=(eid,"aspectRatio","1:1","accepted",True,check["fingerprint"])
    assert wb.episode_spec_change(*args)==wb.episode_spec_change(*args)
    assert wb.episode_view(eid)["kickoff"]["spec"]["aspectRatio"]=="1:1"
    assert not any(i["target_ref"].startswith("品牌片尾") for i in wb.impacts(eid))
