"""Declared reference subjects, story moments and scene anchors in an immutable receipt.
Ported from reference_depictions.py at ab0fa75e3b2254a9341a8501a48be7577ab023eb.
"""
from hashlib import sha256
import re


def neutral_identity(data, asset):
    roles = asset.get("depicts_roles", [])
    cast = data.get("cast", {})
    return (asset.get("type") == "image" and asset.get("category") == "character"
            and asset.get("depicts_moment", "missing") is None and len(roles) == 1
            and cast.get(roles[0], {}).get("identity_required") is True)


def recorded_file(archive, record):
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise ValueError("缺path")
    digest = record.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("缺有效sha256")
    if sha256(archive.read(record["path"])).hexdigest() != digest:
        raise ValueError(f'文件与记录指纹不符：{record["path"]}')
    return digest


def check_depictions(archive, data):
    assets = data.get('assets', [])
    segments = data.get('segments', [])
    if type(data.get('depicts_version')) is not int or data['depicts_version'] != 1:
        raise ValueError('须声明 depicts_version=1')
    if data.get('schema_version') != 2:
        raise ValueError('须声明 schema_version=2')
    order = {s['id']: i for i, s in enumerate(segments)}
    images = {a['id']: a for a in assets if a['type'] == 'image' and not a.get('fixed_role')}
    errors = []
    valid = reviewed_moments(images, order, errors, data)
    check_anchors(archive, data, segments, images, valid, errors)
    for segment in segments:
        check_bindings(segment, images, valid, order, errors)
    if errors:
        raise ValueError('参考图状态检查未通过：\n' + '\n'.join(errors))


def reviewed_moments(images, order, errors, data=None):
    valid = {}
    for aid, a in images.items():
        roles, moment = a.get('depicts_roles'), a.get('depicts_moment')
        if (not isinstance(roles, list) or not all(isinstance(r, str) and r.strip() for r in roles)
                or len(set(roles)) != len(roles)):
            errors.append(f'{aid}：缺少或无效的 depicts_roles')
            continue
        if 'depicts_moment' not in a:
            errors.append(f'{aid}：缺 depicts_moment')
        elif moment is None:
            if (roles or a['category'] in ('grid', 'state')) and not neutral_identity(data or {}, a):
                errors.append(f'{aid}：有角色、状态或九宫格须登记具体段号和时刻')
            else:
                valid[aid] = None
        elif (not isinstance(moment, dict) or not isinstance(moment.get('segment'), str)
              or moment['segment'] not in order or moment.get('at') not in ('start','mid','end')):
            errors.append(f'{aid}：depicts_moment 必须引用有效段号及 start/mid/end')
        else:
            valid[aid] = moment

    return valid


def check_anchors(archive, data, segments, images, valid, errors):
    # Some neutral anchor files are drawing inputs only. Enroll those in the
    # asset table too, so anchor metadata is tied to the very file reviewed.
    aliases = archive.metadata["aliases"]
    by_path = {aliases.get(a['path'], a['path']): a for a in images.values()}
    anchors = data.get('scene_anchors', {})
    if not isinstance(anchors, dict):
        raise ValueError('scene_anchors 须为对象')
    for name, anchor in anchors.items():
        try:
            sha = recorded_file(archive, anchor)
            a = by_path.get(archive.local(anchor['path']))
            if not a or a['sha256'] != sha:
                raise ValueError('实际路径和 SHA-256 未共同匹配到已登记的非身份图片')
            if a['id'] not in valid or a['depicts_roles'] or valid[a['id']] is not None:
                raise ValueError(f'{a["id"]} 含角色或剧情时刻，须使用无角色、无剧情状态的基准图')
        except (ValueError, OSError, TypeError) as exc:
            errors.append(f'锁定图 {name}：{exc}')

    used = {ref['asset_id'] for s in segments for ref in s['references']}
    for a in images.values():
        scene_ids = a.get('scene_ids', [])
        if not isinstance(scene_ids, list) or not all(isinstance(name, str) and name.strip() for name in scene_ids):
            errors.append(f'{a["id"]}：scene_ids 须为场景 ID 数组')
            continue
        if a['id'] in used and a['category'] in ('scene', 'state', 'grid') and not scene_ids:
            errors.append(f'{a["id"]}：缺实际采用场景的 scene_ids 及锁定图')
        for name in scene_ids:
            if name not in anchors:
                errors.append(f'{a["id"]}：场景 {name} 未登记锁定图')


def check_bindings(s, images, valid, order, errors):
    sid, strategy = s['id'], s['reference_strategy']
    refs = [r['asset_id'] for r in s['references']]
    for aid in refs:
        if aid not in valid:
            continue
        moment = valid[aid]
        is_grid = images[aid]['category'] == 'grid'
        if is_grid and strategy != 'grid':
            errors.append(f'{sid} / {aid}：九宫格不能作为 flex 图片')
        if not moment:
            continue
        source, at = moment['segment'], moment['at']
        if source == sid and at == 'start':
            continue
        if is_grid or images[aid]['depicts_roles'] or order[source] >= order[sid]:
            errors.append(f'{sid} / {aid}：描绘 {source}.{at}，与本段开场不兼容')
    if 'opening_relation' not in s:
        errors.append(f'{sid}：缺 opening_relation；无关键站位写 null')
        return
    relation = s['opening_relation']
    if relation is None:
        return
    if not isinstance(relation, dict) or not isinstance(relation.get('description'), str) or not relation['description'].strip():
        errors.append(f'{sid}：关键开场站位须说明实际空间关系')
        return
    aid = relation.get('asset_id')
    if (not isinstance(aid, str) or aid not in refs or valid.get(aid) != {'segment':sid,'at':'start'}
            or (images[aid]['category'] == 'grid') != (strategy == 'grid')):
        errors.append(f'{sid}：{relation["description"]}；缺实际绑定的本段开场状态图或本段九宫格首格')
