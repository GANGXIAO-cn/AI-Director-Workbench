"""Track publication completion separately from episodes still producing video and music."""
from contextlib import closing
import sqlite3
import json
from .db import dumps
from .workflow import ORDER, OPTIONAL


def basis(db, eid):
    return {r['stage']:r['adopted_version_id'] for r in db.execute(
        'SELECT stage,adopted_version_id FROM artifact WHERE episode_id=? AND adopted_version_id IS NOT NULL', (eid,))}


def pending_completion_impacts(wb, db, eid):
    """Use the same live scopes as delivery_problems; old advisory notes are not gates."""
    from .timeline import layout
    plan = wb._adopted_plan(db,eid) or {}
    segments = [s['id'] for s in layout(plan)['segments']]
    targets = {'prompts:'+sid for sid in segments} | {'joint:'+a+'-'+b for a,b in zip(segments,segments[1:])}
    return any(r['kind'] in ('workflow_change','decision_change','supplement_change','palette_change') or r['target_ref'] in targets
               for r in db.execute("SELECT kind,target_ref FROM impact WHERE episode_id=? AND state='pending'",(eid,)))


def current_deliveries(db, eid, versions):
    rows = db.execute("SELECT id,versions FROM delivery WHERE episode_id=? AND scope='full' AND state='deliverable' "
                      "AND NOT EXISTS (SELECT 1 FROM storage_removed WHERE kind='delivery' AND owner=delivery.id AND removed_at>0) "
                      "ORDER BY created_at DESC,rowid DESC", (eid,))
    return [row["id"] for row in rows if all(versions.get(k)==v for k,v in json.loads(row["versions"]).items())]


def film_progress(wb, db, eid, deliveries):
    from .filmedit import progress
    start = db.execute("SELECT delivery FROM film_start WHERE episode_id=?", (eid,)).fetchone()
    did = start[0] if start else next(iter(deliveries), None)
    if did and did in deliveries:
        ready, finished = progress(wb.store.root, db, did)
        if ready:
            return True, True, finished
    if start:
        return True, False, None
    # Historical film runs may predate automatic kickoff.
    path = wb.store.root/"video-production/queue.sqlite"
    if path.is_file():
        episode_ids = [r[0] for r in db.execute("SELECT id FROM delivery WHERE episode_id=?", (eid,))]
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as queue:
            if queue.execute("SELECT 1 FROM sqlite_master WHERE name='soundtrack'").fetchone():
                if any(queue.execute("SELECT 1 FROM soundtrack WHERE delivery=?", (d,)).fetchone() for d in episode_ids):
                    return True, False, None
    return False, False, None


def current(wb, db, eid, film=None):
    saved = db.execute('SELECT * FROM episode_completion WHERE episode_id=?', (eid,)).fetchone()
    if not saved or not saved['completed_at']:
        return dict(completed=False, completedAt=None)
    valid = json.loads(saved['basis']) == basis(db, eid)
    valid = valid and not pending_completion_impacts(wb,db,eid)
    completed_at = saved["completed_at"]
    if valid:
        if film is None:
            film = film_progress(wb, db, eid, current_deliveries(db, eid, json.loads(saved["basis"])))
        producing, _, finished = film
        if producing:
            valid = bool(finished)
            completed_at = finished
    if valid:
        frozen = db.execute('SELECT basis FROM version_publish_basis WHERE version_id=?', (saved['publish_version_id'],)).fetchone()
        valid = bool(frozen) and json.loads(frozen[0])['segments'] == wb._publish_basis(db, eid)['segments']
    return dict(completed=bool(valid), completedAt=completed_at if valid else None,
                publishVersionId=saved['publish_version_id'])


def adopted(wb, db, art, vid, completed_at=None):
    eid = art['episode_id']
    if art['stage'] != 'publish':
        db.execute('UPDATE episode_completion SET completed_at=NULL WHERE episode_id=?', (eid,))
        db.execute("UPDATE episode SET state='in_production' WHERE id=? AND state='delivered'", (eid,))
        return
    versions = basis(db, eid)
    locked = wb._kickoff(db, eid).get('sourceIntent') == 'locked_screenplay'
    required = [s for s in ORDER if s not in OPTIONAL and (not locked or s not in ('story','characters','treatment'))]
    complete = all(versions.get(s) for s in required)
    frozen = db.execute('SELECT basis FROM version_publish_basis WHERE version_id=?',(vid,)).fetchone()
    complete = complete and bool(frozen) and json.loads(frozen[0])['segments'] == wb._publish_basis(db,eid)['segments']
    complete = complete and not pending_completion_impacts(wb,db,eid)
    at = (wb.clock() if completed_at is None else completed_at) if complete else None
    db.execute('INSERT INTO episode_completion VALUES(?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET '
               'publish_version_id=excluded.publish_version_id,basis=excluded.basis,completed_at=excluded.completed_at',
               (eid, vid, dumps(versions), at))
    if at is not None:
        db.execute("UPDATE episode SET state='delivered',revision=revision+1 WHERE id=?", (eid,))
        db.execute("UPDATE topic SET status='已制作' WHERE episode_id=?", (eid,))


def backfill(wb, db):
    """Use existing adoption evidence; never equate list archival with completion."""
    rows = list(db.execute("SELECT a.episode_id,a.adopted_version_id,max(d.created_at) AS adopted_at FROM artifact a "
                           "JOIN adoption d ON d.artifact_id=a.id AND d.version_id=a.adopted_version_id "
                           "WHERE a.stage='publish' AND NOT EXISTS (SELECT 1 FROM episode_completion c WHERE c.episode_id=a.episode_id AND c.completed_at IS NOT NULL) "
                           "GROUP BY a.episode_id,a.adopted_version_id"))
    for r in rows:
        later = db.execute("SELECT 1 FROM adoption d JOIN artifact a ON a.id=d.artifact_id WHERE a.episode_id=? "
                           "AND d.created_at>? LIMIT 1", (r['episode_id'],r['adopted_at'])).fetchone()
        if later: continue
        if db.execute('SELECT 1 FROM episode_completion WHERE episode_id=?',(r['episode_id'],)).fetchone():
            versions=basis(db,r['episode_id'])
            delivered=any(all(versions.get(k)==v for k,v in json.loads(d[0]).items())
                          for d in db.execute("SELECT versions FROM delivery WHERE episode_id=? AND scope='full' AND state='deliverable'",(r['episode_id'],)))
            if not delivered: continue
        old_state=db.execute('SELECT state FROM episode WHERE id=?',(r['episode_id'],)).fetchone()[0]
        adopted(wb,db,dict(episode_id=r['episode_id'],stage='publish'),r['adopted_version_id'],r['adopted_at'])
        if not current(wb,db,r['episode_id'])['completed']:
            db.execute('UPDATE episode_completion SET completed_at=NULL WHERE episode_id=?',(r['episode_id'],))
            db.execute('UPDATE episode SET state=? WHERE id=?',(old_state,r['episode_id']))
