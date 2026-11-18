import json
import sqlite3
from pathlib import Path

import pytest

from helpers import Env
from workbench.errors import Rejected
from workbench.projectfolder import MARKER
from workbench.service import Workbench


def test_portable_project_reopens_outside_store_without_other_project_data(tmp_path):
    env = Env(tmp_path/"source")
    wb = env.wb
    project = env.full_episode(segments={"G01":[8000]})
    other = env.full_episode(segments={"G02":[9000]})
    private = wb.import_file(other["pid"],"private.txt",b"ONLY IN OTHER PROJECT","story_source")
    wb.put_setting("codex_path","/should-never-travel/codex")
    manuscript = wb.import_file(project["pid"],"手稿.txt","开门。".encode(),"story_source")
    source = Path(project["delivery"]["path"])
    out = tmp_path/"带走"/"独立项目"
    out.parent.mkdir()
    report = wb.export_project_folder(project["pid"],str(out))
    assert report["projectId"] == project["pid"] and (out/MARKER).is_file()
    with pytest.raises(Rejected):
        wb.export_project_folder(project["pid"],str(out))
    moved = out.rename(tmp_path/"移动后")
    replica = Workbench(moved,{})
    assert [p["id"] for p in replica.list_projects()] == [project["pid"]]
    assert replica.get_setting("codex_path") is None
    with env.db() as source_db, sqlite3.connect(replica.db_path) as replica_db:
        expected = dict(source_db.execute(
            "SELECT b.version_id,b.segments FROM version_plan_basis b JOIN version v ON v.id=b.version_id "
            "JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=?", (project["eid"],)))
        assert any("G01" in json.loads(segments) for segments in expected.values())
        assert dict(replica_db.execute("SELECT version_id,segments FROM version_plan_basis")) == expected
    assert replica.episode_view(project["eid"]) == wb.episode_view(project["eid"])
    assert replica.read_import(manuscript["importId"])["parsed_text"] == "开门。"
    assert not (moved/"projects"/other["pid"]).exists()
    assert private["importId"].encode() not in (moved/"workbench.sqlite").read_bytes()
    assert replica.asset_bytes(project["img"]["versionId"]) == wb.asset_bytes(project["img"]["versionId"])
    delivery = project["delivery"]["deliveryId"]
    assert replica.verify_delivery(delivery)["ok"]
    assert (moved/replica.delivery_view(delivery)["relpath"]/"episode.json").read_bytes() == (source/"episode.json").read_bytes()
    replica.rename_project(project["pid"],"在外盘继续编辑",replica.list_projects()[0]["name"])
    assert wb.list_projects()[0]["name"] != "在外盘继续编辑"


def test_folder_validates_materials_and_does_not_start_inflight_work(tmp_path):
    import sqlite3
    from base64 import b64encode
    from zipfile import ZipFile
    from workbench.folderserver import inspect_folder, FolderWorkbench
    from workbench.images import png_bytes
    env = Env(tmp_path/"source")
    wb = env.wb
    ep = env.full_episode(segments={"G01":[8000]})
    pid, eid = ep["pid"], ep["eid"]
    out = tmp_path/"外盘"
    packed = wb.package(ep["delivery"]["deliveryId"],["G01"],"portable-zip")
    picture = png_bytes(3,4)
    card = wb.add_palette("雨夜","湿路面",["#30271D"],"侧光","慢推","手绘",b64encode(picture).decode())["id"]
    wb.save_palettes(pid,[card])
    method = wb.import_custom_method(pid,"SKILL.md","先看动作。".encode(),"出镜")
    wb.set_custom_methods(pid,"story",[method["versionId"]],[],episode_id=eid)
    run = wb.create_task(eid,"story","尚未发送")["runId"]
    wb.export_project_folder(pid,str(out))
    assert inspect_folder(out)==pid
    replica = FolderWorkbench(out,wb)
    assert replica.pending_runs()==[] and run in wb.pending_runs()
    assert replica.run_methods(run)==wb.run_methods(run)
    assert replica.custom_methods(pid,"story",eid)["selected"]==[method["versionId"]]
    assert replica.palette_image(card,replica.palette_detail(card)["cover"])==picture
    replay = replica.package(ep["delivery"]["deliveryId"],["G01"],"portable-zip")
    assert Path(replay["path"]).is_relative_to(out) and Path(replay["path"]).read_bytes()==Path(packed["path"]).read_bytes()
    with ZipFile(replay["path"]) as zipfile:
        assert zipfile.testzip() is None
    with sqlite3.connect(replica.db_path) as db:
        db.execute("CREATE TRIGGER sqliteXtrigger AFTER UPDATE ON project BEGIN SELECT 1; END;")
    with pytest.raises(Rejected, match="未知结构"):
        inspect_folder(out)
    digest = wb.asset_version(ep["img"]["versionId"])["sha256"]
    wb.store.blob_path(digest).write_bytes(b"damage")
    with pytest.raises(Rejected,match="指纹不一致"):
        wb.export_project_folder(pid,str(tmp_path/"损坏副本"))
    assert not (tmp_path/"损坏副本").exists()


def test_folder_copy_keeps_image_request_identity_and_reconciles_pending(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    pid,eid=env.new_episode()
    provider=env.images
    batch=wb.create_batch(eid,provider.name,"generate",[dict(prompt="台阶",target={"name":"台阶","category":"scene"})],
                          provider.capabilities["sizes"][0],budget_cents=100)
    wb.authorize_batch(batch["batchId"],batch["summaryHash"],"allow-image",budget_cents=100)
    folder=tmp_path/"副本"
    wb.export_project_folder(pid,str(folder))
    replica=Workbench(folder,{},image_providers={provider.name:provider})
    src=wb.batch_view(batch["batchId"])
    copy=replica.batch_view(batch["batchId"])
    assert copy["requests"][0]["local_request_id"]==src["requests"][0]["local_request_id"]
    assert src["state"]=="authorized" and src["requests"][0]["state"]=="pending"
    assert copy["state"]=="pending_reconcile" and copy["requests"][0]["state"]=="unknown"
    with pytest.raises(Rejected):
        replica.execute_batch(batch["batchId"])


def test_in_use_and_replaced_folder_are_rejected_without_mutation(tmp_path):
    import fcntl
    import os
    from workbench.folderserver import FolderServers
    env = Env(tmp_path/"source")
    pid, _ = env.new_episode()
    out = tmp_path/"独立目录"
    info = env.wb.export_project_folder(pid,str(out))
    server = FolderServers(env.wb)
    fd = os.open(out,os.O_RDONLY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(Rejected,match="另一个工作台"):
            server.open(str(out),info["initialDatabaseSha256"])
    finally:
        os.close(fd)
    with pytest.raises(Rejected,match="原独立项目不符"):
        server.open(str(out),"wrong")
    (out/"外部引用").symlink_to(env.wb.db_path)
    with pytest.raises(Rejected,match="符号链接"):
        server.open(str(out))
    assert not server.entries()
