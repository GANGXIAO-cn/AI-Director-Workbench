"""Build a candidate pack in an isolated repository; leave installed skills and project defaults intact."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"methods/tools"))
from freeze_sections import build, PINS, VIDEO
from source_impact import compare

TITLE = "动作能量与反应"
REL = "references/camera-rhythm.md"
EXTRA = "记录接触前的位置和接触后的位移，保留动作结果可见的时窗。"


def prepare(directory):
    sources = directory/"sources"
    methods = directory/"methods"
    sources.mkdir()
    shutil.copytree(ROOT/"methods/short-film",methods/"short-film")
    for repo, pin in PINS.items():
        source = sources/repo
        subprocess.run(["git","clone","--quiet","--no-checkout",str(Path.home()/".codex/skills"/repo),source],check=True)
        subprocess.run(["git","-C",source,"checkout","--quiet","-b","verify-method-sync",pin],check=True)
    for version in ("v2","v3","v4","v5","v6","v7"):
        build("short-film@"+version,sources,methods/"short-film"/version)
    repo = sources/VIDEO
    file = repo/REL
    lines = file.read_text().splitlines(keepends=True)
    assert "在联合制作表原行内写清关键动作" in lines[29]
    lines[29] = lines[29].replace("在联合制作表原行内写清关键动作","在联合制作表内逐项写清关键动作") + EXTRA + "\n"
    lines[1:1] = ["\n","这段文件导语只用于检验后续小节的行号移动。\n"]
    file.write_text("".join(lines))
    subprocess.run(["git","-C",repo,"-c","user.name=Method Test","-c","user.email=method@localhost","commit","-qam","同步验证：导语增加两行，动作能量小节增加一行"],check=True)
    base = json.loads((methods/"short-film/v7/manifest.json").read_text())
    report = compare(base,VIDEO,repo,"HEAD")
    build("short-film@v8",sources,methods/"short-film/v8",{**PINS,VIDEO:report["target"]})
    return methods,report
