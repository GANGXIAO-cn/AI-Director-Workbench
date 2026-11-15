"""Choose adopted artwork for the shots that will actually use it."""
from collections import defaultdict

from . import imagechoices
from .db import connect
from .worksheet import CATEGORIES


def fallback_shots(candidates, shots):
    """Prefer literal IDs. Accept separators and type prefixes only for unique aliases."""
    def alias(key):
        parts=key.casefold().replace('-', '_').split('_')
        return '_'.join(parts[1:] if parts[0] in ('prop','state','scene','costume') else parts)

    by_key={c['key']:c for c in candidates}
    normalized=defaultdict(list)
    aliases=defaultdict(list)
    for key in by_key:
        normalized[key.casefold().replace('-', '_')].append(key)
        aliases[alias(key)].append(key)
    matched=defaultdict(list)
    for shot in shots:
        for prop in shot.get('propStates') or {}:
            full=prop.casefold().replace('-', '_')
            keys=[prop] if prop in by_key else normalized.get(full,[])
            if not keys and alias(prop)==full:
                keys=aliases.get(full,[])
            if len(keys)==1 and shot['id'] in by_key[keys[0]]['shots'] and shot['id'] not in matched[keys[0]]:
                matched[keys[0]].append(shot['id'])
    return [dict(key=key,shots=matched[key]) for key in by_key if key in matched]


def owned(rule):
    return (rule.get('selectedBy') == 'agent'
            and set(rule.get('autoBindings', {})) == {'worksheet'}
            and not any(rule.get('bindingBase', {}).values()))


