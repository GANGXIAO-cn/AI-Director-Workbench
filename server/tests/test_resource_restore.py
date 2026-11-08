import json
from base64 import b64encode
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import pytest

from helpers import Env
from workbench.errors import Rejected
from workbench.images import png_bytes
from workbench.service import Workbench


def test_restore_owned_sources_and_exported_zip(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    ep = env.full_episode(segments={"G01":[8000]})
    pid, eid, did = ep["pid"], ep["eid"], ep["delivery"]["deliveryId"]
    package = wb.package(did, ["G01"], "take-home")
    image = png_bytes(4,4,(31,55,77))
    cid = wb.add_palette("夜蓝","冷光",["#1F374D"],"背光","缓推","自拍",b64encode(image).decode())["id"]
    card = wb.save_palettes(pid,[cid])[0]
    method = wb.import_custom_method(pid,"SKILL.md","雨棚外补反光。".encode(),"夜雨")
    wb.set_custom_methods(pid,"story",[method["versionId"]],[],episode_id=eid)
    old = wb.create_task(eid,"story","备份前冻结")["runId"]
    bid = wb.backup()["backupId"]
    target = tmp_path/"副本"
    wb.restore(bid, target)
    recovered = Workbench(target, wb.executors)
    assert recovered.project_palettes(pid)==[card]
    detail = recovered.palette_detail(cid)
    assert recovered.palette_image(cid,detail["cover"])==image and detail["sources"]=="自拍"
    assert recovered.run_methods(old)==wb.run_methods(old)
    assert recovered.custom_method_text(pid,method["versionId"])["text"]=="雨棚外补反光。"
    assert recovered.custom_methods(pid,"story",eid)["selected"]==[method["versionId"]]
    export = recovered.package(did, ["G01"], "take-home")
    dest = Path(export["path"])
    assert dest.is_relative_to(target) and dest.read_bytes()==Path(package["path"]).read_bytes()
    with ZipFile(dest) as z:
        assert z.testzip() is None
    redelivered = recovered.deliver(eid,"full",eid+"deliver")
    assert Path(redelivered["html"]).is_relative_to(target) and Path(redelivered["html"]).is_file()
    assert recovered.verify_delivery(did)["ok"]


def test_backup_tamper_and_outside_target(tmp_path):
    env = Env(tmp_path)
    wb=env.wb
    pid,_=env.new_episode()
    wb.import_custom_method(pid,"SKILL.md",b"original","方法")
    bid=wb.backup()["backupId"]
    with pytest.raises(Rejected,match="以外"):
        wb.restore(bid,wb.store.root/"projects"/"invalid")
    folder=wb.store.root/"backups"/bid
    manifest=folder/"manifest.json"
    raw=manifest.read_bytes()
    data=json.loads(raw)
    data["files"]["../escape"]="fake"
    manifest.write_text(json.dumps(data))
    with pytest.raises(Rejected,match="指纹"):
        wb.restore(bid,tmp_path/"tampered")
    assert not (tmp_path/"tampered").exists()
    manifest.write_bytes(raw)
    digest=sha256(b"original").hexdigest()
    (folder/"files"/"blobs"/"sha256"/digest[:2]/digest).write_bytes(b"broken")
    with pytest.raises(Rejected,match="原件校验失败"):
        wb.restore(bid,tmp_path/"broken")
    assert not (tmp_path/"broken").exists()
