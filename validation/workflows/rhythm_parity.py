"""Compare authored plan/text pairs with the frozen skill, including warning fingerprints."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import ROOT, SKILL, PIN

sys.path[:0] = [str(ROOT / "server"), str(SKILL / "scripts")]
from workbench import rhythm, readable


def sample(actions, spans, strategies):
    plan = dict(segments=[], shots=[])
    entries, manifest_segments, rows = {}, [], []
    markers = ["起镜", "同镜延续", "切镜", "同镜延续"]
    for index, strategy in enumerate(strategies, 1):
        sid = f"G0{index}"
        plan["segments"].append(dict(id=sid, order=index, strategy=strategy))
        motion = []
        for i, (start, end) in enumerate(zip(spans, spans[1:])):
            key = f"W{index}{i}"
            plan["shots"].append(dict(id=key, segment=sid, order=i, duration_ms=(end-start)*1000,
                                      camera="固定中景", action=actions[i], continuation="same" if i%2 else "cut"))
            motion.append(f"00:{start:02}.000-00:{end:02}.000\n{markers[i]}，{actions[i]}")
            rows.append([f"S{i//2+1}",sid,f"{start}–{end}","看手的动作","—",actions[i][:3],markers[i],"固定中景"])
        headings = readable.GRID if strategy == "grid" else readable.FLEX
        parts = dict.fromkeys(headings,"现场")
        parts["镜头描述：" if strategy == "grid" else "【镜头与动作】"] = "\n".join(motion)
        entries[sid] = dict(txt=readable.render(parts), prompt_format="readable_v3")
        manifest_segments.append(dict(id=sid, prompt=sid+".txt", duration_s=spans[-1], reference_strategy=strategy))
    return plan, entries, manifest_segments, rows


def compare():
    for module in ["shot_rhythm", "flat_timeline"]:
        path = f"scripts/{module}.py"
        assert (SKILL/path).read_bytes() == subprocess.check_output(["git","-C",SKILL,"show",PIN+":"+path])
    import shot_rhythm as original
    columns = original.FLAT_COLUMNS
    cases = {}
    root = Path(tempfile.mkdtemp(prefix="adw-shot-rhythm-"))
    faults = {
        "bad_id": (0,0,"镜1","镜号不合法"), "marker": (1,6,"切镜","表文接续标记不一致"),
        "same_id": (1,0,"S9","须沿用上一镜号"), "new_id": (2,0,"S1","须使用新镜号"),
        "time": (0,2,"0–3.5","联合表与正文时间不同"), "anchor": (0,5,"不存在","未在本段正文唯一命中"),
        "shared_anchor": (0,5,"手","未在本段正文唯一命中"), "no_camera": (0,7,"—","缺少构图"),
        "no_task": (0,3,"-","缺少观看任务"), "wrong_segment": (0,1,"G02","缺行、多行或错序"),
        "duplicate_task": (0,3,"看门<br>看门","观看任务为空或重复"),
        "empty_task": (0,3,"看门<br>","观看任务为空或重复"),
    }
    warnings = {"multi_task":"多任务","cut_cue":"切镜疑点","long":"长镜","few":"少镜段","even":"全片同镜数"}
    extras = {"dup_header":"重复的表头或列", "dup_column":"重复的表头或列", "wrong_width":"列数与表头不同",
              "missing_table":"缺少镜头充分性联合表", "missing_row":"缺行、多行或错序", "extra_row":"TXT未覆盖"}
    for name in [*faults,*extras,*warnings,"five_seconds","hold_reason","negative_cut","grid"]:
        actions = ["左手推门","右手拉帘","手指停住","手掌向上"]
        if name in {"cut_cue","negative_cut"}:
            actions[1] += "，"+("不要切到" if name=="negative_cut" else "硬切到")+"脚尖"
        spans = [0,3,6,7,8] if name in {"long","hold_reason"} else [0,2,5,6,8]
        strategies = ["flex","grid"] if name=="even" else ["grid" if name=="grid" else "flex"]
        plan, entries, segments, rows = sample(actions,spans,strategies)
        if name in faults:
            row,col,replacement,_ = faults[name]
            rows[row][col] = replacement
        if name=="multi_task":
            rows[1][3] = "看门<br>看手"
        if name=="hold_reason":
            rows[0][4] = "等手从门边挪到锁上"
        if name=="extra_row":
            rows.append(rows[-1].copy())
        if name=="missing_row":
            rows.pop()
        header = list(columns)
        if name=="dup_column":
            header.append(columns[0])
        if name=="wrong_width":
            rows[1].append("多一列")
        lines = [header,["---"]*len(header),*rows]
        if name=="dup_header":
            lines.append(header)
        raw = "# 联合表\n" if name=="missing_table" else "\n".join("|"+"|".join(line)+"|" for line in lines)
        plan["rhythmTable"] = raw
        (root/"plan.md").write_text(raw)
        for sid, entry in entries.items():
            (root/(sid+".txt")).write_text(entry["txt"])
        manifest = dict(shot_rhythm=dict(version=2,plan="plan.md"),segments=segments)
        results = []
        for fn in [lambda: original.analyse(root,manifest),lambda: rhythm.check(plan,entries)["report"]]:
            try:
                results.append(fn())
            except ValueError as exc:
                results.append(str(exc))
        assert results[0] == results[1], (name, results)
        result = results[0]
        if name in faults or name in extras:
            assert isinstance(result,str) and (faults[name][-1] if name in faults else extras[name]) in result, (name,result)
            cases[name] = dict(matchedRule=True, sourceOK=False, workbenchOK=False, issue=result)
        else:
            ids = [w["id"].split("/")[0] for w in result["warnings"]]
            if name in warnings:
                assert warnings[name] in ids, (name,ids)
            if name in {"five_seconds","hold_reason"}:
                assert "长镜" not in ids
            if name=="negative_cut":
                assert "切镜疑点" not in ids
            cases[name] = dict(matchedRule=True, sourceOK=True, workbenchOK=True, report=result)
    for name, config in [("missing_version", {}), ("bool_version", {"version": True}), ("unsupported_version", {"version": 1})]:
        manifest["shot_rhythm"] = config
        errors = []
        for fn in [lambda: original.analyse(root, manifest),
                   lambda: rhythm.packaged_report(manifest, lambda name: (root/name).read_text())]:
            try:
                fn()
            except ValueError as exc:
                errors.append(str(exc))
        assert errors == ["须声明 shot_rhythm.version=2"] * 2
        cases[name] = dict(matchedRule=True, sourceOK=False, workbenchOK=False, issue=errors[0])
    return dict(sourceCommit=PIN, cases=cases)


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = compare()
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(len(result["cases"]), "cases agree")
