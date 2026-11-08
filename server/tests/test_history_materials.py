import json

from helpers import Env
from workbench.db import transaction
from workbench.images import png_bytes
from workbench.storage import sha256_bytes


def test_history_materials_are_project_scoped_versioned_and_replay_without_sources(tmp_path):
    env = Env(tmp_path/"workbench")
    wb = env.wb
    pid = wb.create_project("_template")["projectId"]
    project = tmp_path/"original"
    episode = project/"episodes"/"E01"
    episode.mkdir(parents=True)
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",(json.dumps({"assetRoot":str(project)}),pid))
    image = png_bytes(9,16,(55,22,44))
    (episode/"image.png").write_bytes(image)
    (episode/"正文.txt").write_text("样本正文")
    (episode/"决定.md").write_text("保留此格")
    (episode/"evidence.json").write_text(json.dumps(dict(schema_version=1,episode_id="E01",
        asset_reviews={"actor":dict(status="passed",scope="看过原图",evidence_path="决定.md")},
        segment_reviews={"G01":dict(status="passed",scope="读过文本",evidence_path="决定.md")},
        issues=[dict(id="Q",severity="critical",status="accepted",description="保留画面",segments=["G01"],
                     evidence_path="决定.md",user_direction=dict(stage="closing_manual_revision",instruction="保留此格",evidence_path="决定.md"))])))
    external = project/"other.md"
    external.write_text("这份文件没有登记")
    (episode/"不应复制").symlink_to(external)
    data = dict(schema_version=2,asset_snapshot="不应复制",english_title="Test",total_duration_s=8,episode_id="E01",
        assets=[dict(id="actor",path="image.png",type="image",category="scene",purpose="空景",source="项目",
                    version="v1",usage="首镜",origin="new",review_status="passed",sha256=sha256_bytes(image))],
        segments=[dict(id="G01",duration_s=8,start_s=0,reference_strategy="flex",prompt="正文.txt",
                       references=[dict(asset_id="actor",label="图片1",purpose="空间",exclusions="文字",upload_order=1,shots=["S01"])])],
        prompt_review=dict(requirements="正文.txt",story="正文.txt",plan="正文.txt"),review_evidence="evidence.json")
    (episode/"episode.json").write_text(json.dumps(data))
    first = wb.import_historical_episode(pid,"E01")
    assert first["materialChecks"]["reviews"]["details"]["user_directed_issues"]==["Q"]
    assert first["materialChecks"]["assets"]["state"]=="passed"
    assert first["checks"]["manifest"]["rule"]=="legacy_snapshot"
    assert first["archive"]["unavailable"]["不应复制"]=="路径越出本集"
    assert not wb.store.blob_path(sha256_bytes(external.read_bytes())).exists()
    assert wb.import_historical_episode(pid,"E01")["receipt"]==first["receipt"]
    (episode/"决定.md").write_text("新版本已变更")
    second = wb.import_historical_episode(pid,"E01")
    assert second["receipt"] != first["receipt"]
    assert second["materialChecks"]["reviews"]["state"]=="failed"
    project.rename(tmp_path/"offline")
    offline = wb.historical_episodes(pid)[0]
    assert not offline["available"] and [r["id"] for r in offline["versions"]]==[second["receipt"],first["receipt"]]
    packet = wb.historical_review_inputs(pid,first["receipt"])["record"]
    assert "结论：待审" in packet and sha256_bytes(image) in packet
    assert sha256_bytes("样本正文".encode()) in packet and "结论：通过" not in packet
    replay = wb.historical_receipt(pid,first["receipt"])
    assert replay["materialRechecked"]==first["materialChecks"] and replay["integrity"]["ok"]
    wb.store.blob_path(replay["files"]["正文.txt"]).write_bytes(b"corrupt test copy")
    damaged = wb.historical_receipt(pid,first["receipt"])
    assert not damaged["integrity"]["ok"]
    assert damaged["materialChecks"]["reviews"]["state"]=="passed"
    assert damaged["materialRechecked"]["reviews"]["state"]=="incomplete"


def test_grid_requires_registered_png_in_episode_ratio(tmp_path):
    import io
    import pytest
    from PIL import Image
    from workbench.errors import Rejected
    env=Env(tmp_path)
    pid,eid=env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan=env.plan86({"G01":[8000]}, {"G01":"grid"})
    env.save_adopt(eid,"plan",plan)
    for name, fmt, size in [("画幅错误","PNG",(90,90)),("JPEG九格","JPEG",(90,160)),("正式九格","PNG",(90,160))]:
        out=io.BytesIO()
        Image.new("RGB",size,"green").save(out,fmt)
        asset=env.wb.register_asset(pid,name,out.getvalue(),"grid",episode_id=eid,grid=True)["versionId"]
        content=env.prompts_for(plan,asset)
        content["segments"]["G01"]["refs"][0]["grid"]=True
        if name=="正式九格":
            entry = content["segments"]["G01"]
            entry["generation_settings"] = {"aspect_ratio":"16:9"}
            with pytest.raises(Rejected,match="生成设置画幅"):
                env.save_adopt(eid,"prompts",content,"bad-generation-ratio")
            entry["generation_settings"] = {"aspect_ratio":"90:160"}
            env.save_adopt(eid,"prompts",content,name)
        else:
            with pytest.raises(Rejected,match="画幅一致的PNG"):
                env.save_adopt(eid,"prompts",content,name)


