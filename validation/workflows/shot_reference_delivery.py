"""Compare an actual workbench shipment with the pinned per-shot checker and exercise corrections."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import ROOT,PIN,SKILL
sys.path[:0] = [str(ROOT/"server"),str(ROOT/"server/tests")]
from helpers import Env
from workbench import rhythm, shotrefs, promptreceipt
from workbench.errors import Rejected


def run(tmp):
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","tests","templates"],check=True)
    env = Env(tmp)
    wb = env.wb
    sample = env.full_episode(segments={"G01":[3000,3000,3000]},deliver=False)
    eid, vid = sample["eid"],sample["img"]["versionId"]
    asset = wb.asset_version(vid)
    plan = sample["plan"]
    rows = rhythm.plan_rows(plan)
    for i,r in enumerate(rows):
        r[shotrefs.ROLES] = "阿福" if i==2 else "无"
        r[shotrefs.PROPS] = ["crate=空箱","crate=装满","crate=装满"][i]
    env.save_adopt(eid,"plan",dict(rhythmTable=rhythm.markdown(rows)),"scope-board")
    wb.set_cast(eid,"G01","阿福","ok",asset_version_id=vid)
    pv = sample["prompts"]
    def observe(base,shots,props=None,conflicts=None,combined=False,other=None):
        return wb.save_shot_observation(base,"G01",vid,asset["sha256"],shots,"阿福正面站立",props or {},
                       conflicts or [],combined,other)["versionId"]
    def errors(version):
        return wb.adoption_gate(version)["formatErrors"]
    assert any("实际看图记录" in e for e in errors(pv)),errors(pv)
    wrong = observe(pv,["S01","S02","S03"])
    assert any("身份图作用镜头与出镜不符" in e for e in errors(wrong)),errors(wrong)
    blocked = observe(pv,["S03"],conflicts=["道具不是现时状态"])
    assert any("图片仍有冲突" in e for e in errors(blocked))
    crate = observe(pv,["S03"],props={"crate":"装满"})
    assert any("与本段分镜用途或状态冲突" in e for e in errors(crate))
    vd = observe(pv,["S03"])
    assert not errors(vd),errors(vd)
    # A combined reference may document an appearance-only prop in a separate shot.
    combined = observe(vd,["S01","S03"],{"crate":"仅外观"},combined=True,other={"S01":"crate"})
    assert not errors(combined),errors(combined)
    art = env.art(eid,"prompts")
    wb.adopt(art["id"],vd,art["adopted_version_id"],"adopt-scoped")
    env.review_segments(vd,["G01"],"actual-observation")
    result = wb.deliver(eid,"trial","scoped",["G01"])
    root=Path(result["path"])
    main=json.loads((root/"episode.json").read_bytes())
    path=root/main["workbench"]["shotReferencePackets"]["G01"]
    external=subprocess.run([sys.executable,"-B",SKILL/"scripts/shot_references.py",path],capture_output=True,text=True,check=True)
    source=json.loads(external.stdout)
    assert source["status"]=="passed" and source["shots"]==3
    assert main["segments"][0]["references"][0]["shots"]==["S03"]
    assert "作用镜头：S03" in (root/"00_图文对照.html").read_text()
    assert wb.verify_delivery(result["deliveryId"])["ok"]
    scope=wb.prompt_review_status(combined)["G01"]
    assert scope["coldRead"]["state"]=="passed" and scope["comparison"]["state"]=="needed",scope
    listing=json.loads((root/"manifest.json").read_bytes())["files"]
    (path.parent/"参考1看图记录.txt").write_text("另一个版本的观察")
    try:
        promptreceipt.verify_package(root,main,listing)
        raise AssertionError("changed evidence passed")
    except ValueError as exc:
        assert "实际送审记录不一致" in str(exc),exc
    assert not wb.verify_delivery(result["deliveryId"])["ok"]
    return dict(sourceCommit=PIN,source=source,referenceShots=["S03"],wrongScope=errors(wrong),
                unresolvedConflict=errors(blocked),changingProp=errors(crate),combinedReferencePassed=True,
                coldReviewReusable=True,comparisonNeedsNewObservation=True,changedEvidenceRejected=True)


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="adw-shot-reference-") as tmp:
        report=run(Path(tmp))
    Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(report["source"])