def select(wb, cid, eid, plan_id, plan, matrix):
    from .continuous_judgments import compact_shots, fingerprint

    shots = compact_shots(plan)
    needs = [n for n in wb.asset_worksheet(eid)['rows']
             if n.get('adopted_version_id') and n['decision'] != 'text_only'
             and CATEGORIES.get(n['kind']) in ('scene', 'prop', 'state', 'costume')]
    with connect(wb.db_path) as db:
        rules = {r['versionId']: r for r in imagechoices.requirements(db, eid)}
    old = {vid: rule for vid, rule in rules.items() if 'worksheet' in rule.get('autoBindings', {})}
    fixed = [r for r in rules.values() if not owned(r)]
    segments = []
    by_key = {n['need_key']: n for n in needs}
    cast = {r['segment']: r['cells'] for r in matrix['rows']}
    cursor = wb._continuous_record(cid)['cursor']
    drawings = cursor.get('productDrawings', {})
    for seg in plan['segments']:
        sid = seg['id']
        group = [s for s in shots if s['segment'] == sid]
        shot_ids = {s['id'] for s in group}
        if seg['strategy'] == 'grid':
            # A resumed grid already occupies the cast cells. Count the original
            # artwork that goes into the drawing, not the resulting board.
            artwork = cursor.get('gridArtwork', {}).get(sid, {})
            reserved = {d['master'] for d in drawings.get(sid, [])}
            for role in {role for shot in group for role in shot.get('visibleRoles', [])}:
                vid = cast.get(sid, {}).get(role, {}).get('assetVersionId')
                if vid and vid == artwork.get('versionId'):
                    vid = artwork.get('roleInputs', {}).get(role) or matrix['roster'].get(role, {}).get('assetVersionId')
                if vid:
                    reserved.add(vid)
        else:
            reserved = {c['assetVersionId'] for c in cast.get(sid, {}).values()
                        if c.get('status') == 'ok' and c.get('assetVersionId')}
            reserved.update(s[k] for s in plan['shots'] if s['segment'] == sid
                            for k in ('startAssetId', 'endAssetId') if s.get(k))
            for rule in fixed:
                sources = rule.get('autoBindings', {})
                scopes = ([rule.get('bindingBase', {})] + [v for k, v in sources.items() if k != 'worksheet']
                          if 'worksheet' in sources else [rule])
                if any(sid in scope.get('targetSegments', []) or shot_ids.intersection(scope.get('shotIds', []))
                       for scope in scopes):
                    reserved.add(rule['versionId'])
        candidates = []
        for n in needs:
            vid = n['adopted_version_id']
            rule = rules.get(vid)
            shareable = (not rule or rule.get('selectedBy') == 'agent'
                         and not any(rule.get('bindingBase', {}).values())
                         and set(rule.get('autoBindings', {})) <= {'scene', 'worksheet'}
                         and bool(rule.get('autoBindings')))
            if vid in reserved or (seg['strategy'] != 'grid' and not shareable):
                continue
            eligible = [s['id'] for s in group
                        if (not n['scene_ids'] or s.get('origin', {}).get('scene') in n['scene_ids'])
                        and (not n.get('role_id') or 'visibleRoles' not in s or n['role_id'] in s['visibleRoles'])]
            if eligible:
                candidates.append(dict(key=n['need_key'], title=n['title'], detail=n['description'],
                                       kind=n['kind'], shots=eligible))
        limit = (16 if seg['strategy'] == 'grid' else 9) - len(reserved)
        if candidates and limit > 0:
            segments.append(dict(segment=sid, strategy=seg['strategy'], shots=group, candidates=candidates, limit=limit))

    def validate(answer):
        rows = answer.get('segments') if isinstance(answer, dict) else None
        if not isinstance(rows, dict) or set(rows) != {s['segment'] for s in segments}:
            return ['按给出的段号逐段返回选择，可为空数组']
        for seg in segments:
            picks = rows[seg['segment']]
            allowed = {c['key']: c['shots'] for c in seg['candidates']}
            if not isinstance(picks, list) or len(picks) > seg['limit']:
                return ['选图数量超过本段可用名额']
            seen = set()
            for pick in picks:
                if not isinstance(pick, dict):
                    return ['逐项填写素材编号与镜号']
                key, ids = pick.get('key'), pick.get('shots')
                if not isinstance(key, str) or key in seen or key not in allowed:
                    return ['只选择本段的不重复候选编号']
                if not isinstance(ids, list) or not ids or any(i not in allowed[key] for i in ids):
                    return ['选中的图须对应实际使用的候选镜头']
                seen.add(key)
        return []

    def fallback():
        # Exact prop identifiers remain usable even if the semantic selection fails.
        result = {}
        for seg in segments:
            picks = fallback_shots(seg['candidates'], seg['shots'])
            result[seg['segment']] = picks[:seg['limit']]
        return dict(segments=result)

    flex = defaultdict(list)
    need_keys = {}
    grid = defaultdict(list)
    if segments:
        instruction = ('按定稿分镜选本段实际需要的参考图。优先维持主场景、角色装束、关键道具及其正确状态。'
                       '同一物件的不同状态只选对应镜头。通用道具可不选；不必凑数。flex 用于视频，grid 用于画九宫格。'
                       '只读条目与分镜，无须评图。reply {"segments":{"段号":[{"key":"素材编号","shots":["镜号"]}]}}。')
        answer, basis = wb._continuous_bounded_json(cid, '采用素材逐段选图', plan_id, instruction,
                                                     dict(segments=segments), validate, fallback=fallback)
        for seg in segments:
            for pick in answer['segments'][seg['segment']]:
                key = pick['key']
                if seg['strategy'] == 'grid':
                    grid[seg['segment']].append(by_key[key]['adopted_version_id'])
                else:
                    vid = by_key[key]['adopted_version_id']
                    need_keys[vid] = key
                    flex[vid].extend(pick['shots'])
        for vid, ids in flex.items():
            key = need_keys[vid]
            wb._continuous_bind(cid, vid, list(dict.fromkeys(ids)), plan_id, 'worksheet', basis,
                                '按分镜选用：' + by_key[key]['title'], rules.get(vid), need_key=key)
    for vid, rule in old.items():
        if vid not in flex:
            wb.bind_required_image(eid, vid, [], plan_id, 'worksheet', fingerprint(rule),
                                   '当前分镜已不使用该素材', rule)
    return grid
