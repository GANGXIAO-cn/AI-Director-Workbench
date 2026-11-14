"""Read the selected stage's evidence. Opening the checklist never starts an executor."""
import json

from . import imagechoices, stages, timeline, workflow
from .db import connect
from .enhancements import entries
from .errors import Rejected
from .specs import validate_spec
from .storage import file_sha256

NAMES = dict(story="故事稿",script="剧本",plan="制作方案",space="空间锁定",cast="角色出镜",assets="素材与选图",
             prompts="提示词",review="08分镜审核",publish="发布包",delivery="交付核验",package="H3 出包")
NAMES.update(workflow.LABELS)
FORMATS = dict(story="故事结构与交接字段",script="分场剧本结构",plan="镜号、时长与镜头结构",
               prompts="提示词结构与实际引用",publish="项目发布格式与字段")
FORMATS.update({stage: label + "结构" for stage, label in workflow.STAGES if stage in workflow.FIELDS})
PREVIOUS = {stage:prior for prior, stage in stages.NEXT.items()}


def check(label, ok, reason):
    return dict(label=label,status="通过" if ok else "未通过",reason=reason)


def checks_for_errors(label, problems, success):
    return [check(label,not problems,"；".join(problems) if problems else success)]


def version_methods(wb, db, version):
    result = {}
    for run in wb._version_creation_runs(db,version):
        manifest,digest = wb.store.load_manifest(run["manifest_relpath"])
        if digest != run["input_hash"]:
            raise Rejected("稿件来源的输入清单指纹不符")
        files = manifest.get("methodPack",{}).get("files",{})
        for entry in manifest["inputs"]:
            source = files.get(entry["name"],{})
            codes = (source.get("source") or {}).get("enhancementIds",[])
            if not codes or entry["role"]!="method":
                continue
            if source.get("sha256") != entry["sha256"]:
                raise Rejected("增强方法来源与本版输入指纹不符")
            result[entry["sha256"],entry["name"]] = dict(name=entry["name"],methodSource=source,
                                      text=wb.store.read_blob(entry["sha256"]).decode("utf-8"))
    return list(result.values())


def write_checks(wb, db, eid, stage, art, version):
    checks = []
    if workflow.DEPENDENCIES.get(stage):
        try:
            wb._require_workflow_inputs(db, eid, stage)
            checks.append(check("本阶段上游成果", True, "必需成果已采用，可选阶段已采用或记录跳过理由"))
        except Rejected as error:
            checks.append(check("本阶段上游成果", False, str(error)))
    if not version:
        return checks+[check("本阶段稿件",False,"尚无可检查的版本")]
    gate = wb.adoption_gate(version["id"],db)
    checks += checks_for_errors(FORMATS[stage],gate["formatErrors"],"该版通过工作台的结构校验")
    if stage == "story":
        review = gate["review"]
        checks.append(check("本版独立审查（可选）",bool(review) or not gate.get("reviewRequired"),
                            f"已登记第 {review['passNo']} 遍审查" if review else "未执行补充独审；不作为采用必需条件"))
        if review:
            remaining = set(gate["blocking"]+gate["unresolved"])
            rows = db.execute("SELECT i.id,i.reason,w.reason waived FROM issue i LEFT JOIN waiver w ON w.issue_id=i.id "
                              "AND w.version_id=? WHERE i.review_id=? ORDER BY i.ordinal",(version["id"],review["id"]))
            for issue in rows:
                reason = issue["reason"] + (" · 放行理由："+issue["waived"] if issue["waived"] else "")
                checks.append(check("独审意见",issue["id"] not in remaining,reason))
    if stage == "plan" and not gate["formatErrors"]:
        lay = timeline.layout(json.loads(version["content"]))
        try:
            spec = validate_spec(wb._kickoff(db,eid)["spec"])
            maximum = spec["segmentMaxSeconds"]
            for seg in lay["segments"]:
                duration = seg["duration_ms"]/1000
                checks.append(check(seg["id"]+" 段长",duration<=maximum,f"设计 {duration:g} 秒，本集上限 {maximum} 秒"))
            if spec.get("targetDuration")=="over_60":
                checks.append(check("全片时长",lay["total_ms"]>60000,f"设计 {lay['total_ms']/1000:g} 秒，本集要求超过 60 秒"))
        except Rejected as error:
            checks.append(check("本集规格",False,str(error)))
    adopted = version["id"] == art["adopted_version_id"]
    checks.append(check("导演采用",adopted,"本集正在使用这一版" if adopted else "这一版尚未采用为当前稿"))
    return checks


