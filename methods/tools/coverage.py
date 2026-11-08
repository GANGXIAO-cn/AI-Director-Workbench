"""Inventory every pinned source file and measure direct section coverage in the released method pack."""
import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from freeze_sections import ROOT, STORY, VIDEO, PINS

# Destination policy from migration proposal §2, independent of the freeze recipe.
ROUTES = {
    "story-standard":"AP", "story-draft":"AP", "outline":"A", "selection":"AP", "topic-research":"AW",
    "research-records":"W", "planning-contract":"PW", "user-priority-videos":"P", "experiment-review":"X",
    "story-handoff":"A", "story-ip":"AP", "plot-mechanisms":"A", "series-continuity":"AP", "examples":"P",
    "camera-rhythm":"A", "scene-assets":"AW", "asset-library":"AW", "product-placement":"PW", "shot-references":"A",
    "character-contract":"AW", "image-selection":"AW", "visual-checklist":"A", "prompt-format":"AP",
    "voice-dialogue":"AP", "prompt-review":"AW", "review-delivery":"AW", "revision-diff":"W",
    "episode-manifest":"W", "continuous-production":"AW", "finecut-handoff":"A", "publish-pack":"AP",
    "audience-feedback":"X",
}


def route(repo, path):
    if path.startswith("references/"):
        return ROUTES[Path(path).stem]
    if path == "SKILL.md":
        return "AX" if repo == STORY else "APX"
    if path == "config.json":
        return "PW"
    if path.startswith("agents/"):
        return "X"
    if "experiment" in path:
        return "X"
    if path.startswith("assets/fourthwall-products/"):
        return "P"
    if path.startswith("templates/") and path.endswith(".md"):
        return "A" if repo == STORY else "AW"
    if path.startswith(("scripts/", "tests/", "templates/")):
        return "W"
    raise ValueError(f"新源文件尚未分类 {repo}/{path}")


def sections(path, data):
    if not path.endswith(".md"):
        return []
    lines = data.decode("utf-8").splitlines()
    headings = []
    fence = None
    for idx, line in enumerate(lines, 1):
        delimiter = re.match(r"^\s*(`{3,}|~{3,})", line)
        if delimiter:
            token = delimiter[1]
            if fence is None:
                fence = token
            elif token.startswith(fence):
                fence = None
            continue
        heading = re.match(r"^#{1,3}\s+(.+)", line)
        if fence is None and heading:
            headings.append((idx, heading[1]))
    if not headings or headings[0][0] != 1:
        headings.insert(0, (1, "文件开头"))
    return [(start, next_start - 1, title) for (start, title), (next_start, _) in
            zip(headings, [*headings[1:], (len(lines) + 1, "")])]


