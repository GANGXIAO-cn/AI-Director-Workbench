"""Exercise authored grid/flex layouts against the original readable_v3 and flat-timeline checks."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import PIN, ROOT, SKILL

sys.path[:0] = [str(ROOT/"server"),str(ROOT/"server/tests"),str(SKILL/"scripts")]
from helpers import Env
from workbench import readable
from workbench.errors import Rejected


def build(directory, strategy):
    env = Env(directory)
    wb = env.wb
    pid, eid = env.new_episode(config="puggy")
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"G01":[3000, 5000]})
    plan["segments"][0]["strategy"] = strategy
    refs = {}
    for role in ["milo","leo"]:
        refs[role] = wb.use_fixed_role(eid, role, "select-"+role)["versionId"]
    for s in plan["shots"]:
        s.update(action="一只猫托住盒底，另一只松爪。",camera="侧面中景")
    picture = env.image(pid,eid,name="G01-九宫格" if strategy=="grid" else "G01-柜台",category="grid" if strategy=="grid" else "state",grid=strategy=="grid",size=(90,160))
    if strategy=="flex":
        plan["shots"][0]["startAssetId"]=picture["versionId"]
    wb.mark_image(eid,picture["versionId"],"required",[s["id"] for s in plan["shots"]])
    env.save_adopt(eid,"plan",plan)
    for role,vid in refs.items():
        wb.set_cast(eid,"G01",role,"ok",vid)
    entry=wb.draft_prompts(eid)["segments"]["G01"]
    seg=dict(id="G01",duration_s=8,reference_strategy=strategy,prompt_format="readable_v3",native_bgm="none",
             on_screen_roles=list(refs),references=[dict(label=r["label"],asset_id=r["assetVersionId"]) for r in entry["refs"]])
    data=dict(assets=[dict(id=v,fixed_role=role) for role,v in refs.items()]+[dict(id=picture["versionId"])])
    return entry,seg,data,{r["id"]:r for r in wb.project_role_assets(pid)["roles"]}


def compare():
    for name in ["readable_prompts","flat_timeline"]:
        relative = "scripts/" + name + ".py"
        assert (SKILL/relative).read_bytes() == subprocess.check_output(["git","-C",SKILL,"show",PIN+":"+relative])
    import readable_prompts as source
    temp=Path(tempfile.mkdtemp(prefix="adw-readable-"))
    records={}
    for strategy in ["flex","grid"]:
        entry,seg,data,locked=build(temp/strategy,strategy)
        text=entry["txt"]
        def oracle(case):
            source.check_text(seg,case["txt"],source.READABLE_HEADINGS,flat=True,h3_rules=True)
            source.check_h3_sound(seg,case["txt"])
        def native(case):
            readable.validate(case,strategy,8000)
        oracle(entry)
        native(entry)
        source.check_complete_standard(temp,data,seg,text)
        source.check_short_standard(temp,data,seg,text,locked)
        faults={
            "gap": ("00:03.000-00:08.000","00:03.100-00:08.000"),
            "overlap": ("00:03.000-00:08.000","00:02.900-00:08.000"),
            "zero": ("00:00.000-00:03.000","00:00.000-00:00.000"),
            "truncated": ("00:03.000-00:08.000","00:03.000-00:07.999"),
            "same_line": ("00:03.000-00:08.000\n","00:03.000-00:08.000 "),
            "first_cut": ("起镜，","切镜，"),
            "second_start": ("切镜，","起镜，"),
            "empty_action": ("起镜，侧面中景，一只猫托住盒底，另一只松爪。","起镜，"),
            "second_timeline": ("切镜，","切镜，阶段二："),
            "internal_number": ("切镜，","切镜，S02 "),
            "production_note": ("切镜，","切镜，尚未试听，"),
            "missing_music": ("non_diegetic_music:", "music:"),
            "duplicate_music": ("non_diegetic_music: N/A","non_diegetic_music: N/A\nnon_diegetic_music: N/A"),
            "wrong_music": ("non_diegetic_music: N/A","non_diegetic_music: calm piano"),
            "false_silence": ("non_diegetic_music: N/A","non_diegetic_music: N/A\noverall_soundscape: N/A"),
        }
        cases={}
        for name,(old,new) in faults.items():
            assert old in text,name
            case=dict(entry, txt=text.replace(old,new))
            result={}
            for label,fn in [("source",oracle),("workbench",native)]:
                try: fn(case)
                except (ValueError,Rejected) as exc: result[label]=str(exc)
                else: raise AssertionError(f"{strategy} {name} {label} unexpectedly passed")
            cases[name]=result
        records[strategy]=dict(positive=True, completeStandard=True, shortStandard=True, txt=text, faults=cases)
    return dict(sourceCommit=PIN, records=records)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    result=compare()
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print({k:len(v["faults"]) for k,v in result["records"].items()})
