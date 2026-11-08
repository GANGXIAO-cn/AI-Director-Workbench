"""Exercise per-episode identity, scoped comparison, and the native offline handoff."""
import json
from pathlib import Path
import sys
import tempfile
from hashlib import sha256
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "server/tests")]
from helpers import Env
from workbench import promptreceipt
from workbench.errors import Rejected


def verify(directory):
    env = Env(directory)
    wb = env.wb
    case = env.full_episode(segments={"G01": [8000], "G02": [7000]}, deliver=False)
    eid, pid, role = case["eid"], case["pid"], "guard"
    base = env.image(pid, eid, "值班员中性正面", "identity", rgb=(200,90,30))
    state = env.image(pid, eid, "值班员举伞", "state", rgb=(50,30,160))
    desc, note = "制服左襟有一枚银扣。", "举伞图的左襟银扣与独立基准相同。"
    wb.set_role_baseline(eid, role, base["versionId"], desc, "baseline")
    wb.set_cast(eid, "G01", role, "ok", state["versionId"], note=note)
    wb.set_cast(eid, "G02", role, "absent")
    first = env.prompts_for(case["plan"], state["versionId"], {"G01":"00:00.000-00:08.000 图片1 "+desc})
    with env.db() as db:
        assert any("本段开场" in s for s in wb._cast_prompt_errors(db,eid,"G01",first["segments"]["G01"]))
    wb.set_cast(eid,"G01",role,"ok",state["versionId"],note=note,opening_confirmed=True)
    try:
        wb.set_cast(eid,"G02",role,"ok",state["versionId"],note=note,opening_confirmed=True)
    except Rejected as exc:
        assert "不同生成段" in str(exc)
    else:
        raise AssertionError("沿用了上一段的身份状态图")
    try:
        wb.set_role_baseline(eid, "other", base["versionId"], desc, "other")
    except Rejected as exc:
        assert "共用" in str(exc)
    else:
        raise AssertionError("同图被登记到两个角色")
    # Explicitly choose a neutral image in the absent segment.
    wb.mark_image(eid, case["img"]["versionId"], "required", ["S02"])
    draft = wb.draft_prompts(eid)
    assert desc in draft["segments"]["G01"]["txt"] and desc not in draft["segments"]["G02"]["txt"]
    version, _ = env.save_adopt(eid, "prompts", draft, "with-cast")
    env.review_segments(version, ["G01","G02"])
    before = wb.prompt_review_status(version)
    def attachments(pass_name):
        run = wb.run_manifest(before["G01"][pass_name]["runId"])["manifest"]
        return [(e["role"],e["sha256"]) for e in run["inputs"] if e.get("binary")]
    assert attachments("coldRead") == [("image",state["sha256"])]
    assert attachments("comparison") == [("image",state["sha256"]),("cast_baseline_image",base["sha256"])]
    delivery = wb.deliver(eid, "full", "with-cast-delivery")
    root = Path(delivery["path"])
    manifest = json.loads((root/"episode.json").read_bytes())
    files = wb.delivery_view(delivery["deliveryId"])["files"]
    roster = manifest["workbench"]["page"]["roster"][role]
    assert roster["segments"] == ["G01"] and not roster["unconfirmed"]
    assert manifest["segments"][0]["references"][0]["asset_id"] == state["versionId"]
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]
    package = wb.package(delivery["deliveryId"], ["G01"], "cast-zip")
    with ZipFile(package["path"]) as z:
        h3 = json.loads(z.read("project.json"))
        picture, = h3["assets"]
        assert h3["segments"][0]["refs"] == [dict(assetId=state["versionId"], label="图片1")]
        assert picture["id"] == state["versionId"] and sha256(z.read(picture["path"])).hexdigest() == state["sha256"]
    manifest["workbench"]["page"]["bindings"][0]["note"] = "被替换的观察"
    try:
        promptreceipt.verify_package(root,manifest,files)
    except ValueError as exc:
        assert "角色绑定与送审登记不同" in str(exc)
    else:
        raise AssertionError("错误的身份观察进入交付")
    trial = wb.deliver(eid, "trial", "trial-absent", ["G02"])
    trial_manifest = json.loads((Path(trial["path"])/"episode.json").read_bytes())
    assert role not in trial_manifest["workbench"]["page"]["roster"]
    assert all(data != base["sha256"] for data in wb.delivery_view(trial["deliveryId"])["files"].values())
    wb.set_cast(eid, "G01", role, "ok", state["versionId"], note="另一次观看，制服银扣清楚。")
    after = wb.prompt_review_status(version)
    assert after["G01"]["coldRead"] == before["G01"]["coldRead"] and after["G01"]["comparison"]["state"] == "needed"
    assert after["G02"] == before["G02"] and wb.verify_delivery(delivery["deliveryId"])["ok"]
    return dict(role=role, coldRead=attachments("coldRead"), comparison=attachments("comparison"),
                actualH3Input=picture["sha256"], h3ImageCount=len(h3["assets"]), baseline=base["sha256"], fullSegments=roster["segments"],
                absentTrialRetainsBaseline=False, borrowedOpeningRejected=True, changedObservationRequires=["G01/comparison"],
                otherSegmentReviewUnchanged=True, previousDeliveryVerified=True)


def fixed_character(directory):
    env = Env(directory)
    wb = env.wb
    pid, eid = env.new_episode(config="puggy")
    env.adopt_story(eid)
    env.script(eid)
    env.save_adopt(eid, "plan", env.plan86({"G01":[8000]}))
    prod = json.loads((ROOT/"configs/projects/puggy.json").read_bytes())["production"]
    standard = json.loads((Path(prod["assetRoot"])/prod["characterStandard"]).read_bytes())["roles"]["milo"]
    version = wb.use_fixed_role(eid, "milo", "use-milo")["versionId"]
    desc = standard["video_zh"]
    wb.set_role_baseline(eid, "milo", version, desc, "fixed-baseline")
    state = env.image(pid,eid,"G01 首帧","state",rgb=(180,70,40))
    wb.set_cast(eid,"G01","milo","ok",state["versionId"],note="流程夹具，段首图与原图分开登记",opening_confirmed=True)
    draft = wb.draft_prompts(eid)
    entry = draft["segments"]["G01"]
    assert entry["txt"].count(desc) == 1 and "图片2："+desc in entry["txt"]
    assert [r["assetVersionId"] for r in entry["refs"]] == [state["versionId"],version]
    vid, _ = env.save_adopt(eid, "prompts", draft)
    env.review_segments(vid, ["G01"])
    trial = wb.deliver(eid,"trial","fixed",["G01"])
    assert wb.verify_delivery(trial["deliveryId"])["ok"]
    manifest = json.loads((Path(trial["path"])/"episode.json").read_bytes())
    role = manifest["workbench"]["page"]["roster"]["milo"]
    assert role["sha256"] == standard["sha256"]
    return dict(role="milo", baseline=role["sha256"], roleVersion=standard["role_version"],
                shortTextOccurrences=entry["txt"].count(desc), inputLabels=[r["label"] for r in entry["refs"]],
                dynamicIdentity="not_tested")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="adw-cast-") as folder:
        report = verify(Path(folder))
        report["fixedProject"] = fixed_character(Path(folder)/"fixed")
    Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(report)
