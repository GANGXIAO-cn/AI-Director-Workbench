"""Replay v2 grid dimensions on the frozen 49-episode evidence without importing it again."""
import json
from pathlib import Path
import subprocess
import sys

from historical_baseline import PIN, ROOT, SKILL

sys.path[:0] = [str(ROOT / "server"), str(SKILL / "scripts")]
import validate_episode as source
from workbench.archivefiles import ArchiveFiles
from workbench.specs import frame_geometry
from workbench.storage import Store


def source_geometry(data, root):
    target = source.parse_aspect_ratio(data["aspect_ratio"])
    for item in [data, *data["segments"]]:
        settings = item.get("generation_settings")
        if isinstance(settings,dict) and "aspect_ratio" in settings:
            if source.parse_aspect_ratio(settings["aspect_ratio"]) != target:
                raise ValueError("生成设置画幅与本集不一致")
    assets = {a["id"]:a for a in data["assets"]}
    grids = {}
    for seg in data["segments"]:
        if seg["reference_strategy"] == "flex":
            continue
        assert seg["reference_strategy"] == "grid" and len(seg["references"]) == 1
        asset = assets[seg["references"][0]["asset_id"]]
        assert asset["category"] == "grid"
        size = source.image_size(root/asset["path"])
        if not size or not source.aspect_matches(*size, data["aspect_ratio"]):
            raise ValueError("九宫格画幅与本集不一致")
        grids[seg["id"]] = dict(asset=asset["id"],width=size[0],height=size[1])
    return dict(target=data["aspect_ratio"],segments=len(data["segments"]),grids=grids)


def main():
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts"],check=True)
    evidence = ROOT/"修复验证_20261108"
    baseline = json.loads((evidence/"历史集登记材料与独立检查对照.json").read_bytes())
    path = Path(baseline["archive"])
    store = Store(path)
    results = []
    for ep in baseline["episodes"]:
        name = ep["episode"]
        files = ArchiveFiles(ep["copiedFiles"], ep["archive"], store)
        root = path.parent/"copies"/name
        manifest = files.read("episode.json")
        assert (root/"episode.json").read_bytes() == manifest
        data = json.loads(manifest)
        if data.get("schema_version") != 2:
            results.append(dict(episode=name,version=data.get("schema_version"),status="旧格式，无 v2 参考策略"))
            continue
        left = source_geometry(data, root)
        right = frame_geometry(data, lambda a: files.asset(a)[1])
        assert left == right, name
        results.append(dict(episode=name,version=2,source=left,workbench=right))
    checked = [row["source"] for row in results if "source" in row]
    totals = dict(episodes=len(results), compared=len(checked), segments=sum(r["segments"] for r in checked),
                  grids=sum(len(r["grids"]) for r in checked))
    Path(sys.argv[1]).write_text(json.dumps(dict(sourceCommit=PIN,**totals,results=results),ensure_ascii=False,indent=2)+"\n")
    print(totals)


if __name__ == "__main__":
    main()
