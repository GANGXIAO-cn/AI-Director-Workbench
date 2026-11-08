"""Compare read-only workbench imports with the pinned independent CLI."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import PIN, ROOT, SKILL

sys.path.insert(0, str(ROOT / "server"))
from workbench.service import Workbench


def compare(directory):
    source = Path(json.loads((ROOT/"configs/projects/puggy.json").read_text())["production"]["assetRoot"])
    settings = source / "发布设置.json"
    cli = SKILL / "scripts/check_publish_pack.py"
    committed = subprocess.check_output(["git", "-C", SKILL, "show", PIN + ":scripts/check_publish_pack.py"])
    assert committed == cli.read_bytes()
    wb = Workbench(directory / "data", {})
    pid = wb.create_project("puggy")["projectId"]

    def run(path):
        oracle = subprocess.run([sys.executable, "-B", cli, path, "--settings", settings], capture_output=True, text=True)
        result = {"sourceOK": oracle.returncode == 0}
        if path.is_file():
            data = path.read_bytes()
            record = wb.import_publish_pack(pid, path.name, data, str(path))
            assert wb.store.read_blob(record["sha256"]) == data and record["sha256"] == sha256(data).hexdigest()
            result.update(workbenchOK=record["parseStatus"] == "parsed", issue=record["diagnostic"], sha256=record["sha256"])
        else:
            result.update(workbenchOK=False, issue="规定位置没有发布包，未执行内容检查")
        if oracle.returncode:
            result["sourceIssue"] = oracle.stderr.strip()
        return result

    episodes = {p.parent.name:run(p.parent/"正式交付/01_发布包.md")
                for p in sorted((source/"episodes").glob("*/episode.json"))}
    from workbench import publishing
    raw = publishing.settings(dict(json.loads(settings.read_text()), copy_format="instrumental_v1"))
    fields = dict(title="MILO Umbrella", body="Wind turns the umbrella. 🐱\n" + "\n".join(raw["body_fixed_lines"]),
                  tags=", ".join(raw["fixed_tags"] + ["rain"]), suno_title="Umbrella", suno_style="Light 95 BPM", suno_exclude="vocals")
    baseline = publishing.pack_markdown(fields, {}, raw)
    faults = [
        ("unclosed", baseline + "\n## 编辑备注\n```text\nopen", "未闭合", "未闭合"),
        ("duplicate", baseline + "\n## 标题\n```text\nagain\n```", "多个代码块", "多个代码块"),
        ("required", baseline.replace("## Exclude Styles","## Unknown"), "缺少", "缺少"),
        ("character", baseline.replace("Wind turns","<Wind turns"), "禁用字符", "禁用字符"),
        ("prefix", baseline.replace("MILO Umbrella","Umbrella"), "标题", "标题"),
        ("line_count", baseline.replace("Wind turns the umbrella.", "Extra line\nWind turns the umbrella."), "正文须为", "行数"),
        ("fixed_lines", baseline.replace(raw["body_fixed_lines"][0], "Changed."), "固定行", "固定行"),
        ("hook", baseline.replace(" 🐱", ""), "剧情", "剧情"),
        ("repeat_title", baseline.replace("Wind turns the umbrella.", "MILO Umbrella"), "重复", "重复"),
        ("link", baseline.replace("Wind turns the umbrella.", "Link in bio"), "link", "link"),
        ("hashtags", baseline.replace(", rain", ", #rain"), "不加#", "不加 #"),
        ("fixed_tags", baseline.replace("cat, milo", "milo, cat"), "固定标签", "固定标签"),
        ("extra_tags", baseline.replace(", rain", ", Rain"), "标签最多", "本集标签"),
        ("extra_count", baseline.replace(", rain", ", rain, wind, storm"), "标签最多", "本集标签"),
        ("same_tag", baseline.replace(", rain", ", cat"), "标签最多", "本集标签"),
        ("vocals", baseline.replace("vocals", "drums"), "vocals", "vocals"),
        ("bpm", baseline.replace("95 BPM", "medium tempo"), "BPM", "BPM"),
        ("instrumental", baseline.replace("Instrumental", "Mode"), "Instrumental", "Instrumental"),
    ]
    def sample(name, text):
        path = directory / (name + ".md")
        path.write_text(text)
        return run(path)
    positive = sample("valid", baseline)
    assert positive["sourceOK"] and positive["workbenchOK"]
    injected = {}
    for key, text, source_message, native_message in faults:
        assert text != baseline
        result = sample(key, text)
        result["matchedRule"] = (not result["sourceOK"] and not result["workbenchOK"]
                                 and source_message in result.get("sourceIssue","") and native_message in result.get("issue",""))
        injected[key] = result
    return dict(sourceCommit=PIN, episodes=episodes, positive=positive, faults=injected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    evidence = compare(Path(tempfile.mkdtemp(prefix="publish-parity-")))
    pairs = evidence["episodes"]
    result = all(e["sourceOK"] == e["workbenchOK"] for e in pairs.values())
    faults = evidence["faults"]
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(len(pairs), sum(e["workbenchOK"] for e in pairs.values()), {k:v for k,v in faults.items() if not v["matchedRule"]})
    raise SystemExit(0 if result and all(e["matchedRule"] for e in faults.values()) else 1)
