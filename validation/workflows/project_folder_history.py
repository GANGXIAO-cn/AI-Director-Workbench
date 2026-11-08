"""Move one real historical receipt into a standalone directory and replay it on a fresh host."""
import json
from pathlib import Path
import sys
import tempfile

from historical_baseline import ROOT
sys.path.insert(0,str(ROOT/"server"))
from workbench.folderserver import FolderWorkbench, inspect_folder
from workbench.service import Workbench


def verify(root):
    source = Workbench(root/"source",{})
    pid = source.create_project("puggy")["projectId"]
    source.create_project("_template","另一个项目")
    name = "2026-11-03-拍照"
    receipt = source.import_historical_episode(pid,name)["receipt"]
    expected = source.historical_receipt(pid,receipt)
    sheets = source.historical_contact_sheet(pid,receipt)
    out = root/"独立项目"
    info = source.export_project_folder(pid,str(out))
    moved = out.rename(root/"另一个位置")
    assert inspect_folder(moved)==pid
    fresh = Workbench(root/"新工作台",{})
    opened = FolderWorkbench(moved,fresh)
    assert [p["id"] for p in opened.list_projects()]==[pid] and fresh.list_projects()==[]
    replay = opened.historical_receipt(pid,receipt)
    assert replay==expected and replay["integrity"]["ok"]
    current = opened.historical_contact_sheet(pid,receipt)
    assert current==sheets
    for scene in current["scenes"]:
        for item in scene["sources"] + scene["role_references"] + scene["pages"]:
            assert opened.store.read_blob(item["sha256"])==source.store.read_blob(item["sha256"])
    return dict(episode=name,project=pid,receipt=receipt,files=info["files"],registeredFiles=len(expected["files"]),
                checkedHashes=replay["integrity"]["checked"],materialChecks=replay["materialRechecked"],
                checks=replay["rechecked"],scenes=[dict(scene=s["scene_id"],images=len(s["sources"]),
                    roles=[r["role"] for r in s["role_references"]]) for s in current["scenes"]],
                sameReceipt=True,sameContactSheets=True)


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="adw-folder-history-") as tmp:
        result=verify(Path(tmp))
    Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print({k:v for k,v in result.items() if k not in ("checks","materialChecks","scenes")})