def review_checks(wb, db, eid, version):
    if not version:
        return [check("分镜版本",False,"先保存分镜稿")]
    gate=wb.adoption_gate(version['id'],db)
    if not gate['review']:
        if version.get('base_id') and wb._latest_review(db,version['base_id']):
            return [dict(label='08分镜独审',status='已修订，未另审',reason='审稿记录保留在上一版；本版已修订，不再追加整体独审')]
        return [dict(label="08分镜独审",status="未启用",reason="本版无独审记录；09仍在写作内检查自身结果")]
    return [check("08分镜审核",not gate['blocking'] and not gate['unresolved'],
                  "原结论："+gate['review']['decision'])]


def asset_checks(wb, db, eid):
    plan = wb._adopted_plan(db,eid) or {}
    segments = [s["id"] for s in plan.get("segments",[])]
    if not segments:
        return [check("素材对应的生成段",False,"须先采用制作方案")]
    prompt = wb._adopted_content(db,eid,"prompts") or {}
    used,checks = {},[]
    for sid in segments:
        entry = prompt.get("segments",{}).get(sid,{})
        refs = entry.get("refs",[])
        image_refs = [r for r in refs if r.get("kind")=="image"]
        errors = wb._prompt_reference_errors(db,eid,{sid:entry}) if refs else []
        if not image_refs:
            errors.append("该段采用的提示词尚未引用图片")
        checks += checks_for_errors(sid+" 素材引用",errors,f"已关联 {len(image_refs)} 张图片")
        for ref in image_refs:
            asset = db.execute("SELECT v.sha256 FROM asset_version v JOIN asset a ON a.id=v.asset_id "
                               "JOIN episode e ON e.project_id=a.project_id WHERE e.id=? AND v.id=? "
                               "AND a.kind='image' AND (a.episode_id IS NULL OR a.episode_id=e.id)",(eid,ref.get("assetVersionId"))).fetchone()
            if asset:
                used.setdefault(asset[0],set()).add(sid)
    rules = imagechoices.requirements(db,eid)
    if rules:
        _,problems = imagechoices.check_scope(rules,plan,"full",segments,used)
        checks += checks_for_errors("必用图覆盖",[p["file"]+" · "+p["problem"] for p in problems],f"{len(rules)} 张必用图均已用于指定范围")
    placements = wb.product_placements(eid)
    if placements["minimum"]:
        checks.append(check("商品入景覆盖",placements["verified"]>=placements["minimum"],
                            f"已核验 {placements['verified']}，项目要求至少 {placements['minimum']}"))
    return checks


def output_checks(wb, db, eid, stage, delivery_id, scope, segments):
    if delivery_id:
        delivery = db.execute("SELECT * FROM delivery WHERE episode_id=? AND id=?",(eid,delivery_id)).fetchone()
        if not delivery:
            raise Rejected("此交付记录不属于本集")
    elif stage=="delivery":
        candidates = db.execute("SELECT * FROM delivery WHERE episode_id=? AND scope=? ORDER BY created_at DESC,rowid DESC",(eid,scope))
        delivery = next((d for d in candidates if scope=="full" or set(json.loads(d["segments"]))==set(segments or [])),None)
    else:
        delivery = db.execute("SELECT * FROM delivery WHERE episode_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",(eid,)).fetchone()
    checks = []
    if stage=="delivery":
        problems,_ = wb.delivery_problems(eid,scope,segments)
        checks += checks_for_errors("当前整集的交付条件" if scope=="full" else "所选试验段的交付条件",
            [" · ".join(filter(None,[NAMES.get(p["stage"],""),p.get("segment"),p.get("file"),p["problem"]])) for p in problems],
            "已采用的内容通过所选范围的交付核验")
    if not delivery:
        return checks+[check("交付记录",False,"所选范围还没有已生成的图文交付")]
    scope = "整集" if delivery["scope"]=="full" else "试验段"
    basis = scope+"交付 "+delivery["id"][:8]
    checks.append(check("已生成的交付",True,basis+" · "+"、".join(json.loads(delivery["segments"]))))
    if stage=="delivery":
        current = dict(db.execute("SELECT stage,adopted_version_id FROM artifact WHERE episode_id=?",(eid,)))
        saved = json.loads(delivery["versions"])
        needed = FORMATS if delivery["scope"]=="full" else ("plan","prompts")
        changed = [NAMES[key] for key in needed if saved.get(key)!=current.get(key)]
        checks += checks_for_errors("交付与当前采用版本",["已换稿："+name for name in changed],"采用版本与该次交付一致")
        report = wb.verify_delivery(delivery["id"])
        checks += checks_for_errors("交付文件与登记清单",
            [e["file"]+" · "+e["problem"] for e in report["errors"]],basis+"的文件与登记清单一致")
    else:
        packs = db.execute("SELECT * FROM package WHERE delivery_id=? ORDER BY created_at DESC,rowid DESC",(delivery["id"],)).fetchall()
        if not packs:
            checks.append(check("导出 ZIP",False,basis+"尚无 ZIP 导出记录"))
        for pack in packs:
            try:
                path = wb.store._inside(wb.store.root/pack["relpath"])
                ok = file_sha256(path)==pack["sha256"]
                reason = path.name+(" · 与导出指纹一致" if ok else " · 文件指纹已改变")
            except (ValueError,OSError):
                ok,reason = False,"登记的 ZIP 文件缺失或不可读取"
            checks.append(check("ZIP · "+"、".join(json.loads(pack["segments"])),ok,reason))
    return checks


