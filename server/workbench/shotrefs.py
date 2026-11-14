"""Per-shot roles, prop states and reference observations from short-video at ab0fa75e3b2254a9341a8501a48be7577ab023eb.

All external text comes from the caller's project-scoped receipt or delivery file list.
"""
from dataclasses import dataclass, field
from html import escape
from pathlib import PurePosixPath
from urllib.parse import quote
import re
import json

from . import rhythm

MARKER = 'shot_reference_version'
ROLES = '实际可见角色ID'
PROPS = '关键道具状态'


def enabled(data):
    if MARKER in data:
        return True
    segments = data.get('segments')
    if not isinstance(segments, list):
        return False
    return any(isinstance(ref, dict) and 'visual_review' in ref
               for segment in segments if isinstance(segment, dict) and isinstance(segment.get('references'), list)
               for ref in segment['references'])


@dataclass
class Shot:
    roles: set = field(default_factory=set)
    props: dict = field(default_factory=dict)


def prop_states(cell):
    if cell == '无':
        return {}
    result = {}
    for pair in re.split(r'[;；]', cell):
        key, separator, value = pair.strip().partition('=')
        if (not separator or not re.fullmatch(r'[a-z][a-z0-9-]*', key)
                or not value.strip() or key in result):
            raise ValueError('关键道具状态须为 道具ID=状态，以分号分隔，无则填无')
        result[key] = value.strip()
    return result


def identity_shots(board, roles):
    return [sid for sid, shot in board.items() if shot.roles & roles]


def read_board(read_text, data):
    cfg = data.get('shot_rhythm', {})
    if cfg.get('version') != 2:
        raise ValueError('逐镜引用需要 shot_rhythm v2 表演与镜头联合表')
    plan = read_text(cfg.get('plan', '场面与制作方案.md'))
    board = {}
    for row in rhythm.table(plan, (*rhythm.FLAT_COLUMNS, ROLES, PROPS), required=True):
        raw = row[ROLES].strip()
        ids = [] if raw == '无' else re.split(r'[、,，\s]+', raw)
        if len(set(ids)) != len(ids) or any(role not in data['cast'] for role in ids):
            raise ValueError(f"{row['段ID']}/{row['镜头S']}：未知、重复或空的出镜角色")
        if row[PROPS] in ('', '—', '-', '待填'):
            raise ValueError(f"{row['段ID']}/{row['镜头S']}：须记录关键道具状态，无则填无")
        # A continuous shot can span several rows. The upload scope uses its shot ID once.
        shots = board.setdefault(row['段ID'], {})
        shot = shots.setdefault(row['镜头S'], Shot())
        shot.roles.update(ids)
        for key, value in prop_states(row[PROPS]).items():
            shot.props.setdefault(key, set()).add(value)
    if set(board) != {s['id'] for s in data['segments']}:
        raise ValueError('逐镜引用表的段号与清单不一致')
    return board


def image_review(read_text, asset, ref, tag):
    review = ref.get('visual_review')
    if not isinstance(review, dict) or review.get('sha256') != asset.get('sha256'):
        raise ValueError(tag + '：缺当前图片版本的实际看图记录')
    contents = review.get('observed')
    if not isinstance(contents, str) or not contents.strip():
        raise ValueError(tag + '：缺显眼人物、物体及状态的看图观察')
    accepted={item['issue_key'] for item in asset.get('accepted_image_issues',[])}
    provisional={item['issue_key'] for item in asset.get('provisional_image_issues',[])}
    conflicts=review.get('conflicts')
    if not isinstance(conflicts,list) or any(not isinstance(c,dict) or not (c.get('acceptedIssue') in accepted or c.get('provisionalIssue') in provisional) for c in conflicts):
        detail='；'.join(str(c.get('text','未说明冲突')) if isinstance(c,dict) else str(c) for c in conflicts) if isinstance(conflicts,list) else '缺少冲突核对记录'
        raise ValueError(tag + '：图片仍有冲突或未完成核对：'+detail+'；请先核对已采用用途及本段正文，优先修正引用用途或具体差异')
    evidence = read_text(review.get('evidence_path'))
    if contents not in evidence:
        raise ValueError(tag + '：观察与本集看图记录不符')
    return review


