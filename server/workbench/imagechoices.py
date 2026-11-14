"""Keep user-required image bytes and their requested segment coverage in each delivery."""
import json

from .errors import Rejected


def validate_request(value, plan):
    if not isinstance(value, dict):
        raise Rejected("必用要求必须包含选择理由与目标段")
    targets = value.get("targetSegments", [])
    if not isinstance(targets, list) or any(not isinstance(s, str) for s in targets):
        raise Rejected("目标段须为段号列表")
    if set(targets) - {s["id"] for s in (plan or {}).get("segments", [])}:
        raise Rejected("目标段不存在，请核对已采用的制作方案")
    reason = value.get("reason")
    fitness = value.get("fitness", "not_tested")
    limitations = value.get("limitations", "")
    if not isinstance(reason, str) or not reason.strip():
        reason = "用户指定必用"
    if fitness not in ("not_tested", "pass", "limited", "blocked") or not isinstance(limitations, str):
        raise Rejected("用途适配或限制说明无效")
    if fitness in ("limited", "blocked") and not limitations.strip():
        raise Rejected("请说明这张必用图受限的用途或缺陷范围")
    return dict(targetSegments=list(dict.fromkeys(targets)), selectedBy="user", reason=reason.strip(),
                fitness=fitness, limitations=limitations.strip())


def requirements(db, eid):
    result = []
    for row in db.execute(
        "SELECT c.asset_version_id,c.shot_ids,a.name,v.sha256,i.details FROM image_choice c "
        "JOIN asset_version v ON v.id=c.asset_version_id JOIN asset a ON a.id=v.asset_id "
        "JOIN episode e ON e.id=c.episode_id AND e.id=a.episode_id AND e.project_id=a.project_id "
        "LEFT JOIN required_image_details i ON i.choice_id=c.id "
        "WHERE c.episode_id=? AND c.mark='required' ORDER BY c.created_at,c.id", (eid,)):
        details = json.loads(row["details"]) if row["details"] else dict(
            targetSegments=[], selectedBy="unrecorded", reason="", fitness="not_tested", limitations="")
        details.setdefault("sha256", row["sha256"])
        result.append(dict(versionId=row["asset_version_id"], name=row["name"], shotIds=json.loads(row["shot_ids"]),
                           **details))
    return result


def check_scope(rules, plan, scope, selected, used):
    selected = set(selected)
    shots = {s["id"]: s["segment"] for s in plan["shots"]}
    segments = {s["id"] for s in plan["segments"]}
    included, problems = [], []
    for rule in rules:
        targets = set(rule["targetSegments"])
        targets.update(shots[s] for s in rule["shotIds"] if s in shots)
        hits = used.get(rule["sha256"], set()) & selected
        expected = targets & selected
        if scope == "trial" and not expected and not hits:
            continue
        if scope == "full" and (targets - segments or set(rule["shotIds"]) - shots.keys()):
            problems.append(dict(stage="assets", segment=None, file=rule["name"], problem="必用图的目标段或镜号已不在已采用方案中"))
        missing = sorted(expected - hits)
        if missing or not hits:
            problems.append(dict(stage="assets", segment="、".join(missing) or None, file=rule["name"],
                                 problem="必用图未落实到要求的正式输入段：" + ("、".join(missing) or "至少一段")))
        included.append(dict(rule, requiredHere=sorted(expected), usedHere=sorted(hits)))
    return included, problems


def verify_manifest(manifest, files):
    rules = manifest["workbench"].get("requiredImages", [])
    if manifest["workbench"].get("page", {}).get("requiredImages", []) != rules:
        raise ValueError("交付页与清单登记的必用图不同")
    assets = {a["id"]:a for a in manifest["assets"] if a["type"] == "image"}
    if rules and any(a["sha256"] != files.get(a["path"]) for a in assets.values()):
        raise ValueError("必用图核对所用的素材指纹与交付原件不符")
    for rule in rules:
        hits = sorted(s["id"] for s in manifest["segments"] if any(
            assets.get(ref["asset_id"], {}).get("sha256") == rule["sha256"] for ref in s["references"]))
        if not hits or hits != rule["usedHere"] or set(rule["requiredHere"]) - set(hits):
            raise ValueError("必用图未落实到本次交付的实际输入：" + rule["name"])
