"""Compare each nonblank line of the three pinned story references with its installed destination."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "methods/short-film/v4"
REPO = Path.home()/".codex/skills/milo-leo-story"
PIN = "a07840b5aa286d8f4a388d007ae6ba921a899024"

# Each change is narrowly scoped to one source. Record every actual pair in the audit.
SUBSTITUTIONS = {
    "story-standard": [("MILO","主角"), ("主人收场","另一位角色收场")],
    "story-draft": [("MILO","主角"), ("女主人上班不在家","另一位角色上班不在家"),
                    ("LEO在哪","搭档在哪"), ("女主人在哪","其他角色在哪"), ("猫不说话，就写","角色没有台词时，就写")],
    "outline": [("MILO","主角"), ("LEO或女主人","对手"),
                ("实验另有E情境短事件","E情境短事件"), ("实验E","情境短事件结构")],
}

# Explicit disposition for source lines which were not copied into a methods or project file.
# A partial route is recorded even when another clause on that same line already works.
ROUTES = {
    "story-standard": [
        ([3], "A", "story/story-standard.md；sections/故事标准的参考出处.md", "原文导言的功能由标准和出处页承担。"),
        ([54,113,115,116,117,118,119,120,122], "W", "server/workbench/projectops.py；server/workbench/service.py；server/workbench/production.py；server/workbench/delivery.py",
         "选题开工、送审、采用、交付更新状态。例外记录在具体版本的问题放行中。文件和命令名属于原技能操作。"),
        ([62], "W-部分", "server/workbench/service.py；review/story-review.md",
         "有版本独审、冷读与问题记录。排版变更沿用结论、实质变动按影响范围复审仍需核验。"),
        ([137], "W", "server/workbench/storycheck.py；server/workbench/service.py",
         "角色表经结构检查，修订后的版本须过采用门槛。"),
    ],
    "story-draft": [
        ([1,3], "A", "story/story-draft.md；story/story-standard.md；sections/故事梗概、角色表与交接模板.md",
         "标题和导航由本版字段、故事标准和模板承接。"),
        ([48], "A/W", "review/story-review.md；server/workbench/service.py",
         "独立审阅者按冻结输入完成梗概冷读、来源对照，记录实际观察和结论。"),
        ([50,56], "X", "原技能 review.md 的代码围栏", "工作台以版本关联结构化记录，原本地文件的排版符号无需迁移。"),
        ([51,52,53,54,55], "W", "server/workbench/service.py；web/src/Workspace.tsx",
         "故事版本关联独审阶段、执行器、观察、结论与问题处理。"),
        ([58], "W-部分", "server/workbench/service.py；web/src/Workspace.tsx",
         "普通问题可记录理由，阻塞问题禁止采用。按内容影响范围复审、纯排版改动沿用结论仍需核验。"),
        ([60], "W", "server/workbench/service.py", "采用快照保留版本、独审记录和原结论。输入指纹用于核验冻结文件，原技能命令路径由服务替代。"),
        ([82], "W", "server/workbench/storycheck.py；server/workbench/service.py", "角色表结构由程序检查，本集具体人物及作用交由独审。"),
    ],
    "outline": [],
}


def audit(pack=PACK):
    manifest = json.loads((pack/"manifest.json").read_bytes())
    result = {}
    for stem, replacements in SUBSTITUTIONS.items():
        source = "references/"+stem+".md"
        raw = subprocess.check_output(["git","-C",REPO,"show",PIN+":"+source])
        lines = raw.decode().splitlines()
        rows = defaultdict(list)
        for mode, catalog in [("A",manifest["files"]), ("P",manifest["projectFiles"]["puggy"])]:
            for record in catalog.values():
                ref = record["source"]
                if (ref.get("repo"),ref.get("path")) != ("milo-leo-story",source):
                    continue
                assert ref["commit"] == PIN and ref["sha256"] == sha256(raw).hexdigest()
                dest = (pack / record["path"]).read_bytes()
                assert sha256(dest).hexdigest() == record["sha256"]
                numbers = []
                for span in ref["lines"].split(","):
                    ends = [int(n) for n in span.split("-")]
                    numbers.extend(n for n in range(ends[0],ends[-1]+1) if lines[n-1].strip())
                text_lines = [(n,t) for n,t in enumerate(dest.decode().splitlines(),1) if t.strip()]
                for n,(target_line,target) in zip(numbers,text_lines,strict=True):
                    before = lines[n-1]
                    entry = dict(mode=mode,target=record["path"],line=target_line,exact=(before==target))
                    if before != target:
                        expected = before
                        for old,new in replacements:
                            expected = expected.replace(old,new)
                        assert mode == "A" and expected == target, (source,n,target)
                        entry["installedText"] = target
                    rows[n].append(entry)
        for numbers,mode,destination,note in ROUTES[stem]:
            if mode != "X":
                for target in destination.split("；"):
                    assert (ROOT/target).is_file() or (pack/target).is_file(), target
            for n in numbers:
                assert n not in rows and lines[n-1].strip(), (source,n)
                rows[n].append(dict(mode=mode,target=destination,note=note))
        nonblank = {n for n,t in enumerate(lines,1) if t.strip()}
        assert set(rows) == nonblank, (source, nonblank.symmetric_difference(rows))
        counts = Counter()
        for locations in rows.values():
            counts["exact" if any(r.get("exact") for r in locations) else
                   "rewritten" if any("installedText" in r for r in locations) else locations[0]["mode"]] += 1
        result[source] = dict(nonblank=len(nonblank),counts=dict(counts),sha256=sha256(raw).hexdigest(),
            lines=[dict(line=n,sourceText=lines[n-1],destinations=rows[n]) for n in sorted(rows)])
    return result


if __name__ == "__main__":
    result = audit()
    dest = ROOT/"修复验证_20261108/v4_故事标准写法与大纲逐行去向.json"
    dest.write_text(json.dumps(dict(commit=PIN,pack="short-film@v4",files=result),ensure_ascii=False,indent=2)+"\n")
    for name,file in result.items():
        print(name,file["nonblank"],file["counts"])