def validate_scope(segment, ref, asset, board, canonical_roles, review):
    sid = segment['id']
    aid = asset['id']
    tag = f'{sid}/{aid}'
    declared = ref.get('shots')
    if (not isinstance(declared, list) or not declared
            or any(not isinstance(s, str) or s not in board for s in declared)
            or len(set(declared)) != len(declared)):
        raise ValueError(tag + '：作用镜头为空、重复或不属于本段')
    bound = {r for r, source in segment['identity_bindings'].items() if source == aid}
    canonical = canonical_roles.get(aid) or asset.get('fixed_role')
    depicted = asset.get('depicts_roles', [])
    if not canonical and asset['category'] == 'character' and len(depicted) == 1:
        canonical = depicted[0]
    roles = bound | ({canonical} if canonical else set())
    expected = set(identity_shots(board, roles))
    actual = set(declared)
    if expected - actual:
        raise ValueError(f'{tag}：身份图漏作用镜头 {sorted(expected - actual)}')
    if asset['type'] == 'image' and asset.get('depicts_moment') is None:
        props = review.get('prominent_props')
        if not isinstance(props, dict):
            raise ValueError(tag + '：须登记中性原图中显眼的道具状态，纯身份图可填空对象')
        for key, value in props.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(tag + '：显眼道具缺状态')
            used = set().union(*(board[shot].props.get(key, set()) for shot in declared))
            # The complete board is uploaded once, including all of its contents.
            # A neutral reference must not impose a later state anywhere in this segment.
            segment_states = set().union(*(shot.props.get(key, set()) for shot in board.values()))
            if not used or (value != '仅外观' and segment_states != {value}):
                raise ValueError(f'{tag}：原图道具 {key}={value} 与本段分镜用途或状态冲突')
    if canonical:
        # A combined board can also supply a vehicle or prop. Require a distinct
        # documented purpose for every additional shot, rather than widening identity scope.
        extra = review.get('other_uses', {})
        if not isinstance(extra, dict) or set(extra) != actual - expected:
            raise ValueError(f'{tag}：身份图作用镜头与出镜不符，身份仅适用于 {sorted(expected)}')
        if extra and (review.get('combined') is not True or any(not isinstance(v, str)
                       or not v.strip() for v in extra.values())):
            raise ValueError(tag + '：额外作用须为已看图的组合图逐镜写明道具或空间用途')
        for shot, prop in extra.items():
            if prop not in review.get('prominent_props', {}) or prop not in board[shot].props:
                raise ValueError(tag + '：附加道具须同时登记于图内和对应镜头')
    elif 'other_uses' in review:
        raise ValueError(tag + '：other_uses 仅用于身份基准的独立附加用途')


def check_shot_references(read_text, data):
    if type(data.get(MARKER)) is not int or data[MARKER] != 1:
        raise ValueError('须声明 shot_reference_version=1')
    if data.get('character_contract_version') != 1:
        raise ValueError('逐镜引用须启用本集角色契约')
    board = read_board(read_text, data)
    assets = {a['id']: a for a in data['assets']}
    canonical = {r['identity_asset_id']: rid for rid, r in data['cast'].items() if r['identity_required']}
    for s in data['segments']:
        shots = board[s['id']]
        if set().union(*(shot.roles for shot in shots.values())) != set(s['on_screen_roles']):
            raise ValueError(s['id'] + '：逐镜实际可见角色的合集与本段出镜登记不符')
        for ref in s['references']:
            asset = assets[ref['asset_id']]
            tag = s['id'] + '/' + asset['id']
            review = image_review(read_text, asset, ref, tag) if asset['type'] == 'image' else {}
            validate_scope(s, ref, asset, shots, canonical, review)
    return {'status': 'passed', 'shots': sum(map(len, board.values())),
            'scope': '核对分镜登记、引用范围及看图记录；画面内容由实际看图确认'}


def check_binding_structure(read_text, data):
    """Verify inherited references mechanically, without approving image contents."""
    board = read_board(read_text, data)
    assets = {a['id']: a for a in data['assets']}
    for segment in data['segments']:
        sid = segment['id']; shots = board[sid]
        if set().union(*(shot.roles for shot in shots.values())) != set(segment['on_screen_roles']):
            raise ValueError(sid+'：逐镜角色与本段出镜登记不一致')
        seen = set()
        for ref in segment['references']:
            aid = ref['asset_id']; scope = ref.get('shots')
            if aid not in assets or aid in seen:
                raise ValueError(sid+'：引用素材不存在或重复')
            seen.add(aid)
            if not isinstance(scope,list) or not scope or any(not isinstance(k,str) or k not in shots for k in scope) or len(scope)!=len(set(scope)):
                raise ValueError(sid+'：作用镜头为空、重复或不属于本段')
            roles = {role for role,source in segment['identity_bindings'].items() if source==aid}
            if set(identity_shots(shots,roles))-set(scope):
                raise ValueError(sid+'：身份引用遗漏已登记的出镜镜头')
    return dict(status='passed',scope='仅校验继承的角色、素材和镜头绑定结构；不重新评估图片',shots=sum(map(len,board.values())))


