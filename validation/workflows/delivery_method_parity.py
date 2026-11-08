"""Compare v6 publish and handoff passages to each pinned nonblank line and all inherited line audits."""
import json

from historical_baseline import ROOT, PIN
from production_method_parity import audit, story_audit, OUT

CHANGES = {
    "publish-pack": {3:"项目设置与工作台交付路径", 11:"项目标题语言、前缀与主角", 15:"角色与固定行来自项目",
                     25:"禁用字符取自设置", 33:"通用配乐", 41:"发布字段按项目检查", 46:"工作台出包入口和项目字段",
                     48:"音乐段按项目配置"},
    "templates/publish-pack.md": {1:"使用项目标题语言",3:"按本项目字段与交付入口",8:"本项目的标题前缀与主角",
                                 14:"项目角色与结尾标记",15:"固定行按项目提供",16:"允许项目配置不同的行数",
                                 22:"标签上限取自项目"},
    "finecut-handoff": {3:"实际工作台交付路径与模板入口",37:"故事组来源"},
    "templates/finecut-handoff.md": {1:"交接标题使用项目语言"},
}
# The shell fencing and command belong to the original CLI. The same fixtures exercise this
# checker via real historical import. No CLI command belongs in the author's publication prompt.
W = {"references/publish-pack.md": dict.fromkeys((37,38,39),"server/workbench/publishing.py")}

if __name__=="__main__":
    pack=ROOT/"methods/short-film/v6"
    manifest=json.loads((pack/"manifest.json").read_bytes())
    reports=[
        ("milo-leo-short-video","发布与精剪交接逐行去向",audit(pack,CHANGES,W)),
        ("milo-leo-short-video","四份制作参考逐行去向",audit(pack)),
        ("milo-leo-story","故事标准写法与大纲逐行去向",story_audit(pack)),
    ]
    for repo,name,files in reports:
        data=dict(pack=manifest["id"],repo=repo,commit=manifest["source"][repo],files=files)
        (OUT/("v6_"+name+".json")).write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
        print(name,[(k,v["counts"]) for k,v in files.items()])
