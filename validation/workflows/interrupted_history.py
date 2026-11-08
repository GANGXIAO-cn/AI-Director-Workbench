"""Recover a read-only SQLite snapshot of the two historical CLI replies in isolation."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"server"))
from workbench.service import Workbench

a, b = "569b1063-c2d2-47fe-8059-1cde2dd55c6e", "c0368046-4d90-49eb-8c2b-0ba36cebb0fb"
source = ROOT/"data/接手验收/原始资料本地对照-w7s_829k/data"
def snapshot(db):
    return {r[0]:dict(run=r[1],reason=r[2],task=r[3],diagnostic=r[4]) for r in db.execute(
        "SELECT r.id,r.status,r.reason,t.state,r.diagnostic FROM run r JOIN task t ON r.task_id=t.id WHERE r.id IN (?,?)",(a,b))}
def prepare(output):
    output.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect((source/"workbench.sqlite").as_uri()+"?mode=ro",uri=True) as original:
        before = snapshot(original)
        with sqlite3.connect(output/"workbench.sqlite") as dest:
            original.backup(dest)
    for directory in ("blobs","runs"):
        shutil.copytree(source/directory,output/directory)
    return before


if __name__=="__main__":
    output = Path(tempfile.mkdtemp(prefix="adw-recover-actual-"))
    before = prepare(output)
    wb = Workbench(output,{})
    with sqlite3.connect(wb.db_path) as copy:
        after = snapshot(copy)
        projects = copy.execute("SELECT DISTINCT project_id FROM task WHERE id IN (SELECT task_id FROM run WHERE id IN (?,?))",(a,b)).fetchall()
    records = [r for (pid,) in projects for task in wb.task_records(project_id=pid) for r in task["runs"] if r["id"] in {a,b}]
    hashes = {}
    for rid in (a,b):
        rel = Path("runs")/rid/"result.json"
        assert (source/rel).read_bytes()==(output/rel).read_bytes()
        hashes[rid] = sha256((output/rel).read_bytes()).hexdigest()
    assert before[a]["run"]=="running" and after[a]["run"]==after[a]["task"]=="failed"
    assert "adaptation" in after[a]["diagnostic"] and after[b]==before[b]
    assert {r["id"]:r["status"] for r in records}=={a:"failed",b:"succeeded"}
    out = dict(original=before,isolatedRecovery=after,originalReplyFingerprints=hashes,noExecutorConfigured=True)
    (ROOT/"修复验证_20261108/历史真实回包A与B中断恢复核对.json").write_text(json.dumps(out,indent=2,ensure_ascii=False)+"\n")
    print(after)
