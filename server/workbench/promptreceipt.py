"""Compare review declarations with the actual bytes of the segment TXT and its ordered images."""
import json
from pathlib import Path

from . import shotrefs
from .review_records import records, require_record
from .storage import sha256_bytes


def read_evidence(read_local, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("审核证据必须使用非空的本集相对路径")
    try:
        return read_local(name)
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"审核证据文件缺失或越界：{name}") from exc


def inputs(data, read_local, read_asset):
    if data.get("schema_version") != 2:
        raise ValueError("须使用schema_version=2")
    sources = data.get("prompt_review")
    if not isinstance(sources, dict):
        raise ValueError("prompt_review须列出requirements、story、plan来源路径")

    for field in ("requirements", "story", "plan"):
        read_evidence(read_local, sources.get(field))
    assets = {asset["id"]: asset for asset in data["assets"]}
    expected = {}
    for segment in data["segments"]:
        sid, refs = segment["id"], segment["references"]
        if sid in expected:
            raise ValueError("重复段号：" + sid)
        if [r["upload_order"] for r in refs] != list(range(1, len(refs) + 1)):
            raise ValueError(sid + "：图片上传顺序与清单不一致")
        images = []
        for ref in refs:
            asset = assets[ref["asset_id"]]
            if asset["type"] == "image":
                raw = read_asset(asset) if asset.get("fixed_role") else read_evidence(read_local, asset["path"])
                images.append(dict(id=asset["id"], label=ref["label"], sha256=sha256_bytes(raw)))
        expected[sid] = dict(txt_sha256=sha256_bytes(read_evidence(read_local, segment["prompt"])), images=images)
    return expected


def pending_record(data, read_local, read_asset):
    lines = ["审核对照材料：" + json.dumps(data.get("prompt_review"), ensure_ascii=False)]
    for sid, value in inputs(data, read_local, read_asset).items():
        lines.extend([f"覆盖段：{sid}", "TXT哈希：" + value["txt_sha256"],
                      "输入图片：" + json.dumps(value["images"], ensure_ascii=False),
                      "冷读观察：", "对照结论：", "结论：待审", "问题处理：", ""])
    return "\n".join(lines)


def verify(data, read_local, read_asset, *, delivery_waivers=None):
    expected = inputs(data, read_local, read_asset)
    reviewed = set()
    waivers = delivery_waivers or {}
    used_waivers = []
    for row in records(read_evidence(read_local, "review.md").decode("utf-8")):
        if "scope" not in row:
            continue
        sid = row["scope"]
        if sid not in expected:
            raise ValueError("审核记录包含未知段：" + sid)
        if sid in reviewed:
            raise ValueError("审核记录段号重复或结论冲突：" + sid)
        try:
            fields = ("cold", "comparison", "verdict", "issues", "txt_sha256", "images")
            if sid in waivers:
                waiver = waivers[sid]
                issues = waiver["issues"]
                if (waiver["verdict"] not in ("退回","待判断") or row.get("verdict") != waiver["verdict"]
                        or not issues or any(not i.get("id") or not i.get("reason", "").strip() for i in issues)
                        or row.get("issues") != "已接受：" + json.dumps(issues, ensure_ascii=False)):
                    raise ValueError("工作台用户放行记录与原结论或处置依据不符")
                # The pinned format has no user-waiver verdict. Check its remaining
                # mandatory fields while retaining the distinct disposition below.
                for name in fields:
                    if not isinstance(row.get(name), str) or not row[name].strip():
                        raise ValueError("审核记录缺少字段：" + name)
                used_waivers.append(sid)
            else:
                require_record(row, fields)
            actual = dict(txt_sha256=row["txt_sha256"], images=json.loads(row["images"]))
            if actual != expected[sid]:
                raise ValueError("交付TXT或实际输入图片与送审文件不一致，须重审该段")
        except (ValueError, TypeError) as exc:
            raise ValueError(sid + "：" + str(exc)) from exc
        reviewed.add(sid)
    missing = expected.keys() - reviewed
    if missing:
        raise ValueError("缺少提示词独审段：" + ",".join(sorted(missing)))
    if waivers.keys() - reviewed:
        raise ValueError("用户放行记录包含本次交付以外的段")
    result = dict(status="passed", segments=list(expected), record="review.md")
    if used_waivers:
        result.update(status="user_waived", waived=used_waivers)
    return result