def report(pack, pack_dir=None, source_root=None):
    version = pack["id"].split("@")[1]
    pack_dir = pack_dir or ROOT / "methods/short-film" / version
    source_root = source_root or Path.home()/".codex/skills"
    pins = pack["source"]
    if set(pins) != set(PINS) or not all(re.fullmatch(r"[0-9a-f]{40}", v) for v in pins.values()):
        raise ValueError("方法包须注明全部源仓库的固定提交")
    library = ROOT / "configs/project-libraries/puggy/v1"
    catalog = json.loads((library / "index.json").read_text())
    copies = {}
    def register(repo, source, target, digest):
        path = library / target
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"项目资料与索引指纹不符：{target}")
        copies[repo, source] = dict(target=str(path.relative_to(ROOT)), sha256=digest)
    for section in catalog["sections"]:
        register(section["repo"], section["path"], section["path"], section["sourceSha256"])
    for product in catalog["products"]:
        register(product["repo"], product["source"], product["path"], product["sha256"])
        register(product["repo"], product["catalogSource"], "商品简介.txt", product["catalogSha256"])
    extracted = defaultdict(list)
    source_hashes = {}
    for group, entries in [("A", pack["files"]), ("P", pack["projectFiles"]["puggy"])]:
        for entry in entries.values():
            ref = entry.get("source", {})
            if "repo" in ref:
                if ref["commit"] != pins[ref["repo"]]:
                    raise ValueError(f"选段提交与方法包不一致：{entry['path']}")
                source_key = (ref["repo"],ref["path"])
                if source_hashes.setdefault(source_key,ref["sha256"]) != ref["sha256"]:
                    raise ValueError(f"同一源文件声明了不同指纹：{source_key}")
                included = set()
                for interval in ref["lines"].split(","):
                    bounds = list(map(int, interval.split("-")))
                    included.update(range(bounds[0], bounds[-1] + 1))
                target = (pack_dir / entry["path"]).read_bytes()
                if sha256(target).hexdigest() != entry["sha256"]:
                    raise ValueError(f"冻结文件指纹不符：{entry['path']}")
                extracted[ref["repo"], ref["path"]].append((group, entry["path"], included, target.decode().splitlines()))

    records = []
    for repo, commit in pins.items():
        git = ["git", "-C", source_root / repo]
        paths = subprocess.check_output([*git, "ls-tree", "-r", "--name-only", "-z", commit]).decode().split("\0")
        for path in filter(None, paths):
            data = subprocess.check_output([*git, "show", f"{commit}:{path}"])
            destination = route(repo, path)
            reason = None
            if "X" in destination:
                if "experiment" in path:
                    reason = "隔离实验与桌面实验评审当前不搬"
                elif path.startswith("agents/") or path == "SKILL.md":
                    reason = "仅 CLI 技能注册与调用约定归 X"
                else:
                    reason = "发布后数据回收不在制作工作台范围"
            item = dict(repo=repo, commit=commit, path=path, sha256=sha256(data).hexdigest(),
                        plannedRoutes=list(destination), excludedReason=reason)
            expected = source_hashes.pop((repo,path),None)
            if expected is not None and item["sha256"] != expected:
                raise ValueError(f"选段源文件与目标提交指纹不符：{path}")
            copy = copies.get((repo, path))
            if copy:
                if copy["sha256"] != item["sha256"]:
                    raise ValueError(f"项目副本与固定提交不符：{path}")
                item["exactProjectCopy"] = copy["target"]
            blocks = []
            lines = data.decode().splitlines() if path.endswith(".md") else []
            mappings = []
            for group, target, numbers, target_lines in extracted[repo, path]:
                selected = sorted(n for n in numbers if lines[n-1].strip())
                matcher = SequenceMatcher(None, [lines[n-1] for n in selected],
                                          [line for line in target_lines if line.strip()], autojunk=False)
                exact = {n for match in matcher.get_matching_blocks() for n in selected[match.a:match.a+match.size]}
                mappings.append((group, target, numbers, exact))
            for start, end, heading in sections(path, data):
                hits = []
                exact_lines = set()
                for group, target, numbers, exact in mappings:
                    selected = sorted(n for n in numbers if start <= n <= end)
                    if selected:
                        hits.append(dict(route=group, target=target, selectedLines=selected))
                        exact_lines.update(exact.intersection(selected))
                nonblank = {n for n in range(start, end+1) if lines[n-1].strip()}
                complete = bool(nonblank) and (bool(copy) or exact_lines == nonblank)
                blocks.append(dict(heading=heading, start=start, end=end, directFrozenSections=hits,
                                   completeExactText=complete, nonblankLines=len(nonblank),
                                   exactNonblankLines=len(nonblank) if copy else len(exact_lines)))
            item["sections"] = blocks
            records.append(item)
    if source_hashes:
        raise ValueError(f"源提交缺少选段文件：{list(source_hashes)}")
    if len(copies) != sum("exactProjectCopy" in item for item in records):
        raise ValueError("存在未追溯到固定提交的项目资料")
    return dict(pack=pack["id"], source=pins, files=records, fileCounts=dict(Counter(r["repo"] for r in records)),
                exactProjectFileCount=len(copies),
                sectionsWithCompleteExactText=sum(h["completeExactText"] for r in records for h in r["sections"]),
                sectionsWithMethodLinesOrProjectCopy=sum(bool(h["directFrozenSections"]) or "exactProjectCopy" in r
                                                       for r in records for h in r["sections"]),
                sectionCount=sum(len(r["sections"]) for r in records),
                sectionsWithDirectLines=sum(bool(h["directFrozenSections"]) for r in records for h in r["sections"]))


def attach_line_audit(result, audit):
    """Link reviewed destinations at the source-section level without changing direct-copy counts."""
    repo = audit.get("repo", STORY)
    if audit["pack"] != result["pack"] or audit["commit"] != result["source"][repo]:
        raise ValueError("逐行核查与覆盖表的版本不一致")
    checked = set()
    remaining = Counter()
    for file in result["files"]:
        detail = audit["files"].get(file["path"]) if file["repo"] == repo else None
        if detail:
            if detail["sha256"] != file["sha256"]:
                raise ValueError("逐行核查的源文件指纹不一致")
            checked.add(file["path"])
        for section in file["sections"]:
            direct = bool(section["directFrozenSections"] or file.get("exactProjectCopy"))
            if detail:
                locations = [d for row in detail["lines"] if section["start"] <= row["line"] <= section["end"]
                             for d in row["destinations"]]
                section["reviewedDestinations"] = [dict(route=route,target=target) for route,target in
                                                   sorted({(d["mode"],d["target"]) for d in locations})]
                section["partiallyImplemented"] = any(d["mode"]=="W-部分" for d in locations)
            if file["plannedRoutes"] == ["X"]:
                section["exclusion"] = file["excludedReason"]
            if not direct:
                bucket = ("excludedByPlan" if section.get("exclusion") else "lineAudit" if "reviewedDestinations" in section
                          else "otherWithoutDirectText")
                remaining[bucket] += 1
    if checked != set(audit["files"]):
        raise ValueError("逐行核查中存在未登记的源文件")
    assert sum(remaining.values()) == result["sectionCount"] - result["sectionsWithMethodLinesOrProjectCopy"]
    result["sectionsWithoutDirectText"] = dict(remaining)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--ref", default="short-film@v2")
    parser.add_argument("--pack-dir",type=Path)
    parser.add_argument("--source-root",type=Path)
    parser.add_argument("--line-audit",action="append",default=[],type=Path,help="同版本逐行核查 JSON")
    args = parser.parse_args()
    if not re.fullmatch(r"short-film@v[1-9][0-9]*", args.ref):
        parser.error("未知方法包")
    pack_dir = args.pack_dir or ROOT/"methods/short-film"/args.ref.split("@")[1]
    result = report(json.loads((pack_dir/"manifest.json").read_text()), pack_dir, args.source_root)
    for audit in args.line_audit:
        attach_line_audit(result,json.loads(audit.read_bytes()))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result["fileCounts"], result["sectionCount"], result["sectionsWithDirectLines"])
