import json
from io import BytesIO
from pathlib import Path
import sys

from PIL import Image
import pytest

from helpers import Env
from workbench.errors import Conflict, Rejected
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"validation/workflows"))
from contact_fixture import prepare


def test_adopted_scene_contact_and_portable_originals(tmp_path):
    env=Env(tmp_path)
    sample=prepare(env)
    wb,eid=env.wb,sample["eid"]
    config=wb.episode_contact_options(eid)
    state=sample["state"]["versionId"]
    costume=sample["costume"]["versionId"]
    assert [a["id"] for a in config["images"]]==[state]
    assert config["images"][0]["scene_ids"]==[] and config["scenes"]==["场02"]
    with pytest.raises(Rejected,match="scene_ids"):
        wb.episode_contact_sheet(eid)
    lock=next(iter(config["locks"]))
    settings=dict(images={state:["场02"]},locks={"场02":lock},outfits={costume:"afu"})
    wb.save_contact_options(eid,settings,config["fingerprint"])
    report=wb.episode_contact_sheet(eid)
    scene,=report["scenes"]
    assert report["review_status"]=="not_tested" and scene["segments"]==["G01"]
    assert [a["sha256"] for a in scene["sources"]]==[sample["anchor"]["sha256"],sample["state"]["sha256"]]
    assert [(r["role"],r["reference_kind"],r["sha256"]) for r in scene["role_references"]]==[
        ("afu","identity",sample["identity"]["sha256"]),("afu","outfit",sample["costume"]["sha256"])]
    sheet=wb.store.read_blob(scene["pages"][0]["sha256"])
    assert Image.open(BytesIO(sheet)).size==(1600,1440)
    out=tmp_path/"offline"
    wb.export_episode_contact_sheet(eid,str(out),report["fingerprint"])
    assert json.loads((out/"index.json").read_bytes())==report and len(list((out/"originals").iterdir()))==4
    assert "file://" not in (out/"index.html").read_text()
    with pytest.raises(Rejected,match="已存在"):
        wb.export_episode_contact_sheet(eid,str(out),report["fingerprint"])
    with pytest.raises(Conflict):
        wb.save_contact_options(eid,settings,config["fingerprint"])
    current=wb.episode_contact_options(eid)
    wb.save_contact_options(eid,dict(settings,locks={}),current["fingerprint"])
    with pytest.raises(Conflict):
        wb.export_episode_contact_sheet(eid,str(tmp_path/"other"),report["fingerprint"])
    assert not (tmp_path/"other").exists()
