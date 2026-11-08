"""Compare required-image coverage on an actual two-segment static production."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from historical_baseline import PIN, ROOT, SKILL
sys.path[:0] = [str(ROOT/"server"), str(ROOT/"server/tests"), str(SKILL/"scripts")]
from helpers import Env
import image_selection as source
from workbench.errors import Rejected


def run(tmp):
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts/image_selection.py"],check=True)
    env = Env(tmp)
    wb = env.wb
    sample = env.full_episode(segments={"G01":[8000],"G02":[7000]}, deliver=False)
    eid, pid = sample["eid"], sample["pid"]
    first = sample["img"]["versionId"]
    second = env.image(pid,eid,name="替代参考",rgb=(28,91,12))["versionId"]
    alias = env.image(pid,eid,name="同字节的另一次登记")["versionId"]
    unused = env.image(pid,eid,name="替代参考",rgb=(100,31,90))["versionId"]
    pv,_ = env.save_adopt(eid,"prompts",env.prompts_for(sample["plan"], {"G01":first,"G02":second}),"two-images")
    env.review_segments(pv, ["G01","G02"])
    base = wb.deliver(eid,"full","base")
    requests = []
    def compare(name, vid, targets, fitness, limitations, passed):
        root = tmp/name
        shutil.copytree(base["path"], root)
        sha = wb.asset_version(vid)["sha256"]
        row = next(r for r in source.scan(root)["images"] if r["sha256"] == sha)
        reason = "原图留在指定镜头"
        source.mark_required(root,row["id"],targets,reason,"user")
        ledger = root/source.LEDGER
        values = json.loads(ledger.read_text())
        values["images"][0].update(fitness=fitness, limitations=limitations)
        ledger.write_text(json.dumps(values))
        try:
            original = source.check_selection(root)
            original_ok = True
        except ValueError as exc:
            original = str(exc)
            original_ok = False
        try:
            wb.mark_image(eid,vid,"required",requirement=dict(reason=reason,targetSegments=targets,
                          fitness=fitness,limitations=limitations))
            problems = wb.delivery_problems(eid)[0]
            found = [p for p in problems if p["stage"]=="assets" and "必用" in p["problem"]]
            actual_ok = not found
            message = [p["problem"] for p in found]
        except Rejected as exc:
            actual_ok = False
            message = [str(exc)]
        assert passed == actual_ok == original_ok,(name,message,original)
        requests.append(dict(case=name, targets=targets, sha256=sha, workbenchOK=actual_ok,
                             sourceOK=original_ok,source=original,workbench=message))
        wb.mark_image(eid,vid,"required",enabled=False)
    compare("实际指定段",first,["G01"],"pass","",True)
    compare("错段",first,["G02"],"pass","",False)
    compare("多段漏引",first,["G01","G02"],"not_tested","",False)
    compare("同名异图",unused,[],"not_tested","",False)
    compare("字节相同重登记",alias,["G01"],"pass","",True)
    compare("用户保留用途受阻图",first,["G01"],"blocked","只用于背影",True)
    compare("受限图缺理由",first,["G01"],"limited","",False)

    request = dict(reason="保留第一段原图",targetSegments=["G01"],fitness="limited",limitations="只用于背影")
    wb.mark_image(eid, first, "required", requirement=request)
    status = wb.prompt_review_status(pv)
    assert status["G01"]["comparison"]["state"]=="needed" and status["G01"]["coldRead"]["state"]=="passed"
    assert status["G02"]["comparison"]["state"]=="passed"
    run_id = wb.start_prompt_review(pv,"G01","comparison","new-requirement")["runId"]
    inputs = wb.run_manifest(run_id)["manifest"]["inputs"]
    reviewed, = [json.loads(wb.store.read_blob(i["sha256"])) for i in inputs if i["role"]=="plan_segment"]
    assert reviewed["requiredImages"][0]["limitations"] == "只用于背影"
    env.drain(run_id)
    good = wb.deliver(eid,"full","after")
    snapshot = json.loads((Path(good["path"])/"episode.json").read_text())["workbench"]["requiredImages"]
    assert snapshot[0]["usedHere"]==["G01"] and snapshot[0]["sha256"]==wb.asset_version(first)["sha256"]
    assert "只用于背影" in Path(good["html"]).read_text()
    # A second copy with different bytes under the same filename cannot satisfy the original version.
    # Modifying only the saved purpose must also disagree with the actual comparison run.
    from workbench import promptreceipt
    frozen = json.loads((Path(good["path"])/"episode.json").read_text())
    frozen["workbench"]["requiredImages"][0]["limitations"] = "已被改写"
    listing = json.loads((Path(good["path"])/"manifest.json").read_text())["files"]
    try:
        promptreceipt.verify_package(Path(good["path"]),frozen,listing)
        raise AssertionError("input mismatch was accepted")
    except ValueError as exc:
        assert "第二遍实际送审" in str(exc)
    wb.mark_image(eid,first,"required",requirement=dict(request, targetSegments=["G02"]))
    try:
        wb.deliver(eid,"full","wrong")
        raise AssertionError("wrong target shipped")
    except Rejected as exc:
        assert any(p["segment"]=="G02" and "必用图未落实" in p["problem"] for p in exc.detail)
    wb.mark_image(eid,first,"required",enabled=False)
    wb.mark_image(eid,unused,"required",requirement=request)
    part = wb.deliver(eid,"trial","only-second",["G02"])
    assert json.loads((Path(part["path"])/"episode.json").read_text())["workbench"]["requiredImages"] == []
    assert wb.verify_delivery(good["deliveryId"])["ok"] and wb.verify_delivery(part["deliveryId"])["ok"]
    corrupt_root = tmp/"原文件被覆盖"
    shutil.copytree(base["path"], corrupt_root)
    sha = wb.asset_version(unused)["sha256"]
    img = next(r for r in source.scan(corrupt_root)["images"] if r["sha256"]==sha)
    source.mark_required(corrupt_root,img["id"],["G01"],"保留此版","user")
    for path in img["paths"]:
        (corrupt_root/path).write_bytes(wb.asset_bytes(second))
    try:
        source.check_selection(corrupt_root)
        raise AssertionError("missing original accepted")
    except ValueError as exc:
        original = str(exc)
        assert "必用原版本不存在" in original
    wb.store.blob_path(sha).write_bytes(wb.asset_bytes(second))
    broken = wb.delivery_problems(eid)[0]
    assert any("必用原版本不存在" in p["problem"] for p in broken)
    requests.append(dict(case="覆盖必用图的原始字节",sourceOK=False,workbenchOK=False,source=original,
                         workbench=[p["problem"] for p in broken if "必用" in p["problem"]]))
    return dict(sourceCommit=PIN, comparisons=requests, frozenSelection=snapshot,
                newComparisonReads=reviewed["requiredImages"],otherSegmentReviewReused=True,
                trialSegments=part["segments"],oldPackageVerifiedAfterEdit=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="adw-required-") as folder:
        result = run(Path(folder))
    Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(len(result["comparisons"]), "matching image decisions; validated review scopes and delivery snapshots")
