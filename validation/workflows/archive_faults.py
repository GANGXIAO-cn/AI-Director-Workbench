"""One changed production condition per sample, checked from a workbench receipt and the pinned source."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from historical_baseline import ROOT, SKILL, PIN

sys.path[:0] = [str(ROOT/"server"),str(SKILL/"tests")]
from workbench.archivefiles import capture, ArchiveFiles
from workbench.archivechecks import assess
from workbench.storage import Store
from workbench.history import check_manifest
from workbench.images import png_bytes


def compare():
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","templates","tests"],check=True)
    from test_character_integration import CharacterIntegration
    fixture = CharacterIntegration()
    fixture.setUp()
    import test_support
    from validate_episode import validate, validate_v2_segments, v2_assets, check_appearance
    from check_assets import verify
    from character_contract import check_contract
    from review_evidence import validate_review_evidence
    project = test_support.ASSETS.resolve()
    fixed = verify(project)
    registry = [dict(r,path=str(Path(r["path"]).relative_to(project))) for r in fixed["assets"]]
    standards = dict(promptStandard="制作基准/视频提示词标准.json",characterStandard="制作基准/角色外观标准.json",
                     silentRoles=["milo","leo"])
    directory=Path(tempfile.mkdtemp(prefix="adw-archive-rules-")).resolve()
    store=Store(directory/"store")
    mutations = {
        "unknown_appearance_role": ("appearance", lambda d,a,s,r: s.update(on_screen_roles=["unknown"]), "on_screen_roles缺失或含未知/重复角色"),
        "repeat_appearance_role": ("appearance", lambda d,a,s,r: s.update(on_screen_roles=["girl","girl"]), "on_screen_roles缺失或含未知/重复角色"),
        "missing_appearance_roles": ("appearance", lambda d,a,s,r: s.pop("on_screen_roles"), "on_screen_roles缺失或含未知/重复角色"),
        "episode_appearance_role": ("appearance", lambda d,a,s,r: None, None),
        "fixed_appearance_missing": ("appearance", lambda d,a,s,r: s.update(on_screen_roles=["milo"]), "视频短标准须在角色定义区逐字且仅引用一次"),
        "schema_type": ("manifest", lambda d,a,s,r: d.update(schema_version=True), "须声明 schema_version=2"),
        "legacy_field": ("manifest", lambda d,a,s,r: d.update(asset_snapshot="obsolete.json"), "每集标准快照"),
        "h3_version": ("manifest", lambda d,a,s,r: d.update(h3_rules="v0"), "须声明 h3_rules=v1"),
        "manifest_generation_ratio": ("manifest", lambda d,a,s,r: d.update(generation_settings={"aspect_ratio":"1:1"}), "生成设置画幅"),
        "segment_generation_ratio": ("manifest", lambda d,a,s,r: s.update(generation_settings={"aspect_ratio":"16:9"}), "生成设置画幅"),
        "chinese_title": ("manifest", lambda d,a,s,r: d.update(english_title="Stand 台"), "英文视频标题"),
        "unknown_policy": ("manifest", lambda d,a,s,r: d.update(duration_policy="free"), "非法片长"),
        "zero_length": ("manifest", lambda d,a,s,r: d.update(total_duration_s=0), "非法片长"),
        "sixty_seconds": ("manifest", lambda d,a,s,r: d.update(duration_policy="over_60",total_duration_s=60), "严格大于60秒"),
        "sum_differs": ("inputs", lambda d,a,s,r: d.update(total_duration_s=d["total_duration_s"]+1), "段落时长之和"),
        "missing_asset_table": ("assets", lambda d,a,s,r:d.update(assets=[]),"缺少资产表"),
        "duplicate_asset": ("assets", lambda d,a,s,r:d["assets"].append(dict(a)),"重复资产ID"),
        "invalid_type": ("assets", lambda d,a,s,r:a.update(type="unknown"),"非法资产媒体类型"),
        "invalid_category": ("assets", lambda d,a,s,r:a.update(category="other"),"非法资产类别"),
        "missing_purpose": ("assets", lambda d,a,s,r:a.update(purpose=""),"缺少文本字段"),
        "origin": ("assets", lambda d,a,s,r:a.update(origin="maybe"),"资产来源方式非法或未审核"),
        "local_absolute": ("assets", lambda d,a,s,r:a.update(path=str(fixture.root/"scene.png")),"非固定角色资产必须"),
        "outside": ("assets", lambda d,a,s,r:a.update(path="../outside.png"),"文件缺失或超出本集目录"),
        "repeated_path": ("assets", lambda d,a,s,r:d["assets"].append(dict(a,id="another")),"同一文件不能重复注册"),
        "wrong_hash": ("assets", lambda d,a,s,r:a.update(sha256="0"*64),"资产指纹不符"),
        "no_segments": ("prompts", lambda d,a,s,r:d.update(segments=[]),"缺少生成段"),
        "duplicate_segment": ("prompts", lambda d,a,s,r:d["segments"][1].update(id=s["id"]),"重复段号"),
        "time_type": ("prompts", lambda d,a,s,r:s.update(duration_s=True),"非法时间数值"),
        "duration": ("prompts", lambda d,a,s,r:s.update(duration_s=16),"时间不连续"),
        "start_gap": ("prompts", lambda d,a,s,r:s.update(start_s=0.01),"时间不连续"),
        "strategy": ("prompts", lambda d,a,s,r:s.update(reference_strategy="free"),"非法reference_strategy"),
        "labels": ("prompts", lambda d,a,s,r:s.update(label_style="short"),"未知label_style"),
        "no_input": ("prompts", lambda d,a,s,r:s.update(references=[]),"缺少实际输入"),
        "upload_order": ("prompts", lambda d,a,s,r:r.update(upload_order=True),"输入上传顺序"),
        "unknown_asset": ("prompts", lambda d,a,s,r:r.update(asset_id="missing"),"引用未声明资产"),
        "repeat_label": ("prompts", lambda d,a,s,r:s["references"][1].update(label=r["label"]),"重复标签"),
        "wrong_label": ("prompts", lambda d,a,s,r:r.update(label="图片9"),"输入顺序与标签"),
        "repeat_file": ("prompts", lambda d,a,s,r:s["references"][1].update(asset_id=r["asset_id"]),"同段重复输入"),
        "shot_scope": ("prompts", lambda d,a,s,r:r.update(shots=[]),"缺少作用镜头"),
        "shared_txt": ("prompts", lambda d,a,s,r:d["segments"][1].update(prompt=s["prompt"]),"每段须独立提示词文件"),
        "grid_frame": ("prompts", lambda d,a,s,r:d["segments"][1].update(keyframes_s=[0,1]),"grid须有9个合法关键状态"),
        "ratio_syntax": ("prompts", lambda d,a,s,r:d.update(aspect_ratio="x:y"),"aspect_ratio须为正数"),
        "ratio_zero": ("prompts", lambda d,a,s,r:d.update(aspect_ratio="0:1"),"画幅宽高必须大于0"),
        "grid_ratio": ("prompts", lambda d,a,s,r:d.update(aspect_ratio="1:1"),"九宫格须为与本集"),
        "unreviewed": ("prompts", lambda d,a,s,r:s.update(review_status="pending"),"本段未审核"),
        "cast_waiver": ("cast", lambda d,a,s,r:d["cast"]["girl"].update(identity_required=False,reason=""),"免独立身份基准须说明理由"),
        "cast_mismatch": ("cast", lambda d,a,s,r:s.update(identity_bindings={}),"身份绑定"),
        "review_episode": ("reviews", None,"episode_id不匹配"),
        "user_critical": ("reviews", None,"关键缺陷未解决"),
        "waiver_missing_instruction": ("reviews",None,"指令未记入依据"),
        "user_accepted": ("reviews",None,None),
        "header": ("assets",None,"无法识别图片文件头"),
        "empty_size": ("assets",None,"图片尺寸非法"),
        "fixed_path": ("assets",None,"固定角色不匹配现行角色基准"),
        "fixed_category": ("assets",None,"固定角色文件不可用"),
        "image_count": ("prompts",None,"每段图片必须为1至9张"),
        "media_limits": ("prompts",None,"实际入口媒体数量限制"),
        "mixed_limits": ("prompts",None,"实际入口混合总限制"),
        "grid_extra": ("prompts",None,"grid必须仅输入一张九宫格"),
    }
    txt_changes = {
        "heading_missing":("【开场】","【起幅】","缺少或重复约定中文段落"),
        "heading_order":("【开场】","【镜头与动作】","提示词章节顺序错误"),
        "subject_token":("桌面。","桌面。@Subject9","未声明的主体标签"),
        "extra_ref":("桌面。","桌面与图片8。","提示词引用未声明标签"),
        "empty_definition":("图片1：桌面。","图片1：。","参考标签必须有且只有一个非空定义"),
        "outside_time":("00:10.000","00:11.000","时间码未覆盖本段"),
    }
    mutations.update({k:("prompts",None,v[2]) for k,v in txt_changes.items()})
    mutations["grid_definition"]=("prompts",None,"参考定义缺少输入标签")
    mutations["positive"]=("prompts",None,None)
    cases={}
    for name,(key,fn,expected) in mutations.items():
        root=directory/name
        shutil.copytree(fixture.root,root)
        d=deepcopy(fixture.data)
        a=d["assets"][0]
        s=d["segments"][0]
        r=s["references"][0]
        if fn:
            fn(d,a,s,r)
        elif name in txt_changes:
            path=root/s["prompt"]
            before,after,_=txt_changes[name]
            old=path.read_text()
            if name=="heading_order":
                old=old.replace(after,"【交换】")
            text=old.replace(before,after).replace("【交换】",before)
            assert text!=path.read_text(),name
            path.write_text(text)
        elif name=="grid_definition":
            path=root/d["segments"][1]["prompt"]
            path.write_text(path.read_text().replace("图片1：","构图：",1)+"\n图片1")
        elif key=="reviews":
            path=root/d["review_evidence"]
            evidence=json.loads(path.read_text())
            if name=="review_episode":
                evidence["episode_id"]="other"
            else:
                issue=dict(id="Q1",severity="critical",status="accepted",description="保留此画面",segments=[s["id"]],evidence_path="review.md")
                evidence["issues"]=[issue]
                if name!="user_critical":
                    instruction="保留本段原动作，不换图。"
                    issue["user_direction"]=dict(stage="closing_manual_revision",instruction=instruction,evidence_path="决定.md")
                    (root/"决定.md").write_text(instruction if name=="user_accepted" else "等待意见")
            path.write_text(json.dumps(evidence))
        elif name in ("header","empty_size"):
            raw=b"wrong" if name=="header" else b"\x89PNG\r\n\x1a\n"+bytes(24)
            (root/a["path"]).write_bytes(raw);a["sha256"]=hashlib.sha256(raw).hexdigest()
        elif name in ("fixed_path","fixed_category"):
            item=fixed["assets"][0]
            a.update(fixed_role=item["id"],path=item["path"],sha256=item["sha256"])
            if name=="fixed_path":a["path"]=str(root/"scene.png")
            else:a["category"]="prop"
        elif name in ("media_limits","mixed_limits"):
            (root/"sound.wav").write_bytes(b"sound")
            d["assets"].append(dict(a,id="sound",type="audio",category="sound",path="sound.wav",sha256=hashlib.sha256(b"sound").hexdigest()))
            s["references"].append(dict(r,asset_id="sound",label="音频1",upload_order=3))
            s["verified_input_limits"]=dict(source="test",image=2,video=0,audio=0 if name=="media_limits" else 1,total=2)
        elif name=="image_count":
            for i in range(3,11):
                raw=png_bytes(9,16,(i,5,0));aid=f"extra{i}";path=aid+".png"
                (root/path).write_bytes(raw);d["assets"].append(dict(a,id=aid,path=path,sha256=hashlib.sha256(raw).hexdigest()))
                s["references"].append(dict(r,asset_id=aid,label=f"图片{i}",upload_order=i))
        elif name=="grid_extra":
            d["segments"][1]["references"][0]["asset_id"]="scene"
        (root/"episode.json").write_text(json.dumps(d))
        hashes,meta=capture(root,{"episode.json":(root/"episode.json").read_bytes()},store,registry,project,standards)
        native = check_manifest(d) if key == "manifest" else assess(ArchiveFiles(hashes,meta,store),d)[key]
        source_issue=None
        try:
            if key in ("manifest","inputs"):validate(root/"episode.json")
            elif key=="assets":v2_assets(root,d,fixed)
            elif key=="prompts":validate_v2_segments(root,d,fixed)
            elif key=="cast":check_contract(root,d)
            elif key=="appearance":check_appearance(root,d,fixed)
            else:validate_review_evidence(root/"episode.json")
        except ValueError as exc:
            source_issue=str(exc)
        if expected is None:
            assert source_issue is None and native["state"]=="passed",(name,source_issue,native)
        else:
            assert source_issue and expected in source_issue and native["state"]=="failed" and native["message"]==source_issue,(name,source_issue,native)
        cases[name]=dict(matchedRule=True,sourceOK=source_issue is None,workbenchOK=native["state"]=="passed",issue=source_issue)
    return dict(sourceCommit=PIN,cases=cases)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    result=compare()
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    print(len(result["cases"]),"matched")