def suggestions(read_text, data):
    board = read_board(read_text, data)
    return {s['id']: {rid: identity_shots(board[s['id']], {rid})
                      for rid in s['on_screen_roles'] if data['cast'][rid]['identity_required']}
            for s in data['segments']}


def plan_enabled(plan):
    if any(isinstance(s,dict) and ("visibleRoles" in s or "propStates" in s) for s in (plan or {}).get("shots", [])):
        return True
    text = (plan or {}).get("rhythmTable")
    if not isinstance(text, str):
        return False
    for line in text.splitlines():
        cells = {v.strip() for v in line.strip().strip("|").split("|")}
        if {"段ID","镜头S"} <= cells and cells.intersection((ROLES,PROPS)):
            return True
    return False


def roles_cell(value):
    return [] if value.strip() == "无" else re.split(r"[、,，\s]+", value.strip())


def apply_table_edit(plan):
    """Update model-authored window fields when the editor changes the authoritative table."""
    shots = plan.get("shots")
    if not isinstance(shots, list) or not all(isinstance(shot, dict) for shot in shots):
        return plan
    if not any("visibleRoles" in shot or "propStates" in shot for shot in shots):
        return plan
    try:
        rows = rhythm.table(plan["rhythmTable"], (*rhythm.FLAT_COLUMNS, ROLES, PROPS), required=True)
        rhythm.check_alignment(rhythm.plan_rows(plan), rows)
        from . import timeline
        keys = [key for segment in timeline.layout(plan)["segments"] for key in segment["shots"]]
        scoped = {key: dict(visibleRoles=roles_cell(row[ROLES]), propStates=prop_states(row[PROPS]))
                  for key, row in zip(keys, rows, strict=True)}
    except (ValueError, KeyError, TypeError, AttributeError):
        # Keep invalid drafts editable. Adoption still validates the submitted table.
        return plan
    return dict(plan, shots=[dict(shot, **scoped[shot["id"]]) for shot in plan["shots"]])


