import json
from pathlib import Path

import pytest

from helpers import Env
from workbench.db import transaction
from workbench.errors import Conflict, Rejected
from workbench.storage import sha256_bytes


def test_batch_freezes_scene_evidence_and_retries_the_same_ordered_inputs(tmp_path, monkeypatch):
    env = Env(tmp_path, image_script=["insufficient","insufficient","ok"])
    wb = env.wb
    pid, eid = env.new_episode()
    ref = env.image(pid,eid,name="站台原图",category="scene")
    other = env.image(pid,eid,name="坏伞状态",category="state",rgb=(55,66,77))
    _, other_ep = env.new_episode(project_id=pid,code="E02")
    unrelated = env.image(pid,other_ep,name="另外一集")
    with pytest.raises(Rejected,match="只能为本项目"):
        wb.create_batch(eid,"fake-image","generate",[dict(prompt="错集",target={"assetId":unrelated["assetId"]})],"64x64")
    lock = wb.add_space_item(eid,"站台","车门在右侧","剧本",["G01"])["itemId"]
    with pytest.raises(Rejected,match="本集可用"):
        wb.confirm_space_item(lock,unrelated["versionId"])
    wb.confirm_space_item(lock,ref["versionId"])
    def batch(segments, refs):
        return wb.create_batch(eid,"fake-image","generate",[dict(prompt="雨中站台",segments=segments,inputs=refs)],"64x64",budget_cents=200)
    refs = [other["versionId"],ref["versionId"]]
    pending = batch(["G01"],refs)
    bid = pending["batchId"]
    record = wb.batch_view(bid)["spec"]["sceneLocks"][0][0]
    assert record["positions"] == [2] and record["sha256"] == ref["sha256"]
    grid=env.image(pid,eid,name="站台九宫格",category="grid",grid=True,rgb=(34,44,55))
    edit=wb.redraw_cells(eid,grid["versionId"],[4],[ref["versionId"]],"只改雨量","fake-image","64x64",segments=["G01"])
    assert wb.batch_view(edit["batchId"])["spec"]["sceneLocks"][0][0]["positions"]==[2]
    # A materialized copy with identical bytes is valid, including in a different input slot.
    duplicate=env.image(pid,eid,name="锁定原图的副本",category="scene")
    same=batch(["G01"],[duplicate["versionId"],ref["versionId"]])
    assert wb.batch_view(same["batchId"])["spec"]["sceneLocks"][0][0]["positions"]==[1,2]
    omitted = batch(["G01"],[])
    assert wb.batch_view(omitted["batchId"])["spec"]["sceneLocks"][0][0]["positions"] == []
    assert "sceneLocks" not in wb.batch_view(batch(["G02"],[])["batchId"])["spec"]
    wb.confirm_space_item(lock,other["versionId"])
    assert wb.space_locks(eid)[0]["sha256"] == other["sha256"]
    received = []
    original = env.images.submit
    def observed(request):
        received.append([sha256_bytes(Path(p).read_bytes()) for p in request["inputs"]])
        return original(request)
    monkeypatch.setattr(env.images,"submit",observed)
    wb.authorize_batch(bid,pending["summaryHash"],"approve")
    first = wb.execute_batch(bid)["requests"][0]
    wb.resubmit_request(first["id"],"first retry")
    with pytest.raises(Conflict):
        wb.resubmit_request(first["id"],"second tab")
    second = wb.execute_batch(bid)["requests"][1]
    assert first["state"]==second["state"]=="failed"
    wb.resubmit_request(second["id"],"second retry")
    done = wb.execute_batch(bid)
    assert done["requests"][-1]["state"]=="succeeded"
    assert done["spec"]["sceneLocks"][0][0] == record
    assert received==[[other["sha256"],ref["sha256"]]]*3
    to_fail=batch(["G01"],[other["versionId"]])
    wb.authorize_batch(to_fail["batchId"],to_fail["summaryHash"],"approve-later")
    wb.store.blob_path(other["sha256"]).write_bytes(b"corrupt fixture")
    result=wb.execute_batch(to_fail["batchId"])["requests"][0]
    assert result["state"]=="failed" and "冻结的输入图片不可用" in result["error"] and len(received)==3


