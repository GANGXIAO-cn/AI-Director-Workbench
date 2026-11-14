import json
from pathlib import Path
import subprocess

import pytest

from helpers import Env
from workbench import methodpack
from workbench.errors import Conflict, Rejected


def test_ambiguous_source_boundary_requires_exact_target_selection(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/"validation/workflows"))
    from method_sync import prepare, VIDEO, PINS, TITLE, REL, EXTRA
    from freeze_sections import build
    from source_impact import compare
    if not all((Path.home()/".codex/skills"/repo/".git").exists() for repo in PINS):
        pytest.skip("fixed skill repositories unavailable")
    methods, report = prepare(tmp_path)
    src = tmp_path/"sources"
    repo = src/VIDEO
    text = repo/REL
    lines = text.read_text().splitlines(keepends=True)
    lines.insert(37,"选段末尾新增的镜头检查。\n")
    text.write_text("".join(lines))
    subprocess.run(["git","-C",repo,"-c","user.name=Method Test","-c","user.email=method@localhost",
                    "commit","-qam","边界新增一句"],check=True)
    diff=compare(json.loads((methods/"short-film/v8/manifest.json").read_text()),VIDEO,repo,"HEAD")
    row=next(r for r in diff["changes"][0]["ranges"] if r["section"]==TITLE)
    assert row["review"] and row["lines"] is None
    destination=methods/"short-film/v9"
    pins={**PINS,VIDEO:diff["target"]}
    with pytest.raises(ValueError,match="选段边界"):
        build("short-film@v9",src,destination,pins)
    assert not destination.exists()
    reviewed={TITLE:dict(lines="24-38",commit=report["target"],path=REL)}
    with pytest.raises(ValueError,match="目标提交"):
        build("short-film@v9",src,destination,pins,reviewed)
    reviewed[TITLE]["commit"]=pins[VIDEO]
    build("short-film@v9",src,destination,pins,reviewed)
    build("short-film@v9",src,destination,pins,reviewed)
    content=(destination/"sections"/(TITLE+".md")).read_text()
    assert "选段末尾新增的镜头检查。" in content and EXTRA in content
    reviewed[TITLE]["lines"]="24-37"
    with pytest.raises(ValueError,match="existing version differs"):
        build("short-film@v9",src,destination,pins,reviewed)
    assert (destination/"sections"/(TITLE+".md")).read_text()==content
    # Coverage must use the candidate commit as well as its updated line numbers.
    output=tmp_path/"coverage.json"
    subprocess.run(["python3","-B",root/"methods/tools/coverage.py",output,"--ref","short-film@v9",
                    "--pack-dir",destination,"--source-root",src],check=True)
    coverage=json.loads(output.read_bytes())
    source=next(r for r in coverage["files"] if r["path"]==REL)
    assert source["commit"]==pins[VIDEO] and coverage["source"]==pins


def test_pack_comparison_uses_project_profile_and_preserves_adoption_on_conflict(tmp_path):
    wb=Env(tmp_path).wb
    plain=wb.create_project("_template")["projectId"]
    branded=wb.create_project("puggy")["projectId"]
    def changed(pid):
        return wb.compare_packs("short-film@v2","short-film@v3",pid)["sections"]
    assert all(r["scope"]=="shared" for r in changed(plain))
    assert any(r["scope"]=="puggy" and r["key"]=="商品入景规划模板" for r in changed(branded))
    with pytest.raises(Conflict):
        wb.adopt_pack(plain,"short-film@v1","switch",expected_ref="short-film@v2")
    assert wb.project_view(plain)["method_pack"]=="unified-film@draft"


def test_pack_stage_integrity_and_frozen_read_after_source_is_offline(tmp_path, monkeypatch):
    import shutil
    env=Env(tmp_path)
    wb=env.wb
    _,eid=env.new_episode()
    task=wb.create_task(eid,"story","保留方法")
    original=wb.run_methods(task["runId"])
    sandbox=tmp_path/"methods"
    pack=sandbox/"short-film/v3"
    shutil.copytree(methodpack.pack_dir("short-film@v3"),pack)
    monkeypatch.setattr(methodpack,"METHODS",sandbox)
    damaged=sandbox/"short-film/v9"
    damaged.mkdir()
    (damaged/"manifest.json").write_text("unfinished")
    assert wb.pack_versions()==["unified-film@draft"]
    assert wb.regress_pack("short-film@v3")["ok"]
    with pytest.raises(Rejected,match="读取"):
        wb.regress_pack("short-film@v9")
    item=pack/"manifest.json"
    manifest=json.loads(item.read_bytes())
    manifest["stages"]["story"].append("缺失小节")
    item.write_text(json.dumps(manifest))
    with pytest.raises(Rejected,match="缺失或重复"):
        wb.regress_pack("short-film@v3")
    pack.rename(tmp_path/"offline-pack")
    assert wb.run_methods(task["runId"])==original
    wb.store.blob_path(original["sections"][0]["sha256"]).write_bytes(b"tampered sample")
    with pytest.raises(Rejected,match="指纹核对"):
        wb.run_methods(task["runId"])