def test_delivery_recomputes_grid_dimensions_from_selected_files(tmp_path):
    import pytest
    import zipfile
    from pathlib import Path
    from workbench import deliverypage
    from workbench.delivery import package_geometry, DEFAULT_PACKAGER
    env = Env(tmp_path)
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01":[8000],"G02":[6000]}, {"G01":"grid"})
    env.save_adopt(eid,"plan",plan)
    grid = env.image(pid,eid,"首段分镜",category="grid",grid=True,size=(90,160))["versionId"]
    ref = env.image(pid,eid,"方形身份",size=(100,100))["versionId"]
    prompts = env.prompts_for(plan,{"G01":grid,"G02":ref})
    prompts["segments"]["G01"]["refs"][0]["grid"] = True
    prompts["segments"]["G01"]["generation_settings"] = {"aspect_ratio":"90:160"}
    vid, _ = env.save_adopt(eid,"prompts",prompts)
    env.review_segments(vid,["G01","G02"])
    for sid in ["G01","G02"]:
        d = env.wb.deliver(eid,"trial","trial-"+sid,segments=[sid])
        root = Path(d["path"])
        manifest = json.loads((root/"episode.json").read_bytes())
        geometry = manifest["workbench"]["frameGeometry"]
        assert geometry == dict(target="9:16",segments=1, grids={"G01":dict(asset=grid,width=90,height=160)} if sid=="G01" else {})
        assert ("90 × 160 像素" in (root/"00_图文对照.html").read_text()) == (sid=="G01")
        assert env.wb.verify_delivery(d["deliveryId"])["ok"]
        if DEFAULT_PACKAGER.is_file():
            out = env.wb.package(d["deliveryId"],[sid],"ratio-zip-"+sid)
            with zipfile.ZipFile(out["path"]) as package:
                segment = json.loads(package.read("project.json"))["segments"][0]
            assert segment["id"] == sid and segment["params"]["ratio"] == "9:16"
        if sid=="G01":
            files = json.loads((root/"manifest.json").read_bytes())["files"]
            page = root/"00_图文对照.html"
            page.write_text(page.read_text().replace("90 × 160 像素","160 × 90 像素"))
            with pytest.raises(ValueError, match="页面九宫格原图尺寸不符"):
                deliverypage.verify(root, manifest, files)
            manifest["segments"][0]["generation_settings"]["aspect_ratio"] = "9:16"
            manifest["aspect_ratio"] = "16:9"
            with pytest.raises(ValueError,match="生成设置画幅"):
                package_geometry(root, manifest, files)
            manifest["segments"][0]["generation_settings"]["aspect_ratio"] = "16:9"
            with pytest.raises(ValueError,match="九宫格"):
                package_geometry(root, manifest, files)


