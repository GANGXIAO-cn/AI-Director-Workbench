"""Continue recorded storyboard decisions across production runs, retaining image bytes."""
import json

from . import imagechoices
from .db import connect
from .storage import canonical, sha256_bytes


def restore(wb, cid, eid):
    current = wb._continuous_record(cid)
    cursor = current['cursor']
    owned = dict(cursor.get('gridArtwork', {}))
    replacements = dict(cursor.get('reviewReplacements', {}))
    with connect(wb.db_path) as db:
        rules = {r['versionId']: r for r in imagechoices.requirements(db, eid)}
        history = db.execute('SELECT id,cursor FROM continuous_run WHERE episode_id=? AND id<>? '
                             'AND created_at<=? ORDER BY created_at DESC,rowid DESC',
                             (eid, cid, current['created_at'])).fetchall()
    inherited = []
    for record in history:
        previous = json.loads(record['cursor'])
        for sid, artwork in previous.get('gridArtwork', {}).items():
            if sid in owned:
                continue
            rule = rules.get(artwork.get('versionId'), {})
            # A director may have explicitly released this board since the run.
            if 'grid:' + sid not in rule.get('autoBindings', {}):
                continue
            owned[sid] = artwork
            for old, new in previous.get('reviewReplacements', {}).items():
                replacements.setdefault(old, new)
            inherited.append(dict(segment=sid, versionId=artwork['versionId'], sourceRun=record['id']))
    if inherited:
        wb._continuous_cursor(cid, gridArtwork=owned, reviewReplacements=replacements)
        for item in inherited:
            wb._continuous_log(cid, 'plan', 'choose_image', item['versionId'],
                               '接续上一轮已选九宫格，按当前分镜核对后复用', item)
    return owned


def reconcile(wb, cid, eid, plan_id, owned):
    """Release superseded AI contributions, including those left by older runs."""
    plan = wb._continuous_version(plan_id)
    selected = {s['id']: owned[s['id']]['versionId'] for s in plan['segments']
                if s['strategy'] == 'grid' and s['id'] in owned}
    with connect(wb.db_path) as db:
        rules = imagechoices.requirements(db, eid)
    by_version = {r['versionId']: r for r in rules}
    for segment in wb.cast_matrix(eid)['rows']:
        sid = segment['segment']
        chosen = selected.get(sid)
        if not chosen:
            continue
        for role, cell in segment['cells'].items():
            previous = cell.get('assetVersionId')
            rule = by_version.get(previous, {})
            if (cell.get('status') == 'ok' and previous != chosen
                    and 'grid:' + sid in rule.get('autoBindings', {})):
                wb.set_cast(eid, sid, role, 'ok', chosen, opening_confirmed=True,
                            note='接续本段已选九宫格', command_id=f'{cid}:reconcile-grid-cast:{sid}:{role}:{previous}:{chosen}')
    for rule in rules:
        for source in rule.get('autoBindings', {}):
            if not source.startswith('grid:'):
                continue
            sid = source[5:]
            if selected.get(sid) == rule['versionId']:
                continue
            expected = wb._continuous_image_rule(eid, rule['versionId'])
            if source not in (expected or {}).get('autoBindings', {}):
                continue
            basis = sha256_bytes(canonical([plan_id, source, selected.get(sid), expected]))
            wb.bind_required_image(eid, rule['versionId'], [], plan_id, source, basis,
                                   '接续当前九宫格，撤回旧图的系统绑定', expected,
                                   command_id=f'{cid}:release-grid:{basis}')
            wb._continuous_log(cid, 'plan', 'choose_image', selected.get(sid) or rule['versionId'],
                               '已撤回旧九宫格的系统必用范围，图片保留',
                               dict(segment=sid, previousVersionId=rule['versionId'], versionId=selected.get(sid)))
