import json
from pathlib import Path
import wave
import zipfile

import pytest

from helpers import Env
from workbench import h3export
from workbench.delivery import DEFAULT_PACKAGER
from workbench.errors import Rejected


def test_preview_origin_override_and_replay(tmp_path):
    if not DEFAULT_PACKAGER.is_file():
        pytest.skip("本机缺少 H3 打包器")
    env = Env(tmp_path)
    d = env.full_episode(segments={"G01":[8000],"G02":[6000]})["delivery"]["deliveryId"]
    preview = env.wb.package_preview(d, ["G01"], {"candidates":2})
    row = preview["segments"][0]
    assert row["origins"]["ratio"] == "本集画幅" and row["origins"]["candidates"] == "本次覆盖"
    assert row["params"]["candidates"] == 2 and row["refs"][0]["sourceId"] == row["refs"][0]["packedId"]
    with pytest.raises(Rejected, match="重新预览"):
        env.wb.package(d, ["G02"], "changed-selection", {"candidates":2}, preview["previewHash"])
    with pytest.raises(Rejected, match="仅支持"):
        env.wb.package_preview(d, ["G01"], {"duration":11})
    with pytest.raises(Rejected, match="单段覆盖"):
        env.wb.package_preview(d, ["G01"], segment_overrides={"G02":{"audio":False}})
    with pytest.raises(Rejected, match="单段覆盖"):
        env.wb.package_preview(d, ["G01"], segment_overrides={"G01":{"ratio":"9:16"}})
    with env.db() as db:
        assert db.execute("SELECT COUNT(*) FROM package").fetchone()[0] == 0
    result = env.wb.package(d, ["G01"], "export", {"candidates":2}, preview["previewHash"])
    with zipfile.ZipFile(result["path"]) as archive:
        packed=json.loads(archive.read("project.json"))
    assert packed["assets"][0]["origin"] == "new"
    assert row["params"] == packed["segments"][0]["params"]
    assert env.wb.package(d, ["G01"], "export", {"candidates":2}, preview["previewHash"])["packageId"] == result["packageId"]


def test_reject_zip_different_from_preview(tmp_path):
    if not DEFAULT_PACKAGER.is_file():
        pytest.skip("本机缺少 H3 打包器")
    env = Env(tmp_path)
    delivery = env.full_episode(segments={"G01":[8000]})["delivery"]["deliveryId"]
    custom = tmp_path/"packager.py"
    custom.write_bytes(DEFAULT_PACKAGER.read_bytes() + b'''
_original_write = write_zip
def write_zip(stream, package, files):
    _original_write(stream, dict(package,title="Different"), files)
''')
    env.wb.put_setting("packager_path",str(custom))
    with pytest.raises(Rejected, match="project.json"):
        env.wb.package(delivery, ["G01"], "bad-writer")
    with env.db() as db:
        assert db.execute("SELECT COUNT(*) FROM package").fetchone()[0] == 0


def test_consumer_prompt_boundaries_and_unicode():
    seg=dict(id="G01",refs=[dict(label="图片1",assetId="card")],prompt="图片1走过。")
    h3export.check_prompt(seg, {"card":"image"})
    for text in ["图片1 <Picture 1>", "图片1"+"😀"*24996, "图片1"+"\n"*10000]:
        with pytest.raises(ValueError):
            h3export.check_prompt(dict(seg,prompt=text), {"card":"image"})


def test_media_reference_durations_match_h3(tmp_path):
    path = tmp_path/"steps.wav"
    for seconds in (1, 2, 15, 16):
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", ""))
            stream.writeframes(b"\0\0"*(8000*seconds))
        if 2 <= seconds <= 15:
            assert h3export.media_duration(path, "audio")==seconds
        else:
            with pytest.raises(ValueError,match="2–15"):
                h3export.media_duration(path, "audio")


def test_identity_aliases_only_within_selected_scope():
    originals = [dict(id=k,sha256="abc",type="image") for k in ("first","second")]
    segments = [dict(id=k,references=[dict(asset_id=v)]) for k,v in [("01","first"),("02","second")]]
    project = dict(data=dict(assets=originals,segments=segments))
    assert h3export.unique_inputs(project, ["02"])["data"]["segments"][1]["references"][0]["asset_id"] == "second"
    assert h3export.unique_inputs(project, ["01","02"])["data"]["segments"][1]["references"][0]["asset_id"] == "first"
    assert segments[1]["references"][0]["asset_id"] == "second"
    segments[0]["references"].append(dict(asset_id="second"))
    with pytest.raises(ValueError,match="01.*内容相同"):
        h3export.unique_inputs(project, ["01"])


def test_redraw_on_a_reused_asset_has_its_own_origin(tmp_path):
    env=Env(tmp_path)
    pid,eid=env.new_episode()
    old=env.image(pid,None)
    item=env.wb.submit_reuse(old["versionId"],"人物参考")["entryId"]
    env.wb.review_library(item,"approved")
    copied=env.wb.copy_to_episode(item,eid)
    from workbench.images import png_bytes
    update=env.wb.register_asset(pid,"同一角色新版",png_bytes(64,64,(33,77,99)),"identity",episode_id=eid,
                                 asset_id=copied["assetId"],source_version_id=copied["versionId"],origin="generated")
    assert h3export.asset_origin(env.wb.asset_version(copied["versionId"]))=="reuse"
    assert h3export.asset_origin(env.wb.asset_version(update["versionId"]))=="new"