def stage_checklist(wb, episode_id, stage, version_id=None, delivery_id=None, scope="full", segments=None):
    if scope not in ("full","trial") or segments is not None and (not isinstance(segments,list) or not all(isinstance(s,str) for s in segments)):
        raise Rejected("请选择有效的交付范围")
    if stage not in NAMES:
        raise Rejected("未知制作阶段")
    with connect(wb.db_path) as db:
        db.execute("BEGIN")
        wb._kickoff(db,episode_id)
        version_stage = "plan" if stage=="review" else stage
        version = None
        if version_stage in FORMATS:
            art = db.execute("SELECT * FROM artifact WHERE episode_id=? AND stage=?",(episode_id,version_stage)).fetchone()
            if version_id and not art:
                raise Rejected("此阶段没有对应版本")
            if art:
                vid = version_id or art["adopted_version_id"] or wb._latest_version(db,art["id"])
                if vid:
                    version = wb._version_of(db,vid,art["id"])
        elif version_id:
            raise Rejected("此阶段不接受稿件版本")
        skip = db.execute("SELECT reason FROM optional_stage_choice WHERE episode_id=? AND stage=? AND reason<>''",
                          (episode_id,stage)).fetchone()
        if skip:
            checks = [dict(label=NAMES[stage],status="已跳过",reason=skip[0])]
        elif stage in FORMATS:
            checks = write_checks(wb,db,episode_id,stage,art,version)
        elif stage=="review":
            checks = review_checks(wb,db,episode_id,version)
        elif stage=="space":
            locks = wb._space_locks(db,episode_id)
            checks = [check(r["label"],r["status"]=="confirmed","已确认空间关系" if r["status"]=="confirmed" else
                            "待确认空间关系，涉及 "+("、".join(r["segments"]) or "未指定段")) for r in locks]
            if not locks:
                checks = [check("空间关系登记",False,"尚无空间锁定项；本集无需锁定时可在本阶段登记跳过理由")]
        elif stage=="cast":
            matrix = wb._cast_matrix(db,episode_id)
            checks = []
            for seg in matrix["rows"]:
                for role in matrix["roles"]:
                    cell = seg["cells"].get(role,{})
                    status = cell.get("status")
                    bound = bool(cell.get("assetVersionId"))
                    checks.append(check(seg["segment"]+" · "+role,status=="absent" or status=="ok" and bound,
                        {"absent":"本段未出镜","ok":"已关联身份图" if bound else "缺少身份图",
                         "unclear":"身份不清，需补图或调整动作"}.get(status,"尚未确认出镜状态")))
            if not checks:
                checks = [check("逐段出镜记录",False,"需有采用方案的生成段与角色记录；本集无需检查时可登记跳过理由")]
        elif stage=="assets":
            checks = asset_checks(wb,db,episode_id)
        else:
            checks = output_checks(wb,db,episode_id,stage,delivery_id,scope,segments)
        config_stage = "assets" if stage in ("space","cast","assets") else version_stage
        configured = wb.step_config(episode_id,config_stage).get("enhancements",[]) if config_stage in (*FORMATS,"assets") else []
        methods = entries(configured)
        return dict(stage=stage,version=version and dict(id=version["id"],number=version["display_no"]),
                    checks=checks,methodStage=NAMES[config_stage],methods=methods,
                    creationMethods=version_methods(wb,db,version) if version else [])
