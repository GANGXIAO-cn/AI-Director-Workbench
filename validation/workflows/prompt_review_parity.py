"""Replay fixed source review records, and run the source checker on new native deliveries."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import ROOT, SKILL, PIN
sys.path[:0] = [str(ROOT/"server"), str(ROOT/"server/tests"), str(SKILL/"tests")]
from helpers import Env
from workbench.archivefiles import ArchiveFiles, capture
from workbench import promptreceipt


def outcome(fn, *args):
    try:
        value = fn(*args)
        return dict(segments=value["segments"])
    except ValueError as err:
        return dict(issue=str(err))


def compare():
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","tests"], check=True)
    from test_prompt_review import PromptReviewTest
    from prompt_review import verify
    from review_records import records

    directory = Path(tempfile.mkdtemp(prefix="adw-prompt-review-")).resolve()
    env = Env(directory/"workbench")
    case = env.full_episode()
    partial = env.wb.deliver(case["eid"],"trial","review-one",["G02"])
    packages = {}
    for run in (case["delivery"],partial):
        root = Path(run["path"])
        checked = verify(root/"episode.json")
        assert checked["segments"] == run["segments"] and env.wb.verify_delivery(run["deliveryId"])["ok"]
        packages[run["scope"]] = dict(segments=checked["segments"], recordSHA256=sha256((root/"review.md").read_bytes()).hexdigest())

    wb = env.wb
    book = next(item for item in wb.browse_scripts(query="搜神记")["rows"] if item["title"] == "搜神记")
    excerpt = wb.script_excerpt(book["path"], 2400)
    wb.save_step_config(case["eid"], "prompts", keep="维持门口侧光", mode="match", references=[excerpt])
    wb.save_palettes(case["pid"], ["001"])
    status = wb.prompt_review_status(case["prompts"])["G02"]
    assert status["coldRead"]["state"] == "passed" and status["comparison"]["state"] == "needed"
    cold = wb.run_manifest(status["coldRead"]["runId"])["manifest"]
    assert not any(e["role"] in ("citation","visual_reference","step_requirements") for e in cold["inputs"])
    review = wb.start_prompt_review(case["prompts"], "G02", "comparison", "comparison-with-references")
    env.drain(review["runId"])
    frozen = wb.run_manifest(review["runId"])["manifest"]
    reference_run = wb.deliver(case["eid"], "trial", "cited-trial", ["G02"])
    package = Path(reference_run["path"])
    references = json.loads((package/"审核对照/制作要求.json").read_bytes())
    selected = {row["role"]:row for row in references if row["role"] in ("step_requirements","citation","visual_reference")}
    assert set(selected) == {"step_requirements","citation","visual_reference"}
    assert json.loads(selected["citation"]["text"])["text"] == excerpt["text"]
    assert json.loads(selected["visual_reference"]["text"])["id"] == "001"
    for role, row in selected.items():
        actual, = [e for e in frozen["inputs"] if e["role"] == role]
        assert sha256(row["text"].encode()).hexdigest() == actual["sha256"] == row["sha256"]
    assert verify(package/"episode.json")["segments"] == ["G02"]
    wb.set_episode_palettes(case["eid"],"prompts","off")
    wb.save_step_config(case["eid"],"prompts", mode="off")
    assert wb.prompt_review_status(case["prompts"])["G02"]["comparison"]["state"] == "needed"
    assert wb.verify_delivery(reference_run["deliveryId"])["ok"]
    reference_evidence = {role:dict(name=r["name"], sha256=r["sha256"]) for role,r in selected.items()}

    from workbench.executors.fake import review
    env.claude.script.append(lambda req: review(req, "return", [dict(category="clarity",reason="停顿略长")]))
    blocked = wb.start_prompt_review(case["prompts"], "G02", "comparison", "waiver-check")
    env.drain(blocked["runId"])
    status = wb.prompt_review_status(case["prompts"])["G02"]
    issue, = status["comparison"]["issues"]
    assert not status["ok"] and not issue["blocking"]
    wb.waive_segment_issue(issue["id"], "保留反应停顿", "user-directs")
    done = wb.deliver(case["eid"], "trial", "waived-trial", ["G02"])
    waived = Path(done["path"])
    data = json.loads((waived/"episode.json").read_bytes())
    listed = wb.delivery_view(done["deliveryId"])["files"]
    native = promptreceipt.verify_package(waived, data, listed)
    original = outcome(verify, waived/"episode.json")
    assert native["status"] == "user_waived" and native["waived"] == ["G02"]
    assert "审核退回" in original["issue"] and wb.verify_delivery(done["deliveryId"])["ok"]
    del data["workbench"]["promptReviewWaivers"]
    assert outcome(promptreceipt.verify_package, waived, data, listed) == original
    waiver_example = dict(source=original, workbench=native, withoutRegisteredWaiver=original)

    checks = {}
    for name in ("valid","table","extra_stage","schema","sources","missing_plan","duplicate_segment","upload_order",
                 "unknown_record","duplicate_record","change_txt","change_image","swap","remove","add","new_id_same_bytes",
                 "bad_images_json","no_obs","returned","pending","open","missing_record","orphan","duplicate_column"):
        fixture = PromptReviewTest()
        fixture.setUp()
        root, data, rec = fixture.root, fixture.data, fixture.record
        first, second = data["segments"]
        refs = first["references"]
        if name == "table":
            fields = ["images","verdict","comparison","scope","issues","cold","txt_sha256"]
            rows = [fields, ["---"] * len(fields), *[[r[k] for k in fields] for r in records(rec)]]
            rec = "\n".join("|" + "|".join(row) + "|" for row in rows)
        elif name == "extra_stage": rec += "\n\n|阶段|结论|\n|---|---|\n|故事|通过|\n"
        elif name == "schema": data["schema_version"] = 1
        elif name == "sources": data["prompt_review"] = None
        elif name == "missing_plan": data["prompt_review"]["plan"] = "absent"
        elif name == "duplicate_segment": second["id"] = first["id"]
        elif name == "upload_order": refs[0]["upload_order"] = 9
        elif name == "unknown_record": rec = rec.replace("覆盖段：a","覆盖段：X")
        elif name == "duplicate_record": rec = rec.replace("覆盖段：b","覆盖段：a")
        elif name == "change_txt": (root/first["prompt"]).write_bytes(b"changed prompt")
        elif name == "change_image": (root/"image-a").write_bytes(b"changed image")
        elif name == "swap":
            refs.reverse()
            for i, ref in enumerate(refs,1):
                ref.update(upload_order=i,label=f"图片{i}")
        elif name == "remove": refs.pop()
        elif name == "add": refs.append(dict(refs[0], upload_order=3, label="图片3"))
        elif name == "new_id_same_bytes":
            data["assets"].append(dict(data["assets"][0], id="new-id"))
            refs[0]["asset_id"] = "new-id"
        elif name == "bad_images_json": rec = rec.replace("输入图片：[","输入图片：X[",1)
        elif name == "no_obs": rec = rec.replace("冷读观察：测试冷读观察","冷读观察：",1)
        elif name == "returned": rec = rec.replace("结论：通过","结论：退回",1)
        elif name == "pending": rec = fixture.packet
        elif name == "open": rec = rec.replace("问题处理：无","问题处理：尚未解决",1)
        elif name == "missing_record": rec = rec.split("\n\n")[0]
        elif name == "orphan": rec += "\n结论：退回"
        elif name == "duplicate_column": rec = "|段号|结论|结论|\n|---|---|---|\n|a|退回|通过|"
        (root/"review.md").write_text(rec)
        fixture.write()
        files, meta = capture(root, {"episode.json": fixture.manifest.read_bytes()}, env.wb.store, [], root.parent, {})
        archive = ArchiveFiles(files, meta, env.wb.store)
        original = outcome(verify, fixture.manifest)
        native = outcome(promptreceipt.verify, data, archive.read, archive.identity_bytes)
        assert original == native, (name, original, native)
        good = name in ("valid","table","extra_stage")
        assert good == ("segments" in native), (name,native)
        checks[name] = dict(matchedRule=True, sourceOK=good, workbenchOK=good, **native)
    return dict(source=PIN, packages=packages, comparisonReferences=reference_evidence, userWaiver=waiver_example, cases=checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = compare()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(len(report["cases"]), "matched", report["packages"])
