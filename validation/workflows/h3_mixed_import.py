"""Exercise image, video and audio paths through delivery, ZIP and the native H3 importer offline."""
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"server"), str(ROOT/"server/tests")]
from helpers import Env


def run(directory, studio, evidence):
    env = Env(directory)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    env.save_adopt(eid, "plan", env.plan86({"G01":[8000]}))
    env.image(pid,eid)
    prompts = wb.draft_prompts(eid)
    entry = prompts["segments"]["G01"]
    clip = directory/"turn.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=blue:s=64x64:d=2", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(clip)], check=True)
    recording = BytesIO()
    with wave.open(recording,"wb") as out:
        out.setparams((1, 2, 8000, 0, "NONE", ""))
        out.writeframes(bytes(32000))
    for kind, filename, data, label, role in (("video", "turn.mp4", clip.read_bytes(), "视频1", "转身节奏"),
                                               ("audio", "room.wav", recording.getvalue(), "音频1", "声音参考")):
        v = wb.register_asset(pid,filename,data,"scene",kind=kind,episode_id=eid)
        entry["refs"].append(dict(assetVersionId=v["versionId"], label=label, kind=kind, role=role))
        entry["txt"]=entry["txt"].replace("【参考素材】",f"【参考素材】\n{label}：{role}。",1)
    vid, _ = env.save_adopt(eid,"prompts",prompts)
    env.review_segments(vid,["G01"])
    did = wb.deliver(eid,"trial","mixed-media",segments=["G01"])["deliveryId"]
    pr = wb.package_preview(did,["G01"])
    assert [r["kind"] for r in pr["segments"][0]["refs"]]==["image","video","audio"]
    packed = wb.package(did,["G01"],"mixed-export",expected_preview=pr["previewHash"])
    subprocess.run(["node","--import",str(studio/"node_modules/tsx/dist/loader.mjs"),str(Path(__file__).with_name("h3_import.mjs")),
                    str(studio),packed["path"],str(evidence)],check=True)


if __name__=="__main__":
    with TemporaryDirectory(prefix="adw-mixed-media-") as tmp:
        run(Path(tmp),Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
