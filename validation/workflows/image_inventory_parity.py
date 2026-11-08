"""Compare scoped image choices and actual upload segments for each historical episode."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import ROOT, SKILL, PIN
sys.path[:0] = [str(ROOT/"server"),str(SKILL/"scripts")]
import image_selection as original
from workbench.service import Workbench


def compare(wb, pid, folder):
    source = wb._history_root(pid)/folder
    receipt = wb.import_historical_episode(pid,folder,include_image_inventory=True)
    check = receipt["materialChecks"]["imageInventory"]
    out = dict(episode=folder,receipt=receipt["receipt"])
    try:
        data,groups,used,_,_ = original.state(source)
        excluded = original.inventory_details(source)[1]
    except (ValueError,OSError,KeyError) as exc:
        reason = str(exc)
        out.update(sourceError=reason,workbenchError=check["message"],matched=check["state"]=="failed" and
                   (reason==check["message"] or "schema_version" in reason and "schema_version" in check["message"]))
        return out
    def normalized(row, paths, segments):
        return dict(id=row["id"],sha256=row["sha256"],paths=sorted(row["paths"]),current=sorted(paths),
                    segments=sorted(segments),priority=row.get("priority","normal"),targets=row.get("target_segments",[]),
                    selectedBy=row.get("selected_by",""),reason=row.get("selection_reason",""),
                    fitness=row.get("fitness","not_tested"),quality=row.get("quality","not_tested"),
                    limitations=row.get("limitations",""))
    left = sorted([normalized(r,groups.get(r["sha256"],[]),used.get(r["sha256"],set())) for r in data["images"]],key=lambda r:r["sha256"])
    detail = check.get("details",{})
    right = sorted([normalized(r,r["currentPaths"],r["usedIn"]) for r in detail.get("images",[])],key=lambda r:r["sha256"])
    try:
        original.check_selection(source)
        source_problems = []
    except ValueError as exc:
        source_problems = str(exc).splitlines()
    # Source appends the requested segment list to missing-coverage diagnoses.
    problems = [p.split("未落实到要求的正式输入段")[0]+"未落实到要求的正式输入段" if "未落实到要求的正式输入段" in p else p for p in source_problems]
    passed = (left==right and excluded==detail.get("excluded_projects") and len(groups)==detail.get("current_images")
              and problems==detail.get("problems") and (not problems)==(check["state"]=="passed"))
    expected_reports = [sha256(path.read_bytes()).hexdigest() for path in original.reference_inputs(source)[1]]
    reports = [r for r in receipt["archive"].get("referenceReports",[]) if not r.get("error")]
    recorded = [r["resources"]["report.html"] for r in reports]
    out["reports"] = [dict(source=r["source"],html=r["resources"]["report.html"],markdown=r["resources"].get("report.md"),
                           images=sum(name.startswith(("frames/","evidence/")) for name in r["resources"]),missing=r["missing"]) for r in reports]
    passed = passed and expected_reports == recorded and all(not r["missing"] for r in reports)
    out.update(matched=passed,images=right,currentCount=len(groups),excludedProjects=excluded,problems=source_problems)
    if not passed:
        out.update(source=left,workbenchMessage=check["message"],workbenchExcluded=detail.get("excluded_projects"))
    return out


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    parser.add_argument("--directory",type=Path)
    args = parser.parse_args()
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts"],check=True)
    directory = args.directory or Path(tempfile.mkdtemp(prefix="adw-image-inventory-"))
    wb = Workbench(directory/"archive",{})
    pid = wb.create_project("puggy","历史图片盘点",command_id="image-inventory-project")["projectId"]
    result = []
    for item in wb.historical_episodes(pid):
        row = compare(wb,pid,item["folder"])
        print(row["episode"],row["matched"],flush=True)
        result.append(row)
    counts = Counter()
    counts.update(episodes=len(result),matched=sum(r["matched"] for r in result))
    for r in result:
        if "images" in r:
            counts.update(applicable=1,currentVersions=r["currentCount"],historicalOnly=sum(not i["current"] for i in r["images"]),
                          excludedFolders=len(r["excludedProjects"]),required=sum(i["priority"]=="required" for i in r["images"]))
    args.output.write_text(json.dumps(dict(sourceCommit=PIN,counts=counts,results=result),ensure_ascii=False,indent=2)+"\n")
    print(counts,flush=True)
    if counts["matched"]!=counts["episodes"] or counts["episodes"]!=49:
        raise SystemExit(1)
