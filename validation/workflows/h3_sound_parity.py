"""Compare frozen historical H3 declaration results with the pinned skill on original TXT bytes."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from historical_baseline import PIN, ROOT, SKILL

sys.path[:0] = [str(ROOT / "server"), str(SKILL / "scripts")]
from readable_prompts import check_h3_sound as original
from workbench.archivechecks import archived_sound
from workbench.archivefiles import ArchiveFiles
from workbench.storage import Store


def main(destination):
    subprocess.run(["git", "-C", SKILL, "diff", "--quiet", PIN, "--", "scripts"], check=True)
    baseline = json.loads((ROOT / "修复验证_20261108/历史集登记材料与独立检查对照.json").read_bytes())
    directory = Path(baseline["archive"])
    store = Store(directory)
    results, counts = [], Counter()
    for episode in baseline["episodes"]:
        name = episode["episode"]
        root = directory.parent / "copies" / name
        files = ArchiveFiles(episode["copiedFiles"], episode["archive"], store)
        manifest = files.read("episode.json")
        assert manifest == (root / "episode.json").read_bytes(), name
        data = json.loads(manifest)
        if data.get("schema_version") != 2:
            counts["legacy"] += 1
            results.append(dict(episode=name, state="旧版清单"))
            continue
        counts["v2"] += 1
        result = archived_sound(files, data)
        rows = result["details"]["segments"]
        assert result["details"]["marker"] == data.get("h3_rules")
        for seg, row in zip(data["segments"], rows, strict=True):
            path = root / seg["prompt"]
            raw = path.read_bytes()
            assert files.read(seg["prompt"]) == raw and sha256(raw).hexdigest() == row["sha256"], name
            assert row["segment"] == seg["id"]
            try:
                original(seg, path.read_text())
            except ValueError as exc:
                assert row["state"] == "failed" and row["message"] == str(exc), (name, row, exc)
            else:
                assert row["state"] == "passed", (name, row)
            counts[row["state"] + "Segments"] += 1
        passed = data.get("h3_rules") == "v1" and all(row["state"] == "passed" for row in rows)
        assert passed == (result["state"] == "passed")
        counts[result["state"] + "Episodes"] += 1
        results.append(dict(episode=name, **result))
    destination.write_text(json.dumps(dict(sourceCommit=PIN, counts=counts, episodes=results),
                                     ensure_ascii=False, indent=2) + "\n")
    print(counts)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