def verify_package(root, manifest, files):
    def read(name):
        path = (root / name).resolve()
        if name not in files or root.resolve() not in path.parents:
            raise ValueError("交付清单外的文件")
        return path.read_bytes()

    result = verify(manifest, read, lambda asset: read(asset["path"]),
                    delivery_waivers=manifest.get("workbench", {}).get("promptReviewWaivers"))
    meta = manifest.get("workbench", {}).get("page", {})
    if meta.get("version", 1) >= 2:
        roster = meta.get("roster", {})
        for role, details in roster.items():
            if details.get("assetVersionId") and sha256_bytes(read(details["path"])) != details["sha256"]:
                raise ValueError(f"{role} 的独立身份原图指纹不符")
        plans = json.loads(read(manifest["prompt_review"]["plan"]))
        packets = {p["segment"]["id"]:p["shotReferences"] for item in plans if item["role"]=="plan_segment"
                   for p in [json.loads(item["text"])] if "shotReferences" in p}
        if packets or "shotReferencePackets" in manifest["workbench"]:
            if set(packets) != {s["id"] for s in manifest["segments"]} or packets.keys() != manifest["workbench"]["shotReferencePackets"].keys():
                raise ValueError("逐镜引用索引与交付段不一致")
            if shotrefs.evidence_page(manifest,packets).encode() != read("逐镜参考.html"):
                raise ValueError("逐镜参考页与第二遍送审记录不一致")
        for source in plans:
            if source["role"] != "plan_segment":
                continue
            plan = json.loads(source["text"])
            sid = plan["segment"]["id"]
            shotrefs.verify_export(read,manifest,sid,plan.get("shotReferences"))
            if "requiredImages" in manifest["workbench"]:
                rules = [{k:v for k,v in rule.items() if k not in ("usedHere","requiredHere")}
                         for rule in manifest["workbench"]["requiredImages"]
                         if sid in rule["usedHere"] or sid in rule["requiredHere"]]
                if rules != plan.get("requiredImages", []):
                    raise ValueError(f"{sid} 必用图要求与第二遍实际送审记录不同")
            expected = plan.get("castBindings", {})
            rows = [r for r in meta["bindings"] if r["segment"] == sid]
            if len(rows) != len(expected) or {r["role"] for r in rows} != expected.keys():
                raise ValueError(f"{sid} 出镜角色与送审登记不同")
            for row in rows:
                role = row["role"]
                cell = expected[role]
                if (row.get("id") != cell["assetVersionId"] or row.get("depictsMoment") != cell.get("depictsMoment")
                        or any(row[k] != cell[k] for k in ("cells","status","note"))):
                    raise ValueError(f"{sid}/{role} 角色绑定与送审登记不同")
                frozen = plan.get("roleBaselines", {}).get(role, {})
                current = roster.get(role, {}) if cell["status"] != "absent" else {}
                if any(frozen.get(k) != current.get(k) for k in ("assetVersionId","sha256","appearance")):
                    raise ValueError(f"{sid}/{role} 身份原图或外观描述与第二遍送审依据不同")
    return result


