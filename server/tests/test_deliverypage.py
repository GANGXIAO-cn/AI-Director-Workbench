import io
import json
from pathlib import Path
import wave

import pytest
from PIL import Image

from helpers import Env
from workbench import deliverypage
from workbench.errors import Rejected


def test_pack_preserves_media_bytes_prompt_field_and_selected_roles(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01":[8000]})
    env.save_adopt(eid, "plan", plan)
    photo = io.BytesIO()
    Image.new("RGB",(9,16),"green").save(photo,format="JPEG")
    with pytest.raises(Rejected, match="图片无法解码"):
        wb.register_asset(pid, "截断的JPEG", photo.getvalue()[:-20], "identity", episode_id=eid)
    jpeg = wb.register_asset(pid,"肖像 & 照片",photo.getvalue(),"identity",episode_id=eid)["versionId"]
    sound = io.BytesIO()
    with wave.open(sound,"wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(160))
    wav_id = wb.register_asset(pid,"站台广播",sound.getvalue(),"sound",kind="audio",episode_id=eid)["versionId"]
    wb.set_cast(eid,"G01","旅客","ok",jpeg)
    text = "\n00:00.000-00:08.000 图片1 中的人抬头。\r\n音频1：广播响起。<雨> & 伞。"
    content = env.prompts_for(plan, jpeg, {"G01":text})
    entry = content["segments"]["G01"]
    entry["refs"][0]["shots"] = ["S02"]
    entry["refs"].append(dict(assetVersionId=wav_id, label="音频1", kind="audio", role="广播"))
    with pytest.raises(Rejected,match="作用镜头"):
        env.save_adopt(eid, "prompts",content,"bad-shot")
    entry["refs"][0]["shots"] = ["S01"]
    pv,_ = env.save_adopt(eid,"prompts",content)
    env.review_segments(pv, ["G01"])
    for call in env.claude.calls[-2:]:
        assert [p.name for p in call.images] == ["01_图片1.jpg"]
        assert call.images[0].read_bytes() == photo.getvalue()
        assert "本次文本审查未审看视频或试听音频" in call.prompt
    delivery = wb.deliver(eid,"trial","trial",["G01"])
    root = Path(delivery["path"])
    data = json.loads((root/"episode.json").read_bytes())
    image, audio = data["assets"]
    assert (root/image["path"]).suffix==".jpg" and (root/image["path"]).read_bytes()==photo.getvalue()
    assert (root/audio["path"]).suffix==".wav" and (root/audio["path"]).read_bytes()==sound.getvalue()
    assert (root/data["segments"][0]["prompt"]).read_bytes().decode()==text
    page = root/"00_图文对照.html"
    original = page.read_text()
    assert '<audio controls' in original and '作用镜头：S01' in original
    assert '<pre><span>\n' in original and '&#13;' in original
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]
    files = wb.delivery_view(delivery["deliveryId"])["files"]
    for before, after, issue in [
        ('作用镜头：S01','作用镜头：S99','作用镜头'),
        ('身份清楚','未出镜','角色绑定'),
        ('<audio controls','<video controls','素材预览'),
        ('未采用图片／补选</a>','补图</a>','补选同页入口'),
    ]:
        assert before in original
        page.write_text(original.replace(before,after,1))
        with pytest.raises(ValueError,match=issue):
            deliverypage.verify(root, data, files)
    page.write_text(original)
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]


