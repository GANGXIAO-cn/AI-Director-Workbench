"""Compare frozen task input sizes with real runs recorded before local methods were added."""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def load(path):
    return json.loads(path.read_text())


def size(manifest):
    return sum(entry["bytes"] for entry in manifest["inputs"])


def report(evidence, previous):
    baseline = defaultdict(dict)
    for path in previous.rglob("input-manifest.json"):
        manifest = load(path)
        if manifest["purpose"] in {"create", "revise"}:
            baseline[manifest["stage"]][manifest["runId"]] = size(manifest)
    lines = ["# 真实任务输入字节数", "",
             "历史列采用加入本地方法前的同阶段创作运行中位数，各集故事、素材和开工资料不同。重复导出的同一运行只计一次。", "",
             "| 路径 | 阶段 | 运行 ID | 用途 | 输入字节 | 历史同阶段中位数（样本数） |",
             "| --- | --- | --- | --- | ---: | ---: |"]
    for path in sorted(evidence.glob("*/*-manifest.json")):
        receipt = load(path)
        if not receipt["matches"]:
            raise ValueError(f"指纹不一致 {path}")
        m = receipt["manifest"]
        values = baseline[m["stage"]].values()
        comparison = f"{median(values):g}（{len(values)}）" if values and m["purpose"] in {"create", "revise"} else "无可比样本"
        lines.append(f"| {path.parent.name} | {m['stage']} | {m['runId']} | {m['purpose']} | {size(m)} | {comparison} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("previous", type=Path)
    args = parser.parse_args()
    (args.evidence / "输入字节数.md").write_text(report(args.evidence, args.previous))
