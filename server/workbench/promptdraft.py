"""Deterministic draft for the user-facing '从方案起草提示词' action."""
import json
import re
from . import timeline, readable, acting, imagechoices, shotrefs
from .db import connect
from .errors import Rejected
from .delivery import fmt

def draft(wb, eid):
    with connect(wb.db_path) as db:
        ep = wb._kickoff(db, eid)
        plan = wb._adopted_content(db,eid,"plan")
        if not plan: raise Rejected("请先采用本集制作方案")
        files = {r["role"]:r["text"] for r in wb._source_entries(db,eid,"prompts")}
        choices = [dict(r) for r in db.execute("SELECT * FROM image_choice WHERE episode_id=? AND mark IN ('required','adopted') ORDER BY created_at,id",(eid,))]
    blocks=json.loads(files.get("promptStandard","{}")).get("blocks",{})
    standards=json.loads(files.get("characterStandard","{}"))
    roles=standards.get("roles",{})
    matrix = wb.cast_matrix(eid)
    cast={r["segment"]:r["cells"] for r in matrix["rows"]}
    assets={a["id"]:a for a in wb.episode_assets(eid) if a["kind"]=="image"}
    lay=timeline.layout(plan)
    requirements = [a["requirement"] for a in assets.values() if "requirement" in a]
    used = {}
    limits_by_hash = {}
    for rule in requirements:
        if rule["limitations"]:
            limits_by_hash.setdefault(rule["sha256"], []).append(rule["limitations"])
    byid={s["id"]:s for s in plan["shots"]}
    readings = acting.report(plan)
    if readings["errors"]:
        raise Rejected("；".join(readings["errors"]))
    spoken, voices = {}, {}
    for line in readings["rows"]:
        spoken.setdefault(line["shot"], []).append(line)
        for later in line["continuingShots"]:
            if byid[later].get("continuation") != "same":
                voices.setdefault(later, []).append(line["speaker"])
    board_scope = {}
    if shotrefs.plan_enabled(plan):
        for row,shot in zip(shotrefs.board_rows(plan), (w for seg in lay["segments"] for w in seg["shots"]), strict=True):
            for role in re.split(r"[、,，\s]+", row[shotrefs.ROLES]):
                board_scope.setdefault((row["段ID"],role), []).append(shot)
    out={}
    for seg in lay["segments"]:
        sid=seg["id"]; grid=seg["strategy"]=="grid"
        refs={}; identities=[]; lines=[]; role_ids=set(); bound_text={}
        def add(vid, use, shot_ids=()):
            if vid not in assets: raise Rejected(f"{sid} 的{use}不是本集可用图片")
            if vid in refs:
                if use not in refs[vid]["role"].split("、"): refs[vid]["role"] += "、"+use
            else:
                refs[vid]=dict(assetVersionId=vid,label=f"图片{len(refs)+1}",kind="image",role=use,grid=bool(assets[vid]["grid"]))
            if shot_ids:
                target = refs[vid].setdefault("shots", [])
                target.extend(s for s in shot_ids if s not in target)
        for s in seg["shots"]:
            shot=byid[s]; t=lay["shots"][s]
            if not grid:
                for fld,use in [("startAssetId","起幅"),("endAssetId","落幅")]:
                    if shot.get(fld): add(shot[fld],s+use,[s])
            marker = "起镜" if not lines else ("同镜延续" if shot.get("continuation") == "same" else "切镜")
            action = "，".join(str(shot[k]).strip().rstrip("。，") for k in ("camera","action","performance","gaze","reaction","sound") if shot.get(k))
            for speaker in voices.get(s, []):
                action += f"。<scenetrans> 沿用上一镜{speaker}的声音，继续说完原句"
            for cue in spoken.get(s, []):
                direction = f"（{cue['direction']}）" if cue["direction"] else ""
                across = "，声音越过切点连续 <scenetrans>" if any(byid[k].get("continuation") != "same" for k in cue["continuingShots"]) else ""
                action += (f"。段内 {cue['start_ms']/1000:g} 秒起，{cue['speaker']}{direction}说「{cue['text']}」"
                           f"，用 {cue['speech_ms']/1000:g} 秒说完，句后停 {cue['pause_ms']/1000:g} 秒{across}")
            lines.append(f"{fmt(t['seg_start_ms'])}-{fmt(t['seg_end_ms'])}\n{marker}，{action}。")
        for role,binding in cast.get(sid,{}).items():
            if binding["status"]!="ok": continue
            if not grid and binding.get("assetVersionId"): add(binding["assetVersionId"],role+"身份",board_scope.get((sid,role), ()))
            baseline = matrix["roster"].get(role, {})
            description = baseline.get("appearance")
            std=next((v for k,v in roles.items() if role.casefold() in (k.casefold(),v["name"].casefold())),None)
            if description:
                if not std or description not in std.get("video_zh", ""):
                    identities.append(role+"："+description)
                if grid and binding.get("assetVersionId") and assets[binding["assetVersionId"]]["grid"]:
                    add(binding["assetVersionId"],"本段九宫格",seg["shots"])
                elif not grid and std and baseline.get("assetVersionId"):
                    add(baseline["assetVersionId"],role+"固定身份原图",board_scope.get((sid,role), ()))
            if std and std.get("video_zh"):
                if grid:
                    identities.append(std["video_zh"])
                else:
                    vid = next((v for v in refs if assets[v]["sha256"] == std.get("sha256")), None)
                    if not vid:
                        raise Rejected(f"{sid} 缺少 {role} 的固定身份原图，请在角色页登记并绑定")
                    bound_text[vid] = std["video_zh"]
                role_ids.add(next(k for k,v in roles.items() if v is std))
        for c in choices:
            vid=c["asset_version_id"]; shots=set(json.loads(c["shot_ids"]))
            requirement = assets.get(vid, {}).get("requirement", {}) if c["mark"]=="required" else {}
            wanted = sid in requirement.get("targetSegments", [])
            if requirement and not requirement["targetSegments"] and not shots and len(lay["segments"]) == 1:
                wanted = True
            if wanted and bool(assets[vid]["grid"]) != grid:
                raise Rejected(f"{sid} 的必用图与本段参考策略不匹配，请核对目标段或制作方案")
            if vid in assets and bool(assets[vid]["grid"])==grid and (wanted or shots.intersection(seg["shots"])):
                if wanted:
                    vid = next((bound for bound in refs if assets[bound]["sha256"] == assets[vid]["sha256"]), vid)
                add(vid,"本段九宫格" if grid else "镜头参考",[s for s in seg["shots"] if s in shots or (wanted and grid and not shots)])
        if not refs:
            candidates=[a for a in assets.values() if bool(a["grid"])==grid and a["category"] != "product_reference"
                        and a["origin"] != "kickoff_reference"]
            if grid: candidates=[a for a in candidates if a["name"].split(" ")[0]==sid or a["name"].startswith(sid+"-")]
            if len(candidates)!=1: raise Rejected(f"{sid} 请先为本段镜头明确选图：在镜卡绑定起落幅，或为本段标记必用图。")
            add(candidates[0]["id"],"本段九宫格" if grid else "场面参考",seg["shots"] if grid else ())
        if grid and len(refs)!=1: raise Rejected(f"{sid} 有多张九宫格匹配，请明确本段采用版本")
        def fixed(key):
            return blocks.get(key,{}).get("zh","")
        def join(*values):
            return "\n".join(v for v in values if v)
        if {"milo","leo"} <= role_ids:
            pair = standards.get("pair_rules",{}).get("milo+leo",{}).get("video_zh")
            if pair:
                identities.append(pair)
        definitions = []
        for vid, ref in refs.items():
            definition = ref["label"] + "：" + bound_text.get(vid, ref["role"] + "。")
            digest = assets[vid]["sha256"]
            used.setdefault(digest, set()).add(sid)
            limits = "；".join(dict.fromkeys(limits_by_hash.get(digest, [])))
            if limits:
                definition += "用途限制：" + limits
            definitions.append(definition)
        definitions = join(*definitions, fixed("reference_use"))
        visual = join(fixed("visual_style"), fixed("motion"), ep["spec"].get("style"))
        opening = byid[seg["shots"][0]].get("opening") or byid[seg["shots"][0]]["action"]
        audio = join(ep["spec"].get("sound"), fixed("cats_silent") if role_ids & {"milo","leo"} else "", "non_diegetic_music: N/A")
        if grid:
            parts = dict(zip(readable.GRID, [join(definitions, fixed("grid_reference")),join(*identities) or "按本段九宫格中的角色。",
                         visual or "沿用本段九宫格的空间。",opening,join(*lines),"保持本段已列机位与真实剪点。",
                         audio,fixed("frame_text") or "按本段方案。"]))
        else:
            parts = dict(zip(readable.FLEX, [join(definitions,*identities),visual or "按采用的场景、角色与镜头方案。",
                         opening,join(*lines),join(audio,fixed("frame_text"))]))
        out[sid]=dict(refs=list(refs.values()), txt=readable.render(parts), prompt_format="readable_v3",
                      blocks=parts, sound=dict(bgm=False,silent=False,ambience=[],
                                              dialogue=[cue["text"] for shot in seg["shots"] for cue in spoken.get(shot, [])]))
    _, missing = imagechoices.check_scope(requirements, plan, "full", out, used)
    if missing:
        raise Rejected("请在素材页确认必用图的目标段：" + "、".join(dict.fromkeys(p["file"] for p in missing)), detail=missing)
    return dict(segments=out)