def delivery_records(workbench, status, segments, put):
    """Use the two frozen runs that passed for each segment, including an explicit user waiver."""
    from .db import connect
    from .errors import Rejected
    from .storage import canonical

    waivers = {}
    groups = {"requirements": {}, "story": {}, "plan": {}}
    paths = dict(requirements="审核对照/制作要求.json", story="审核对照/故事.json", plan="审核对照/分段方案.json")
    lines = ["# 提示词审核依据", "", "每段结论包含两遍审查及用户对具体问题的处置。原始记录保留每遍决定、审核方、运行号和输入指纹。", "", "对照来源 " + " · ".join(
        f"[{Path(path).stem}]({path})" for path in paths.values()), ""]
    with connect(workbench.db_path) as db:
        for segment in segments:
            sid = segment["id"]
            state = status.get(sid)
            if not state or not state["ok"]:
                raise Rejected(f"{sid} 的提示词独审需要重新完成")
            inputs = []
            observations = {}
            notes = []
            accepted = []
            for stage in ("coldRead", "comparison"):
                result = state[stage]
                run = workbench.run_manifest(result["runId"])
                if not run["matches"]:
                    raise Rejected(f"{sid} 审查运行的输入指纹不符")
                manifest = run["manifest"]
                entries = manifest["inputs"]
                version = db.execute("SELECT content FROM version WHERE id=?", (result["versionId"],)).fetchone()
                reviewed = json.loads(version[0])["segments"][sid]
                refs = [ref for ref in reviewed["refs"] if ref["kind"] == "image"]
                binaries = [e for e in entries if e.get("binary") and e["role"] != "cast_baseline_image"]
                if len(refs) != len(binaries) or any(Path(e["name"]).stem != r["label"] for e,r in zip(binaries, refs)):
                    raise Rejected(f"{sid} 审查运行的图片数量或标签与被审版本不符")
                images = [dict(id=r["assetVersionId"], label=r["label"], sha256=e["sha256"]) for e,r in zip(binaries, refs)]
                if stage == "comparison":
                    plan_entry, = [e for e in entries if e["role"] == "plan_segment"]
                    plan_data = json.loads(workbench.store.read_blob(plan_entry["sha256"]))
                    uploaded = {i["sha256"] for i in images}
                    expected = [r["sha256"] for r in plan_data.get("roleBaselines", {}).values() if r["sha256"] not in uploaded]
                    if expected != [e["sha256"] for e in entries if e["role"] == "cast_baseline_image"]:
                        raise Rejected(f"{sid} 角色对照原图与第二遍审查不一致")
                txt, = [e["sha256"] for e in entries if e["role"] == "segment_txt"]
                inputs.append((txt, images))
                raw = db.execute("SELECT observations FROM review WHERE id=?", (result["reviewId"],)).fetchone()
                observations[stage] = " / ".join(" ".join(o["evidence"].splitlines()) for o in json.loads(raw[0]))
                pass_name = "冷读" if stage == "coldRead" else "对照"
                decision = {"pass":"通过","return":"退回","uncertain":"待判断"}[result["decision"]]
                notes.append(f"{pass_name} {decision} · {result['reviewer']} · {result['runId']} · {run['fingerprint']}")
                accepted.extend(dict(id=i["id"], review=result["reviewId"], issue=i["reason"], reason=i["waiver_reason"]) for i in result["issues"] if i["waiver_reason"])
                if stage == "comparison":
                    for e in entries:
                        if e.get("binary") or e["role"] in ("method", "project_method", "segment_txt",
                                                            "reference_order", "cold_read_observations"):
                            continue
                        group = "story" if e["role"] == "adopted_story" else "plan" if e["role"] in ("plan_segment","neighbour_txt") else "requirements"
                        key = (e["role"], e["name"], e["sha256"])
                        if key not in groups[group]:
                            groups[group][key] = dict(role=e["role"], name=e["name"], sha256=e["sha256"],
                                                      text=workbench.store.read_blob(e["sha256"]).decode(), segments=[])
                        groups[group][key]["segments"].append(sid)
            if inputs[0] != inputs[1]:
                raise Rejected(f"{sid} 两遍独审的 TXT 或参考图不同")
            txt, images = inputs[0]
            verdict = "通过"
            if accepted:
                verdict = "退回" if any(state[k]["decision"] == "return" for k in ("coldRead","comparison")) else "待判断"
                waivers[sid] = dict(verdict=verdict, issues=accepted)
                lines.extend(["工作台处置 用户放行", ""])
            disposition = "已接受：" + json.dumps(accepted, ensure_ascii=False) if accepted else "无"
            lines.extend([f"覆盖段：{sid}", f"TXT哈希：{txt}", "输入图片：" + json.dumps(images, ensure_ascii=False),
                          "冷读观察：" + observations["coldRead"], "对照结论：" + observations["comparison"],
                          "结论：" + verdict, "问题处理：" + disposition, "原始记录 " + " / ".join(notes), ""])
    for group, values in groups.items():
        put(paths[group], canonical(list(values.values())))
    return paths, lines, waivers
