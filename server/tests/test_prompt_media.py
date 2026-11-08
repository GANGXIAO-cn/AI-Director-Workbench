from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import wave

import pytest

from helpers import Env
from workbench import h3export
from workbench.errors import Conflict, Rejected


def wav(directory, name, seconds, level=0):
    path = directory / name
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", ""))
        output.writeframes(level.to_bytes(2, "little") * 8000 * seconds)
    return path


@pytest.mark.parametrize("ext,kind", [(e, k) for k in ("audio", "video") for e in sorted(h3export.EXTENSIONS[k])])
def test_supported_upload_containers_require_matching_stream(tmp_path, ext, kind):
    source = tmp_path / ("source" + ext)
    clip = ["color=blue:s=64x64:d=3"] if kind == "video" else ["sine=frequency=300:duration=3"]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", *clip, str(source)], check=True)
    with source.open("rb") as stream:
        duration = h3export.media_duration(Path("/dev/stdin"), kind, source.name, stream)
    assert 3 <= duration < 3.2
    wrong = "wrong.avi" if kind == "audio" else "wrong.flac"
    with pytest.raises(ValueError):
        h3export.media_duration(source, h3export.media_kind(wrong), wrong)


def test_retry_duration_identity_and_reference_renumbering(tmp_path):
    env = Env(tmp_path)
    pid, eid = env.new_episode()
    _, other = env.new_episode(project_id=pid, code="another")
    env.adopt_story(eid)
    env.script(eid)
    env.save_adopt(eid, "plan", env.plan86({"G01":[8000], "G02":[8000]}, {"G02":"grid"}))
    env.image(pid, eid)
    env.image(pid, eid, "G02 开场九宫格", "grid", grid=True)
    content = env.wb.draft_prompts(eid)
    art = env.art(eid, "prompts")["id"]
    original = env.wb.save_manual(art, None, content, "initial")["versionId"]

    def upload(file, cid, episode=eid, name=None):
        with file.open("rb") as stream:
            return env.wb.upload_reference_media(episode, name or file.name, stream, cid)["versionId"]

    def add(version, asset, cid, role, segment="G01"):
        return env.wb.edit_prompt_reference(version, segment, "add", cid, asset_id=asset, purpose=role)["versionId"]

    short = wav(tmp_path, "a.wav", 7)
    with ThreadPoolExecutor(2) as pool:
        a, replay = pool.map(lambda _:upload(short,"same-upload"),range(2))
    assert a == replay
    with pytest.raises(Conflict):
        upload(short,"same-upload",name="changed.wav")
    with pytest.raises(Rejected):
        upload(short,"bad-path",name="../a.wav")
    with pytest.raises(Rejected,match="2–15"):
        upload(wav(tmp_path,"short.wav",1),"short")
    b = upload(wav(tmp_path, "b.wav", 8, 1),"b")
    c = upload(wav(tmp_path, "c.wav", 2, 2),"c")
    other_asset = upload(short,"other",other)
    with pytest.raises(Rejected,match="本集"):
        add(original,other_asset,"other-ref","音色")
    with pytest.raises(Rejected,match="九宫格"):
        add(original,a,"grid-ref","音色","G02")
    first = add(original,a,"add-a","走路")
    assert add(original,a,"add-a","走路") == first
    duplicate = upload(short, "second-name", name="alias.wav")
    with pytest.raises(Rejected, match="相同文件"):
        add(first,duplicate,"duplicate","重复")
    both = add(first,b,"add-b","回声")
    with pytest.raises(Rejected, match="总时长"):
        add(both,c,"too-long","额外声音")
    with pytest.raises(Rejected,match="总时长"):
        env.wb.edit_prompt_reference(both,"G01","replace","replace-long",asset_id=upload(wav(tmp_path,"nine.wav",9,3),"nine"),previous_id=a)
    replacement = env.wb.edit_prompt_reference(both,"G01","replace","replace-a",asset_id=c,previous_id=a)["versionId"]
    def read(version):
        v = next(x for x in env.art(eid,"prompts")["versions"] if x["id"] == version)
        return v["content"]["segments"]["G01"]
    assert read(replacement)["refs"][1]["assetVersionId"] == c
    assert read(replacement)["txt"] == read(both)["txt"]
    draft = dict(txt=read(replacement)["txt"]+"\n依音频2收束。", ambience=["雨"], dialogue=[])
    last = env.wb.edit_prompt_reference(replacement,"G01","remove","remove",previous_id=c,draft=draft)["versionId"]
    entry = read(last)
    assert [(r["label"],r["assetVersionId"]) for r in entry["refs"][1:]] == [("音频1",b)]
    assert "音频1：回声" in entry["txt"] and "依音频1收束" in entry["txt"] and "走路" not in entry["txt"]
    assert entry["sound"]["ambience"] == ["雨"]
    assert read(original)["txt"] == content["segments"]["G01"]["txt"]
    assert len(env.art(eid,"prompts")["versions"]) == 5


def test_unknown_h3_tag_names_the_segment_and_reference():
    segment = dict(id="G03",prompt="图片1 后使用 @Audio2",refs=[dict(label="图片1",assetId="picture")])
    with pytest.raises(ValueError,match="G03.*@Audio2"):
        h3export.check_prompt(segment, {"picture":"image"})
