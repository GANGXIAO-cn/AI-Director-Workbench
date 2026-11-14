"""Per-segment source tracking, not an approval or a content-quality receipt."""
import json
from .storage import canonical, sha256_bytes


def hashes(plan):
    return {s['id']:sha256_bytes(canonical(dict(segment=s,shots=[w for w in plan.get('shots',[]) if w['segment']==s['id']])))
            for s in (plan or {}).get('segments',[])}


def basis(db,vid):
    row=db.execute('SELECT segments FROM version_plan_basis WHERE version_id=?',(vid,)).fetchone()
    return json.loads(row[0]) if row else {}


def record(db,vid,base_id,content,plan,scope=None,restored_from=None):
    entries=content.get('segments',{})
    if restored_from:
        values=basis(db,restored_from)
    else:
        old=db.execute('SELECT content FROM version WHERE id=?',(base_id,)).fetchone() if base_id else None
        previous=json.loads(old[0]).get('segments',{}) if old else {}
        values=basis(db,base_id) if base_id else {}
        changed=set(scope) if scope is not None else {sid for sid,entry in entries.items() if entry!=previous.get(sid)}
        current=hashes(plan)
        values.update({sid:current[sid] for sid in changed if sid in current})
    values={sid:h for sid,h in values.items() if sid in entries}
    db.execute('INSERT INTO version_plan_basis VALUES(?,?) ON CONFLICT(version_id) DO UPDATE SET segments=excluded.segments',
               (vid,json.dumps(values)))


def resolve(wb,db,eid,vid):
    version=db.execute('SELECT content,created_at FROM version WHERE id=?',(vid,)).fetchone()
    entries=json.loads(version['content']).get('segments',{})
    plan=wb._adopted_content(db,eid,'plan') or {}
    current=hashes(plan); known=basis(db,vid)
    # Existing content/reference checks already run at adoption. This only
    # settles the source changes actually represented by this adopted version.
    matched={sid for sid,h in current.items() if known.get(sid)==h and sid in entries}
    order=[s['id'] for s in sorted(plan.get('segments',[]),key=lambda s:s['order'])]
    joints={'joint:'+a+'-'+b:{a,b} for a,b in zip(order,order[1:])}
    for item in db.execute("SELECT * FROM impact WHERE episode_id=? AND state='pending' AND kind='plan_change'",(eid,)).fetchall():
        if item['created_at']>version['created_at']:continue
        target=item['target_ref']
        scope={target[8:]} if target.startswith('prompts:') else joints.get(target,set())
        if not scope:continue
        valid=set(matched)
        # Historical prompt versions have no source map. An unchanged neighbour
        # is not made stale merely because the other side was edited.
        if item['source_ref'].startswith('plan:'):
            before=db.execute('SELECT b.content FROM version v JOIN version b ON b.id=v.base_id WHERE v.id=?',(item['source_ref'][5:],)).fetchone()
            old=hashes(json.loads(before[0])) if before else {}
            valid.update(sid for sid in scope if sid in entries and sid in current and old.get(sid)==current[sid])
        if scope<=valid:
            db.execute("UPDATE impact SET state='resolved' WHERE id=?",(item['id'],))
