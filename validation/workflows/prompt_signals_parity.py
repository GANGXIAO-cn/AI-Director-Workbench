"""Compare warning records and exact prompt bytes on the frozen historical copies."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from historical_baseline import PIN, ROOT, SKILL

sys.path[:0] = [str(ROOT / "server"), str(SKILL / "scripts")]
import readable_prompts as oracle
from workbench.archivefiles import ArchiveFiles
from workbench.promptsignals import archived
from workbench.storage import Store


def main(destination):
    subprocess.run(["git", "-C", SKILL, "diff", "--quiet", PIN, "--", "scripts"], check=True)
    baseline = ROOT / "修复验证_20261108/历史集登记材料与独立检查对照.json"
    source = json.loads(baseline.read_bytes())
    directory = Path(source["archive"])
    store = Store(directory)
    results, kinds = [], Counter()
    with TemporaryDirectory(prefix="adw-prompt-standards-") as tmp:
        standard = Path(tmp) / "角色外观标准.json"
        for episode in source["episodes"]:
            name = episode["episode"]
            root = directory.parent / "copies" / name
            files = ArchiveFiles(episode["copiedFiles"], episode["archive"], store)
            manifest = files.read("episode.json")
            assert manifest == (root / "episode.json").read_bytes(), name
            data = json.loads(manifest)
            if data.get("schema_version") != 2:
                results.append(dict(episode=name, status="旧版清单"))
                continue
            native = archived(files, data)
            eligible = [s for s in data["segments"] if s.get("prompt_format") == "readable_v3"]
            expected = [(s["id"], sha256((root / s["prompt"]).read_bytes()).hexdigest()) for s in eligible]
            assert [(s["segment"], s["sha256"]) for s in native["inputs"]] == expected, name
            assert len(native["skipped"]) + len(expected) == len(data["segments"]), name
            if eligible:
                for key, target in [("characterStandard", standard), ("promptStandard", standard.with_name("视频提示词标准.json"))]:
                    target.write_bytes(store.read_blob(files.metadata["standards"][key]["sha256"]))
            with patch.object(oracle, "standard_file", return_value=standard):
                warnings = oracle.episode_warnings(root, dict(data, segments=eligible))
            assert warnings == native["warnings"], name
            kinds.update(w["kind"] for w in warnings)
            results.append(dict(episode=name, inputs=native["inputs"], skipped=native["skipped"], warnings=warnings))
    counts = dict(episodes=len(results), v2=sum("inputs" in r for r in results),
                  eligibleEpisodes=sum(bool(r.get("inputs")) for r in results),
                  verifiedSegments=sum(len(r.get("inputs", [])) for r in results),
                  skippedV2Segments=sum(len(r.get("skipped", [])) for r in results), warningCounts=dict(kinds))
    destination.write_text(json.dumps(dict(sourceCommit=PIN, **counts, results=results), ensure_ascii=False, indent=2) + "\n")
    print(counts)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
