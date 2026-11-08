"""Locate changes against the exact source ranges published in a method pack."""
import argparse
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess


@lru_cache(maxsize=256)
def source_hunks(root, base, new, path):
    # Columns are zero-based half-open intervals, including insertions and deletions.
    diff = subprocess.check_output(["git", "-C", root, "--literal-pathspecs", "diff", "--no-color",
                                    "--no-ext-diff", "--no-textconv", "--no-renames", "-U0",
                                    "--inter-hunk-context=0", base, new, "--", path])
    hunks = []
    for a, n, b, m in re.findall(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        n, m = int(n or 1), int(m or 1)
        left, right = int(a) - bool(n), int(b) - bool(m)
        hunks.append((left, left+n, right, right+m))
    return hunks


def intervals(ranges):
    result = []
    for span in ranges.split(","):
        if not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?", span):
            raise ValueError(f"无效的源行范围 {span}")
        parts = span.split("-")
        left, right = int(parts[0])-1, int(parts[-1])
        if left >= right or result and left < result[-1][1]:
            raise ValueError(f"源行范围重叠或次序无效 {ranges}")
        result.append((left, right))
    return result


def map_ranges(ranges, hunks):
    """Map the old selection, reporting ambiguous boundaries for editorial selection."""
    mapped, review = [], []
    changed = False
    for left, right in intervals(ranges):
        new_left, new_right = left, right
        for start, end, new_start, new_end in hunks:
            added = new_end - new_start
            delta = added - (end - start)
            if start == end and start in (left, right):
                review.append(f"新增内容位于选段边界（原第 {start} 行之后）")
            if end <= left:
                new_left += delta
                new_right += delta
            elif start < right:
                new_right += delta
                changed = True
                if start < left or end > right:
                    review.append(f"原第 {start+1}–{end} 行的改动跨越选段边界")
        if new_right <= new_left:
            review.append(f"原第 {left+1}–{right} 行已移除")
        mapped.append(str(new_left+1) if new_right==new_left+1 else f"{new_left+1}-{new_right}")
    return dict(lines=None if review else ",".join(mapped), changed=changed,
                review=list(dict.fromkeys(review)))


def compare(manifest, repo, root, target):
    base = manifest["source"][repo]
    new = subprocess.check_output(["git", "-C", root, "rev-parse", "--verify", "--end-of-options", target+"^{commit}"], text=True).strip()
    paths = subprocess.check_output(["git", "-C", root, "diff", "--no-ext-diff", "--no-renames",
                                     "--name-only", "-z", base, new]).decode().split("\0")
    entries = list(manifest["files"].items())
    for annex in manifest.get("projectFiles", {}).values():
        entries.extend(annex.items())
    changes = []
    for path in filter(None, paths):
        hunks = source_hunks(root, base, new, path)
        ranges = []
        sections = []
        for name, entry in entries:
            source = entry.get("source", {})
            if source.get("repo") != repo or source.get("path") != path:
                continue
            mapped = map_ranges(source["lines"], hunks)
            # The project's product protocol always takes the entire source.
            if source.get("selection")=="whole" or (name=="商品入景" and path=="references/product-placement.md"):
                exists = subprocess.run(["git","-C",root,"cat-file","-e",new+":"+path],capture_output=True).returncode==0
                count = len(subprocess.check_output(["git","-C",root,"show",new+":"+path]).splitlines()) if exists else 0
                mapped = dict(changed=True, lines=f"1-{count}" if count else None,
                              review=[] if count else ["整篇源文件已移除"])
            if mapped["changed"] or mapped["review"]:
                sections.append(name)
            if mapped["changed"] or mapped["review"] or mapped["lines"] != source["lines"]:
                ranges.append(dict(section=name, original=source["lines"], **mapped))
        stages = sorted(stage for stage, keys in manifest["stages"].items() if set(keys).intersection(sections))
        changes.append(dict(path=path, sections=sections, stages=stages, ranges=ranges,
                            hunks=[[a+int(b>a),b-a] for a,b,_,_ in hunks],
                            intervals=[dict(old=[a,b],new=[c,d]) for a,b,c,d in hunks]))
    return dict(repo=repo, base=base, target=new, changes=changes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("repo")
    parser.add_argument("root", type=Path)
    parser.add_argument("target")
    args = parser.parse_args()
    print(json.dumps(compare(json.loads(args.manifest.read_text()), args.repo, args.root, args.target),
                     ensure_ascii=False, indent=2))