def test_scoped_image_inventory_archives_parent_inputs_and_survives_replacement(tmp_path):
    import pytest
    from workbench.service import Conflict
    env = Env(tmp_path/"app")
    wb = env.wb
    pid = wb.create_project("_template")["projectId"]
    project = tmp_path/"project"
    folder = project/"episodes/E"
    child, evidence = folder/"独立集", folder/"过程稿/对标"
    child.mkdir(parents=True)
    evidence.mkdir(parents=True)
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",(json.dumps({"assetRoot":str(project)}),pid))
    def image(path, tone):
        data = png_bytes(9,16,(tone,22,44))
        path.write_bytes(data)
        return sha256_bytes(data)
    parent = image(folder/"空景.png",10)
    shared = image(child/"同框.png",20)
    ignored = image(child/"另一个结局.png",30)
    selected = image(evidence/"指定参考.png",40)
    evidence_only = image(evidence/"拆镜.png",50)
    (folder/"空景副本.png").write_bytes((folder/"空景.png").read_bytes())
    (child/"episode.json").write_text(json.dumps(dict(schema_version=2,episode_id="sub",assets=[],segments=[])))
    data = dict(schema_version=2,episode_id="E",assets=[
        dict(id="take",type="image",path="独立集/同框.png",sha256=shared)],
        segments=[dict(id="G01",references=[dict(asset_id="take")])])
    (folder/"episode.json").write_text(json.dumps(data))
    (folder/"过程稿/图片选择.json").write_text(json.dumps(dict(schema_version=1,episode_root=str(folder),images=[
        dict(id="I123",sha256=selected,paths=["过程稿/对标/指定参考.png"],priority="required",
             selected_by="user",selection_reason="为收尾格保留原图",target_segments=["G01"])])))
    before = wb.import_historical_episode(pid,"E","registered")
    assert "imageInventory" not in before["materialChecks"] and "空景.png" not in before["files"]
    with pytest.raises(Conflict):
        wb.import_historical_episode(pid,"E","registered",True)
    first = wb.import_historical_episode(pid,"E","whole",True)
    check = first["materialChecks"]["imageInventory"]
    assert check["state"]=="failed" and check["details"]["excluded_projects"]==["独立集"]
    assert check["details"]["problems"]==["I123：必用图未落实到要求的正式输入段"]
    rows = {r["sha256"]:r for r in check["details"]["images"]}
    assert set(rows)=={parent,shared,selected}
    assert rows[parent]["currentPaths"]==["空景.png","空景副本.png"] and rows[shared]["usedIn"]==["G01"]
    assert not any(wb.store.blob_path(h).exists() for h in [ignored,evidence_only])
    image(evidence/"指定参考.png",80)
    second = wb.import_historical_episode(pid,"E","changed",True)
    changes = second["materialChecks"]["imageInventory"]["details"]
    assert changes["current_images"]==2 and any("必用原版本不存在" in p for p in changes["problems"])
    assert second["receipt"]!=first["receipt"]
    project.rename(tmp_path/"offline")
    assert wb.import_historical_episode(pid,"E","whole",True)==first
    offline = wb.historical_receipt(pid,first["receipt"])
    assert offline["integrity"]["ok"] and offline["materialRechecked"]["imageInventory"]==check
    wb.store.blob_path(parent).write_bytes(b"altered test copy")
    damaged = wb.historical_receipt(pid,first["receipt"])
    assert not damaged["integrity"]["ok"] and damaged["materialRechecked"]["imageInventory"]["state"]=="incomplete"


def test_historical_report_only_freezes_receipted_materials(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    import pytest
    from workbench import referencereports
    from workbench.errors import NotFound, Rejected

    wb = Env(tmp_path/"app").wb
    pid = wb.create_project("_template")["projectId"]
    project = tmp_path/"project"
    episode = project/"episodes/E"
    episode.mkdir(parents=True)
    pack = project/"episodes/other/accepted"
    (pack/"frames").mkdir(parents=True)
    receipt = pack/"参考包验收.json"
    (episode/"episode.json").write_text(json.dumps(dict(schema_version=2,episode_id="E",assets=[],segments=[])))
    state = {"records":{"reference":{"paths":{"receipt":"../other/accepted/参考包验收.json"}}}}
    (episode/"流程状态.json").write_text(json.dumps(state))
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",(json.dumps({"assetRoot":str(project)}),pid))
    md = b"# Test\n\n<script>bad()</script>"
    frame = png_bytes(9,16,(24,56,78))
    files = {"report.md":md, "report.html":b"<script>bad()</script>", "frames/shot.png":frame,
             "frames/changed.png":b"changed", "video.mp4":b"source"}
    for name, data in files.items():
        (pack/name).write_bytes(data)
    hashes = {k:sha256_bytes(v) for k,v in files.items()}
    hashes["frames/changed.png"] = sha256_bytes(b"old")
    receipt.write_text(json.dumps({"files":hashes}))
    imported = wb.import_historical_episode(pid,"E",include_image_inventory=True)
    report = imported["archive"]["referenceReports"][0]
    assert report["missing"] == ["frames/changed.png"]
    assert imported["materialChecks"]["imageInventory"]["details"]["current_images"] == 0
    def fetch(name):
        return wb.historical_report_file(pid,imported["receipt"],0,name)
    pack.rename(pack.with_name("offline"))
    page = fetch("preview")
    assert b"&lt;script&gt;" in page and b"<script>" not in page
    assert fetch("frames/shot.png") == frame
    with ZipFile(BytesIO(fetch("report.zip"))) as zipfile:
        assert set(zipfile.namelist()) == {"report.md","report.html","frames/shot.png"}
        assert all(zipfile.read(name)==files[name] for name in zipfile.namelist())
    with pytest.raises(NotFound):
        fetch("../other/accepted/video.mp4")
    wb.store.blob_path(report["resources"]["frames/shot.png"]).write_bytes(b"corrupt")
    with pytest.raises(Rejected):
        fetch("report.zip")
    assert not wb.historical_receipt(pid,imported["receipt"])["integrity"]["ok"]
    outside = tmp_path/"outside/参考包验收.json"
    state["records"]["reference"]["paths"]["receipt"] = str(outside)
    ref = wb.store.put_blob(json.dumps(state).encode())
    invalid = referencereports.capture(episode,project,wb.store,{"流程状态.json":ref})
    assert invalid[0]["error"] and not invalid[0]["resources"]
