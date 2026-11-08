"""Run the three pinned read-only skill checks for each distinct historical episode."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SKILL = Path.home()/".codex/skills/milo-leo-short-video"
PIN = "ab0fa75e3b2254a9341a8501a48be7577ab023eb"


def inspect(manifest):
    folder = manifest.parent
    delivery = folder / "正式交付"
    pack = delivery / "01_发布包.md"
    page = delivery / "00_图文对照.html"
    checks = {}
    for name, args in [("validate_episode", [manifest]), ("check_delivery", [manifest, page]),
                       ("check_publish_pack", [pack, "--settings", folder.parents[1]/"发布设置.json"])]:
        run = subprocess.run([sys.executable, "-B", SKILL/"scripts"/(name+".py"), *args],
                             capture_output=True, text=True, timeout=180)
        checks[name] = dict(ok=not run.returncode, message=(run.stderr.strip() or run.stdout.strip())[-2200:])
    return dict(episode=folder.name, manifestSHA256=sha256(manifest.read_bytes()).hexdigest(), checks=checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    # These CLIs load sibling Python modules and, for page checks, JS/CSS templates.
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","templates","config.json"],check=True)
    source = Path(json.loads((ROOT/"configs/projects/puggy.json").read_text())["production"]["assetRoot"])
    manifests=sorted((source/"episodes").glob("*/episode.json"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        records=list(pool.map(inspect, manifests))
    totals=Counter(name for row in records for name, value in row["checks"].items() if value["ok"])
    output = dict(source=str(source/"episodes"), commit=PIN, count=len(records), passed=dict(totals), episodes=records)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n")
    print(len(records),dict(totals))