def test_same_bytes_new_reference_and_changed_requirements_expire_only_affected_reviews(tmp_path):
    from workbench.storage import canonical, sha256_bytes
    from workbench.db import transaction

    env = Env(tmp_path)
    wb = env.wb
    case = env.full_episode(segments={"G01":[7000], "G02":[8000]}, deliver=False)
    old = case["prompts"]
    aid = env.art(case["eid"], "prompts")["id"]
    inputs = env.prompts_for(case["plan"], case["img"]["versionId"])
    sid = "G01"
    ref = inputs["segments"][sid]["refs"][0]
    entry = inputs["segments"][sid]
    frozen = wb.prompt_review_status(old)[sid]
    legacy = sha256_bytes(canonical(dict(txt=entry["txt"], refs=[(ref["label"], ref.get("role") or "用途未写", case["img"]["sha256"])])))
    with transaction(wb.db_path) as db:
        db.execute("UPDATE review SET scope_hash=? WHERE id=?", (legacy, frozen["coldRead"]["reviewId"]))
    assert wb.prompt_review_status(old)[sid]["coldRead"]["reviewId"] == frozen["coldRead"]["reviewId"]
    replacement = wb.register_asset(case["pid"], "另一份登记", wb.asset_bytes(case["img"]["versionId"]),
                                    "scene", episode_id=case["eid"])
    assert replacement["sha256"] == case["img"]["sha256"]
    ref["assetVersionId"] = replacement["versionId"]
    new = wb.save_manual(aid, old, inputs, "replace")["versionId"]
    current = wb.prompt_review_status(new)
    assert current["G01"]["coldRead"]["state"] == "needed" and current["G02"]["ok"]
    wb.save_step_config(case["eid"], "prompts", keep="保持车门打开")
    current = wb.prompt_review_status(old)
    assert current["G02"]["coldRead"]["state"] == "passed" and current["G02"]["comparison"]["state"] == "needed"


def test_cast_status_and_grid_positions_are_validated_before_writing(tmp_path):
    env = Env(tmp_path)
    case = env.full_episode(segments={"G01":[8000]},deliver=False)
    wb, eid = env.wb, case["eid"]
    version = case["img"]["versionId"]
    grid = env.image(case["pid"],eid,"九宫格","grid",grid=True)["versionId"]
    for seg, status, vid, cells, error in [
        ("G02","ok",version,[],"采用制作方案"),
        ("G01","absent",version,[],"未出镜角色"),
        ("G01","ok",version,[1],"九宫格"),
        ("G01","ok",grid,[True],"不重复整数"),
        ("G01","ok",grid,[2,2],"不重复整数"),
        ("G01","ok",grid,[10],"不重复整数"),
    ]:
        with pytest.raises(Rejected,match=error):
            wb.set_cast(eid,seg,"guard",status,vid,cells)
    assert wb.cast_matrix(eid)["rows"][0]["cells"] == {}
    wb.set_cast(eid,"G01","guard","ok",grid,[1,9],"首尾都露出银扣")
    wb.set_cast(eid,"G01","guard","absent",None,[],"镜头改到室外")
    assert wb.cast_matrix(eid)["rows"][0]["cells"]["guard"]["assetVersionId"] is None


def test_prompt_receipt_preserves_review_return_and_user_waiver(tmp_path):
    from workbench.executors.fake import review
    from workbench.review_records import records

    env = Env(tmp_path)
    wb = env.wb
    case = env.full_episode(segments={"G01":[8000]}, deliver=False)
    version = case["prompts"]
    wb.save_step_config(case["eid"], "prompts", keep="场末保留停顿")
    env.claude.script.append(lambda req: review(req, "return", [dict(category="clarity",reason="停顿略长")]))
    rid = wb.start_prompt_review(version,"G01","comparison","with-issue")["runId"]
    env.drain(rid)
    status = wb.prompt_review_status(version)["G01"]
    assert not status["ok"]
    with pytest.raises(Rejected,match="交付核验未通过"):
        wb.deliver(case["eid"],"trial","not-adopted",["G01"])
    issue, = status["comparison"]["issues"]
    wb.waive_segment_issue(issue["id"],"保留门外人物的反应拍","accept")
    accepted = wb.deliver(case["eid"],"trial","accepted",["G01"])
    text = (Path(accepted["path"])/"review.md").read_text()
    entry, = records(text)
    assert entry["verdict"] == "退回" and "工作台处置 用户放行" in text
    assert entry["issues"].startswith("已接受：") and "保留门外人物的反应拍" in entry["issues"]
    assert "对照 退回" in text and wb.verify_delivery(accepted["deliveryId"])["ok"]
