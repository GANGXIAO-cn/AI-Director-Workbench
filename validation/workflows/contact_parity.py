"""Compare all registered historical contact-sheet outcomes with the pinned production tool."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

from historical_baseline import ROOT, SKILL, PIN
sys.path[:0] = [str(ROOT/"server"),str(SKILL/"scripts")]
from workbench.errors import Rejected
from workbench.service import Workbench
import scene_contact_sheet as source


def normalized(result):
    return dict(unmapped=result["unmapped_asset_ids"],review=result["review_status"],scenes=[
        dict(scene=s["scene_id"],sources=[(r["id"],r["sha256"]) for r in s["sources"]],
             references=[(r["id"],r["reference_kind"],r["role"],r["sha256"]) for r in s["role_references"]],
             warnings=[("未登记服装" if "服装参考" in warning and "未登记" in warning else warning)
                       for warning in s["reference_warnings"]], pages=len(s["sheets"])) for s in result["scenes"]])


def run(directory):
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","tests"],check=True)
    wb=Workbench(directory/"archive",{})
    pid=wb.create_project("puggy")["projectId"]
    root=wb._history_root(pid)
    rows=[]
    for entry in wb.historical_episodes(pid):
        name=entry["folder"]
        receipt=wb.import_historical_episode(pid,name)
        src=dst=None
        try:
            src=normalized(source.build(root/name/"episode.json",directory/name))
        except (ValueError,OSError) as exc:
            src_error=str(exc)
        try:
            dst=normalized(wb.historical_contact_sheet(pid,receipt["receipt"]))
        except Rejected as exc:
            dst_error=str(exc)
        if src is not None and dst is not None:
            rows.append(dict(episode=name,matched=src==dst,source=src,workbench=dst))
        else:
            # Keep the original diagnostic; the contact tool adds guidance after its rule name.
            left=src_error if src is None else "accepted"
            right=dst_error if dst is None else "accepted"
            rows.append(dict(episode=name,matched=left.split("；")[0]==right.split("；")[0],sourceError=left,workbenchError=right))
        print(name,rows[-1]["matched"],flush=True)
    return dict(sourceCommit=PIN,total=len(rows),matched=sum(x["matched"] for x in rows),
                scenesCompared=sum(len(x["source"]["scenes"]) for x in rows if "source" in x),
                pagesCompared=sum(s["pages"] for x in rows if "source" in x for s in x["source"]["scenes"]),
                withSheets=sum("source" in x for x in rows),records=rows)


if __name__=="__main__":
    directory=Path(tempfile.mkdtemp(prefix="adw-contact-parity-"))
    # Share blocks with the previous validation fixture, keeping that evidence immutable.
    prior=Path(json.loads((ROOT/"修复验证_20261108/历史集登记材料与独立检查对照.json").read_bytes())["archive"])
    if prior.name=="archive" and prior.parent.name.startswith("adw-historical-import-") and prior.is_dir() and sys.platform=="darwin":
        subprocess.run(["cp","-cR",str(prior),str(directory/"archive")],check=True)
    report=run(directory)
    Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print({k:v for k,v in report.items() if k!="records"})
    raise SystemExit(0 if report["total"]==report["matched"]==49 else 1)