def test_each_candidate_keeps_its_own_ordered_inputs_after_retry(tmp_path):
    env = Env(tmp_path, image_script=["ok", "insufficient", "ok"])
    wb = env.wb
    pid, eid = env.new_episode()
    a = env.image(pid, None, name="项目原图")
    b = env.image(pid, eid, name="本集原图", rgb=(2, 3, 4))
    items = [dict(prompt="正序", inputs=[a["versionId"], b["versionId"]]),
             dict(prompt="逆序", inputs=[b["versionId"], a["versionId"]])]
    batch = wb.create_batch(eid, "fake-image", "generate", items, "64x64", budget_cents=100)
    bid = batch["batchId"]
    wb.authorize_batch(bid, batch["summaryHash"], "approve")
    second = wb.execute_batch(bid)["requests"][1]
    wb.resubmit_request(second["id"], "one retry")
    done = wb.execute_batch(bid)
    assert [r["sourceOrdinal"] for r in done["requests"]] == [1, 2, 2]
    assert [[(r["name"], r["sha256"], r["versionNo"]) for r in group] for group in done["inputGroups"]] == [
        [("项目原图", a["sha256"], 1), ("本集原图", b["sha256"], 1)],
        [("本集原图", b["sha256"], 1), ("项目原图", a["sha256"], 1)]]
    with transaction(wb.db_path) as db:
        historical = dict(done["spec"])
        historical.pop("inputs")
        historical["items"][0].pop("inputs")
        db.execute("UPDATE image_batch SET spec=? WHERE id=?", (json.dumps(historical), bid))
    old = wb.batch_view(bid)["inputGroups"]
    assert old[0] is None and old[1][0] == dict(versionId=b["versionId"], sha256=None)


def test_late_provider_reply_preserves_reconciled_image(tmp_path, monkeypatch):
    from workbench.images import png_bytes
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    batch = wb.create_batch(eid, "fake-image", "generate", [dict(prompt="站台")], "64x64", budget_cents=20)
    bid = batch["batchId"]
    wb.authorize_batch(bid, batch["summaryHash"], "approve")
    rid = wb.batch_view(bid)["requests"][0]["id"]
    data = png_bytes(64, 64, (3, 4, 5))
    original = env.images.submit

    def reconcile_before_reply(request):
        with pytest.raises(Rejected):
            wb.cancel_batch(bid)
        wb.reconcile_request(rid, "completed", data)
        return original(request)

    monkeypatch.setattr(env.images, "submit", reconcile_before_reply)
    result = wb.execute_batch(bid)
    assert result["requests"][0]["sha256"] == sha256_bytes(data)
    assert len(wb.episode_assets(eid)) == 1


def test_archived_scene_inputs_stay_replayable_without_sources(tmp_path):
    env=Env(tmp_path/"test")
    wb=env.wb
    pid=wb.create_project("_template")["projectId"]
    project=tmp_path/"project"
    root=project/"episodes"/"E01"
    log=root/"过程稿"/"实际生成输入版本.json"
    log.parent.mkdir(parents=True)
    tool=tmp_path/"image-tool"
    tool.mkdir()
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?", (json.dumps(dict(assetRoot=str(project),imageOutputRoot=str(tool))),pid))
    def image(path, value):
        path.write_bytes(value)
        return dict(path=str(path.resolve()),sha256=sha256_bytes(value))
    anchor=image(root/"anchor.png",b"original anchor")
    asset=dict(image(root/"state.png",b"adopted state"),id="S",type="image",category="state",scene_ids=["platform"],generation_request="R")
    external=image(tool/"actual.png",b"original anchor")
    stray=image(tmp_path/"private.png",b"outside the registered root")
    data=dict(schema_version=2,assets=[asset],scene_anchors={"platform":dict(anchor,responsibility="space")},
              segments=[dict(references=[dict(asset_id="S")])])
    (root/"episode.json").write_text(json.dumps(data))
    def request(reference):
        log.write_text(json.dumps({"R":dict(output=asset,input_versions=[reference])}))
        return wb.import_historical_episode(pid,"E01")
    first=request(external)
    assert first["materialChecks"]["sceneAnchors"]["state"]=="checked"
    second=request(stray)
    warnings=second["materialChecks"]["sceneAnchors"]["warnings"]
    assert [w["code"] for w in warnings]==["scene_inputs_unchecked"]
    assert not wb.store.blob_path(stray["sha256"]).exists()
    project.rename(tmp_path/"offline-project")
    tool.rename(tmp_path/"offline-tool")
    replay=wb.historical_receipt(pid,first["receipt"])
    assert replay["materialRechecked"]["sceneAnchors"]==first["materialChecks"]["sceneAnchors"]
    assert replay["integrity"]["ok"]