def board_rows(plan):
    rows = rhythm.plan_table(plan)
    if not rows or any(ROLES not in row or PROPS not in row for row in rows):
        raise ValueError("逐镜引用须同时填写实际可见角色ID和关键道具状态")
    expected = rhythm.plan_rows(plan)
    rhythm.check_alignment(expected,rows)
    previous = {}
    used = {}
    for window, row in zip(expected, rows):
        sid, ident = row["段ID"], row["镜头S"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*",ident):
            raise ValueError("逐镜引用表中的镜号不合法：" + ident)
        if row["接续方式"] == "同镜延续":
            if previous.get(sid) != ident:
                raise ValueError("同镜延续须沿用上一镜号：" + ident)
        elif ident in used.get(sid, set()):
            raise ValueError("起镜或切镜须使用新镜号：" + ident)
        previous[sid] = ident
        used.setdefault(sid, set()).add(ident)
        if not row[ROLES].strip() or row[ROLES].strip() in ("待填", "—", "-"):
            raise ValueError(row["镜头S"]+" 须明确出镜角色，无则填无")
        props = prop_states(row[PROPS])
        if ROLES in window and (set(roles_cell(window[ROLES])) != set(roles_cell(row[ROLES]))
                                or prop_states(window[PROPS]) != props):
            raise ValueError(sid+"/"+ident+" 的出镜角色或道具状态与结构化镜头记录不一致")
    return rows


def segment_packet(wb, db, eid, plan, matrix, sid, entry, *, draft=False):
    """Package adopted bindings; historical observations remain optional evidence."""
    from . import timeline
    rows = [r for r in board_rows(plan) if r["段ID"] == sid]
    segment = next((s for s in timeline.layout(plan)["segments"] if s["id"] == sid), None)
    if not segment or not rows:
        raise ValueError("逐镜引用缺少本段联合表：" + sid)
    mapping = dict(zip(segment["shots"], (r["镜头S"] for r in rows), strict=True))
    cells = next(r["cells"] for r in matrix["rows"] if r["segment"] == sid)
    mentioned = {r for row in rows for r in re.split(r"[、,，\s]+",row[ROLES])}
    ref_ids = {ref["assetVersionId"] for ref in entry["refs"]}
    roster = {name:dict(identity_required=bool(info.get("assetVersionId")),identity_asset_id=info.get("assetVersionId"))
              for name,info in matrix["roster"].items() if name in mentioned or cells.get(name,{}).get("status")=="ok"
              or info.get("assetVersionId") in ref_ids}
    assets, refs = [], []
    files = {"联合表.md":rhythm.markdown(rows)}
    for i, ref in enumerate(entry["refs"], 1):
        vid = ref["assetVersionId"]
        asset = wb.asset_version(vid, db)
        item = dict(id=vid, name=asset["name"], type=asset["kind"], category=asset["category"], sha256=asset["sha256"])
        item["accepted_image_issues"]=[dict(r) for r in db.execute("SELECT * FROM accepted_image_issue WHERE asset_version_id=?",(vid,))]
        from .progress_expert import provisional_image_issues
        item["provisional_image_issues"]=provisional_image_issues(db,eid,vid)
        if asset["category"] == "identity":
            item["category"] = "character"
            item["depicts_roles"] = asset.get("roles") or [r for r,c in cells.items() if c.get("assetVersionId")==vid and c["status"]=="ok"]
        if any(c.get("depictsMoment") and c["assetVersionId"] == vid for c in cells.values()):
            item["depicts_moment"] = {"segment":sid, "at":"start"}
        assets.append(item)
        windows = ref.get("shots", [])
        if not isinstance(windows,list) or any(not isinstance(s,str) or s not in mapping for s in windows) or len(windows)!=len(set(windows)):
            if not draft:
                raise ValueError(sid+"：作用镜头重复或不属于本段")
            windows = []
        actual = list(dict.fromkeys(mapping[w] for w in windows))
        binding = dict(asset_id=vid, shots=actual, label=ref["label"], upload_order=i)
        if ref.get("visualReviewId"):
            row = db.execute("SELECT details FROM shot_image_observation WHERE id=? AND episode_id=? AND segment=? AND asset_version_id=?",
                             (ref["visualReviewId"],eid,sid,vid)).fetchone()
            if row is None:
                # Missing legacy evidence is not a binding failure. Never fabricate it.
                refs.append(binding)
                continue
            review = json.loads(row["details"])
            name = f"参考{i}看图记录.txt"
            files[name] = review["observed"] + "\n"
            binding["visual_review"] = dict(review, evidence_path=name)
        refs.append(binding)
    seg = dict(id=sid, references=refs, identity_bindings={r:c["assetVersionId"] for r,c in cells.items()
               if c["status"]=="ok" and c.get("assetVersionId")}, on_screen_roles=[r for r,c in cells.items() if c["status"]=="ok"])
    data = dict(shot_reference_version=1,character_contract_version=1,shot_rhythm=dict(version=2,plan="联合表.md"),
                cast=roster,segments=[seg],assets=assets)
    return dict(manifest=data, files=files, windowShots=mapping)


def evidence_page(episode, packets):
    assets = {a["id"]:a for a in episode["assets"]}
    index = episode["workbench"]["shotReferencePackets"]
    page = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>逐镜引用与看图记录</title><style>body{max-width:850px;margin:auto;padding:24px;font:16px/1.6 system-ui;color:#20252c}'
            'img{max-width:100%;max-height:300px}table{border-collapse:collapse}th,td{border:1px solid #bbb;padding:4px 9px}'
            'figure{margin:20px 0}p{white-space:pre-wrap}</style><main><h1>逐镜引用与看图记录</h1>']
    for segment in episode["segments"]:
        sid = segment["id"]
        packet = packets[sid]
        data = packet["manifest"]
        board = read_board(packet["files"].__getitem__,data)[sid]
        page.append("<section><h2>"+escape(sid)+'</h2><table><thead><tr><th>实际镜头</th><th>可见角色</th><th>关键道具状态</th></tr></thead><tbody>')
        for ident, shot in board.items():
            props = "；".join(k+"="+"、".join(sorted(v)) for k,v in shot.props.items())
            page.append("<tr>"+"".join("<td>"+escape(v)+"</td>" for v in (ident,"、".join(sorted(shot.roles)) or "无",props or "无"))+"</tr>")
        page.append("</tbody></table>")
        for ref in data["segments"][0]["references"]:
            asset = assets[ref["asset_id"]]
            page.append("<figure><h3>"+escape(ref["label"]+" · "+asset["name"])+"</h3>")
            if asset["type"] == "image":
                url = escape(quote(asset["path"]))
                review = ref.get("visual_review", {})
                page.append(f'<a href="{url}"><img src="{url}" alt="{escape(asset["name"])}"></a>')
                page.append("<p>看图观察 · "+escape(review.get("observed", "未登记历史观察；沿用已采用的素材绑定"))+"</p>")
                for field, label in (("prominent_props","图内显眼道具"),("other_uses","非出镜镜头的附加用途")):
                    if review.get(field):
                        page.append("<p>"+label+" · "+escape("；".join(k+"="+v for k,v in sorted(review[field].items())))+"</p>")
            page.append("<figcaption>作用镜头："+escape("、".join(ref["shots"]))+"</figcaption></figure>")
        page.append('<a href="'+escape(quote(index[sid]))+'">查看本段核对清单</a></section>')
    return "\n".join([*page,'<a href="00_图文对照.html">返回图文对照</a></main></html>'])


def export_packets(root, manifest, put, *, packets=None):
    if packets is None:  # historical packages retain their original review evidence
        plans = json.loads((root / manifest["prompt_review"]["plan"]).read_bytes())
        packets = {p["segment"]["id"]:p["shotReferences"] for entry in plans if entry["role"]=="plan_segment"
                   for p in [json.loads(entry["text"])] if "shotReferences" in p}
    index = {}
    for ordinal, segment in enumerate(manifest["segments"], 1):
        sid = segment["id"]
        if sid not in packets:
            raise ValueError(sid+" 缺少当前分镜的逐镜引用记录")
        packet = packets[sid]
        source = packet["manifest"]
        check_binding_structure(packet["files"].__getitem__, source)
        folder = f"逐镜引用/{ordinal:03d}"
        for name,text in packet["files"].items():
            put(folder+"/"+name,text.encode())
        path = folder+"/逐镜引用.json"
        put(path,json.dumps(source,ensure_ascii=False,indent=2).encode())
        index[sid] = path
        for ref, reviewed in zip(segment["references"],source["segments"][0]["references"],strict=True):
            if ref.get("shots"):
                ref["windows"] = list(ref["shots"])
            ref["shots"] = reviewed["shots"]
    manifest["workbench"]["shotReferencePackets"] = index
    put("逐镜引用索引.json",json.dumps(index,ensure_ascii=False,indent=2).encode())
    put("逐镜参考.html",evidence_page(manifest,packets).encode())


def verify_export(read, episode, sid, packet):
    index = episode["workbench"].get("shotReferencePackets", {})
    if not packet and sid not in index:
        return
    if not packet or sid not in index or json.loads(read("逐镜引用索引.json")) != index:
        raise ValueError(sid+" 逐镜引用索引与送审段不一致")
    path = PurePosixPath(index[sid])
    data = json.loads(read(str(path)))
    if data != packet["manifest"]:
        raise ValueError(sid+" 逐镜引用清单与实际送审记录不一致")
    def text(name):
        if name not in packet["files"]:
            raise ValueError("逐镜引用依据不在送审记录中")
        raw = read(str(path.parent/name)).decode()
        if raw != packet["files"][name]:
            raise ValueError(sid+" 联合表或看图观察与实际送审记录不一致")
        return raw
    check_binding_structure(text,data)
    assets = {a["id"]:a for a in episode["assets"]}
    refs = next(s["references"] for s in episode["segments"] if s["id"]==sid)
    audited = data["segments"][0]["references"]
    if len(refs)!=len(audited) or any(r.get(k)!=a.get(k) for r,a in zip(refs,audited)
                                     for k in ("asset_id","shots","upload_order","label")):
        raise ValueError(sid+" 交付的图片顺序或作用镜头与逐镜核对不符")
    if any(a["sha256"] != assets[a["id"]]["sha256"] for a in data["assets"]):
        raise ValueError(sid+" 交付素材与逐镜看图记录的指纹不符")
