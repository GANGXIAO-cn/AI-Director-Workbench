"""Run the two explicitly authorized frozen story inputs once; never retry automatically."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json, sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"server"))
from workbench.executors import CodexExecutor
from workbench.service import Workbench
from workbench.db import connect
def main(root):
    evidence=json.loads((root/"evidence.json").read_text())
    wb=Workbench(root/"data",{"codex":CodexExecutor()})
    def once(label):
        eid=evidence[label]["episode"]; rid=evidence[label]["storyInput"]
        manifest,_=wb.store.load_manifest("runs/"+rid+"/input-manifest.json")
        assert manifest["purpose"]=="create" and manifest["stage"]=="story" and manifest["episodeId"]==eid
        assert all(not i.get("binary") and i["role"] in {"method","request","project_settings","kickoff"} for i in manifest["inputs"])
        with connect(wb.db_path) as db:
            assert db.execute("SELECT status FROM run WHERE id=?",(rid,)).fetchone()[0]=="queued"
        wb.execute_run(rid)
        with connect(wb.db_path) as db:
            run=dict(db.execute("SELECT status,reason FROM run WHERE id=?",(rid,)).fetchone())
            v=db.execute("SELECT content FROM version WHERE source_run_id=?",(rid,)).fetchone()
        (root/("model-"+label+".json")).write_text(json.dumps(dict(run=rid,**run,content=json.loads(v[0]) if v else None),ensure_ascii=False,indent=2))
        print(label,rid,run,flush=True)
        return bool(v)
    with ThreadPoolExecutor(max_workers=2) as pool:
        return all(list(pool.map(once,["A","B"])))
if __name__=="__main__": sys.exit(0 if main(Path(sys.argv[1])) else 1)
