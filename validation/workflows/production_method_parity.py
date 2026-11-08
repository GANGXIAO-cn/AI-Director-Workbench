"""Audit all nonblank source lines and external guide excerpts against the installed v5 release."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from story_method_parity import audit as story_audit
from historical_baseline import SKILL, PIN, ROOT

PACK = ROOT/"methods/short-film/v5"
OUT = ROOT/"修复验证_20261108"
# Lines that intentionally differ from the pinned Chinese original. Every other
# consumed nonblank line must be identical. PUGGY passages permit no edits.
CHANGES = {
    "scene-assets": {},
    "camera-rhythm": {3:"按项目确定类型",50:"角色名与动作主体通用化",102:"表情与对白按本段角色"},
    "voice-dialogue": {7:"具体口音和人物音色改由项目提供",13:"台词语言与语速取自项目",
                       21:"说话者按是否发声编号，台词沿用实际语言",27:"无对白角色通用化",
                       39:"按本段设定处理唱词",45:"引用适用的项目声音约束"},
    "prompt-format": {11:"原版全参考指南随包含指纹副本",12:"原版基础指南随包含指纹副本",13:"配乐按本段声音计划",
                      25:"画面风格由项目确定",31:"对白语言、口音和角色发声边界来自项目",41:"已确认角色名和支撑面通用化",
                      55:"工作台项目指定制作基准",77:"声音栏按项目角色及实际语言填写",108:"表情差异适用于同场角色",
                      132:"引用项目适用的声音约束"},
}


def line_numbers(ranges):
    for span in ranges.split(","):
        ends = span.split("-")
        yield from range(int(ends[0]),int(ends[-1])+1)


def audit(pack=PACK, changes=CHANGES, workbench_lines=None):
    manifest = json.loads((pack/"manifest.json").read_bytes())
    result = {}
    for stem, allowed in changes.items():
        source = stem if "/" in stem else f"references/{stem}.md"
        raw = subprocess.check_output(["git","-C",SKILL,"show",f"{PIN}:{source}"])
        lines = raw.decode().splitlines()
        targets = defaultdict(list)
        differences = set()
        for scope,catalog in [("A",manifest["files"]),("P",manifest["projectFiles"]["puggy"])]:
            for key,entry in catalog.items():
                spec = entry["source"]
                if spec.get("repo")!="milo-leo-short-video" or spec["path"]!=source:
                    continue
                assert spec["commit"]==PIN and spec["sha256"]==sha256(raw).hexdigest()
                dest = (pack/entry["path"]).read_bytes()
                assert sha256(dest).hexdigest()==entry["sha256"]
                source_rows = [i for i in line_numbers(spec["lines"]) if lines[i-1].strip()]
                actual = [(i,s) for i,s in enumerate(dest.decode().splitlines(),1) if s.strip()]
                stages = [s for s,items in manifest["stages"].items() if key in items]
                assert stages and "cold_read" not in stages
                for n,(target_line,text) in zip(source_rows,actual,strict=True):
                    record = dict(mode=scope,target=entry["path"],line=target_line,stages=stages,exact=text==lines[n-1])
                    if not record["exact"]:
                        assert scope=="A" and n in allowed,(source,n,key,text)
                        differences.add(n)
                        record.update(installedText=text,note=allowed[n])
                    targets[n].append(record)
        for n,location in (workbench_lines or {}).get(source, {}).items():
            assert n not in targets and (ROOT/location).is_file()
            targets[n].append(dict(mode="W",target=location,exact=False,note="发布格式检查由工作台确定性校验接管"))
        assert set(targets)=={i for i,s in enumerate(lines,1) if s.strip()}
        assert differences==set(allowed), (stem,differences.symmetric_difference(allowed))
        counts=Counter("exact" if any(v["exact"] for v in dest) else "workbench" if all(v["mode"]=="W" for v in dest) else "rewritten" for dest in targets.values())
        result[source]=dict(nonblank=len(targets),counts=dict(counts),sha256=sha256(raw).hexdigest(),
            lines=[dict(line=n,sourceText=lines[n-1],destinations=targets[n]) for n in sorted(targets)])
    return result


def check_guides():
    manifest=json.loads((PACK/"manifest.json").read_bytes())
    guides={}
    for name in ["base-en.txt","ref-en.txt"]:
        frozen=(PACK/"guides"/name).read_bytes()
        original=Path.home()/".codex/skills/h3-prompt-writing/references"/name
        assert frozen==original.read_bytes()
        digest=sha256(frozen).hexdigest()
        assert manifest["files"][name]["sha256"]==digest
        excerpt, = [e for e in manifest["files"].values() if e["source"].get("path")=="h3-prompt-writing/references/"+name and "lines" in e["source"]]
        actual=(PACK/excerpt["path"]).read_bytes()
        numbers = line_numbers(excerpt["source"]["lines"])
        lines=frozen.decode().splitlines()
        assert [s for s in actual.decode().splitlines() if s.strip()]==[lines[i-1] for i in numbers if lines[i-1].strip()]
        assert sha256(actual).hexdigest()==excerpt["sha256"] and excerpt["source"]["sha256"]==digest
        guides[name]=dict(originalSha256=digest,excerptSha256=excerpt["sha256"],lines=excerpt["source"]["lines"])
    return guides


if __name__=="__main__":
    for repo,files,path in [("milo-leo-short-video",audit(),"四份制作参考逐行去向"),("milo-leo-story",story_audit(PACK),"故事标准写法与大纲逐行去向")]:
        pins=json.loads((PACK/"manifest.json").read_bytes())["source"]
        data=dict(pack="short-film@v5",repo=repo,commit=pins[repo],files=files)
        if repo=="milo-leo-short-video":
            data["guides"]=check_guides()
        (OUT/("v5_"+path+".json")).write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
        print(repo,[(k,r["nonblank"],r["counts"]) for k,r in files.items()])
