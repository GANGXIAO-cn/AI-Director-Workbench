"""Recheck frozen historical pages and their original directory mappings without modifying either."""
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import subprocess

from historical_baseline import ROOT, SKILL, PIN

sys.path[:0] = [str(ROOT/"server"),str(SKILL/"scripts")]
from workbench.archivefiles import ArchiveFiles
from workbench.archivepage import PAGE, checked_content
from workbench import archivechecks, shotrefs, rhythm
from workbench.storage import Store


def main():
    evidence = ROOT/"修复验证_20261108"
    inventory = json.loads((evidence/"历史集登记材料与独立检查对照.json").read_bytes())
    store = Store(Path(inventory["archive"]))
    root = Path(json.loads((ROOT/"configs/projects/puggy.json").read_bytes())["production"]["assetRoot"])/"episodes"
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts"],check=True)
    from shot_references import check_shot_references
    from shot_rhythm import analyse
    from review_evidence import validate_review_evidence
    def outcome(call):
        try:
            call()
            return dict(state="passed")
        except (ValueError,KeyError,OSError,TypeError) as exc:
            return dict(state="failed",message=str(exc))
    counts = Counter()
    results = []
    for ep in inventory["episodes"]:
        old = ArchiveFiles(ep["copiedFiles"],ep["archive"],store)
        directory = root/ep["episode"]
        data = json.loads(old.read("episode.json"))
        assert (directory/"episode.json").read_bytes()==old.read("episode.json")
        counts["episodes"]+=1
        if data.get("schema_version")!=2:
            results.append(dict(episode=ep["episode"],state="旧清单"))
            counts["older"]+=1
            continue
        assert old.read(PAGE)==(directory/PAGE).read_bytes()
        # Recreate only the URL base added in this release from the exact original folder.
        # Every page, TXT and media file read by the checker still comes from the old frozen receipt.
        meta = dict(old.metadata,pageOriginalRoot=str(directory),
                    pageExternalPaths={name:os.path.relpath(name,directory/"正式交付") for name in old.metadata["fixedRefs"]})
        receipt = ArchiveFiles(old.files,meta,store)
        actual = checked_content(receipt,data)
        mirror = Path(inventory["archive"]).parent/"copies"/ep["episode"]
        gates = {}
        for rule, native, oracle in [
            ("shotReferences",lambda:shotrefs.check_shot_references(receipt.text,data),lambda:check_shot_references(mirror,data)),
            ("rhythm",lambda:rhythm.packaged_report(data,receipt.text),lambda:analyse(mirror,data)),
            ("reviews",lambda:archivechecks.review_evidence(receipt,data),lambda:validate_review_evidence(mirror/"episode.json"))]:
            gates[rule] = outcome(native)
            assert gates[rule]==outcome(oracle),(ep["episode"],rule,gates[rule],outcome(oracle))

        counts[actual["state"]]+=1
        for row in actual["details"]["segments"]:
            counts["segments"]+=1
            counts["textPass"]+=row["textState"]=="passed"
            counts["allMappingsPass"]+=row["mappingState"]=="passed"
            seg=next(s for s in data["segments"] if s["id"]==row["segment"])
            assert row["promptSha256"]==sha256((directory/seg["prompt"]).read_bytes()).hexdigest()
            counts["checkedInputPositions"]+=len(row["inputs"])
            for ref in row["inputs"]:
                asset=next(a for a in data["assets"] if a["id"]==ref["assetId"])
                assert ref["sha256"]==sha256((directory/asset["path"]).read_bytes()).hexdigest()
        results.append(dict(episode=ep["episode"],manifestChecks=gates,**actual))
    (evidence/"历史图文页与实际上传逐段核对.json").write_text(json.dumps(dict(sourceCommit=PIN,counts=counts,results=results),ensure_ascii=False,indent=2)+"\n")
    print(dict(counts))


if __name__=="__main__":
    main()
