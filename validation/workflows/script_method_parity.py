"""Audit frozen script, group and example passages and recheck the inherited releases."""
import json

from delivery_method_parity import CHANGES as DELIVERY, W
from historical_baseline import ROOT
from production_method_parity import audit, story_audit, OUT

CHANGES = {
    "story-handoff": {3:"采用工作台故事阶段的交接入口",13:"使用采用版本和独审记录核对交接"},
    "story-ip": {3:"路由到故事阶段",7:"主角由项目提供",16:"角色与服装由项目提供",23:"主角由项目提供"},
    "series-continuity": {3:"系列设定与台账由项目提供",9:"项目的主角与单集重点角色",19:"计划规模由项目提供"},
    "continuous-production": {},
    "examples": {},
}
if __name__ == "__main__":
    pack=ROOT/"methods/short-film/v7"
    manifest=json.loads((pack/"manifest.json").read_bytes())
    for repo, name, files in [
        ("milo-leo-short-video","剧本交接与系列案例逐行去向",audit(pack,CHANGES)),
        ("milo-leo-short-video","发布与精剪交接逐行去向",audit(pack,DELIVERY,W)),
        ("milo-leo-short-video","四份制作参考逐行去向",audit(pack)),
        ("milo-leo-story","故事标准写法与大纲逐行去向",story_audit(pack)),
    ]:
        result=dict(pack=manifest["id"],repo=repo,commit=manifest["source"][repo],files=files)
        (OUT/("v7_"+name+".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
        print(name,[(key,row["counts"]) for key,row in files.items()])
