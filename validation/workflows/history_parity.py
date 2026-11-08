"""Compare three native historical import checks to fixed source CLIs on identical immutable copies."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import PIN, ROOT, SKILL

sys.path.insert(0, str(ROOT / "server"))
from workbench.history import MANIFEST, PAGE, PACK
from workbench.service import Workbench


def audit(directory):
    directory = directory.resolve()
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","templates","config.json"],check=True)
    wb = Workbench(directory/"archive", {})
    project = next((p["id"] for p in wb.list_projects() if p["config_id"]=="puggy"), None) or wb.create_project("puggy")["projectId"]
    entries = wb.historical_episodes(project)
    sys.path.insert(0,str(SKILL/"scripts"))
    from check_assets import verify as fixed_assets
    from readable_prompts import standard_file
    from validate_episode import v2_assets, validate_v2_segments, check_appearance
    from review_evidence import validate_review_evidence
    from baseline_provenance import baseline_warnings, scene_anchor_warnings
    from character_contract import check_contract
    from reference_depictions import check_depictions
    from prompt_review import verify as verify_prompt_review
    from shot_references import check_shot_references
    from shot_rhythm import analyse
    fixed = fixed_assets(standard_file().parent.parent)
    rows = []
    for entry in entries:
        receipt = wb.import_historical_episode(project, entry["folder"])
        verified = wb.historical_receipt(project, receipt["receipt"])
        assert verified["rechecked"] == receipt["checks"]
        assert verified["materialRechecked"] == receipt["materialChecks"] and verified["integrity"]["ok"]
        sandbox = directory / "copies" / entry["folder"]
        for key, digest in receipt["files"].items():
            path = sandbox/key
            path.parent.mkdir(parents=True,exist_ok=True)
            # The three fixed CLI entry points only read this private mirror.
            if path.exists():
                assert path.samefile(wb.store.blob_path(digest)), "使用新的验收副本目录"
            else:
                os.link(wb.store.blob_path(digest),path)
        settings = sandbox/"check-settings.json"
        settings.write_text(json.dumps(receipt["settings"],ensure_ascii=False))
        checks = {}
        for key, cmd, args in [("manifest","validate_episode", [sandbox/MANIFEST]),
                               ("delivery","check_delivery", [sandbox/MANIFEST, sandbox/PAGE]),
                               ("publish","check_publish_pack", [sandbox/PACK,"--settings",settings])]:
            oracle = subprocess.run([sys.executable,"-B",SKILL/"scripts"/(cmd+".py"),*args], capture_output=True,text=True,timeout=180)
            message = oracle.stderr.strip().removeprefix("页面检查失败：").removeprefix("清单检查失败：").removeprefix("发布包检查失败：")
            native = receipt["checks"][key]
            if oracle.returncode == 0:
                matched = native["state"] == "passed"
            elif native["rule"] == "missing_pack":
                matched = not (sandbox/PACK).exists() and "No such file" in message
            else:
                matched = native["state"] == "failed" and native["message"] in message
            checks[key] = dict(workbench=native, sourceOK=not oracle.returncode, sourceIssue=message if oracle.returncode else None,
                               matched=matched)
        data = json.loads((sandbox/MANIFEST).read_bytes())
        materials = {}
        for key, fn in [("assets",lambda:v2_assets(sandbox,data,fixed)),("prompts",lambda:validate_v2_segments(sandbox,data,fixed)),
                        ("cast",lambda:check_contract(sandbox,data)),("appearance",lambda:check_appearance(sandbox,data,fixed)),
                        ("depictions",lambda:check_depictions(sandbox,data)),
                        ("reviews",lambda:validate_review_evidence(sandbox/MANIFEST)),
                        ("promptReview",lambda:verify_prompt_review(sandbox/MANIFEST)),
                        ("shotReferences",lambda:check_shot_references(sandbox,data)),
                        ("rhythm",lambda:analyse(sandbox,data))]:
            native = verified["materialRechecked"].get(key,{})
            if native.get("state") not in ("passed","failed"):
                continue
            try:
                value = fn()
                message = None
            except (OSError,ValueError,KeyError,TypeError) as exc:
                message = str(exc)
            matched = (message is None and native["state"]=="passed" or
                       message is not None and native["state"]=="failed" and native["message"]==message)
            if message is None and native["state"] == "passed":
                if key in ("cast", "reviews", "rhythm", "shotReferences"):
                    matched = value == native["details"]
                elif key == "promptReview":
                    matched = dict(value, record=Path(value["record"]).relative_to(sandbox).as_posix()) == native["details"]
                elif key == "assets":
                    matched = len(value) == native["count"]
                elif key == "prompts":
                    matched = len(value[1]) == native["count"]
            materials[key]=dict(state=native["state"],nativeIssue=native["message"] if native["state"]=="failed" else None,
                                sourceIssue=message,matched=matched)
        if data.get("schema_version") == 2:
            for key, warnings in (("sceneAnchors", scene_anchor_warnings(data,sandbox)),
                                  ("sceneBaselines", baseline_warnings(data,standard_file().parent.parent))):
                native = verified["materialRechecked"][key]
                # Localized messages and mirrored paths may differ. Compare each warning's
                # rule, scene, asset(s) and request in order, retaining both full texts.
                def located(items):
                    return [{k:v for k,v in w.items() if k!="message"} for w in items]
                materials[key] = dict(state=native["state"],sourceWarnings=warnings,
                                      matched=native["state"]==("advisory" if warnings else "checked")
                                              and located(warnings)==located(native["warnings"]))
        rows.append(dict(episode=entry["folder"], receipt=receipt["receipt"], fingerprint=verified["fingerprint"],
                         copiedFiles=verified["files"], checks=checks, materials=materials, archive=verified["archive"],
                         integrity=verified["integrity"],workbenchMaterialChecks=verified["materialRechecked"]))
    totals = {key:dict(Counter(r["checks"][key]["workbench"]["rule"] for r in rows)) for key in ("manifest","delivery","publish")}
    parts=[v for row in rows for v in row["materials"].values()]
    all_hashes={digest for row in rows for digest in row["copiedFiles"].values()}
    return dict(componentChecks=len(parts), componentMatches=sum(v["matched"] for v in parts),
                copiedPaths=sum(len(row["copiedFiles"]) for row in rows),distinctLocalFiles=len(all_hashes),
                sourceCommit=PIN, archive=str(directory/"archive"), count=len(rows), totals=totals,
                matched=sum(all(c["matched"] for c in r["checks"].values()) for r in rows), episodes=rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    report = audit(args.directory or Path(tempfile.mkdtemp(prefix="adw-historical-import-")))
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(report["count"], report["matched"], report["totals"])
    raise SystemExit(0 if report["count"] and report["count"] == report["matched"] and report["componentChecks"]==report["componentMatches"] else 1)
