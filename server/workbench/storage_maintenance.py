"""Deterministic retention. Preview by default; never infer eligibility with a model."""
import json
import re
from pathlib import Path
from .db import connect, transaction, dumps
from .storage import file_sha256, canonical, write_atomic
from .errors import Rejected
from .completion import current

DAY = 86400
RETENTION = 10 * DAY
HEX = re.compile(r'^[0-9a-f]{64}$')


def setting(db, key, default=None):
    row = db.execute('SELECT value FROM app_setting WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, value):
    db.execute('INSERT INTO app_setting VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dumps(value)))


def initialize(wb):
    with transaction(wb.db_path) as db:
        if not setting(db, 'storage:baselineReady',False):
            db.executemany('INSERT OR IGNORE INTO storage_baseline VALUES(?)',
                           ((p.relative_to(wb.store.root).as_posix(),) for p in wb.store.root.rglob('*') if p.is_file()))
            put(db,'storage:baselineReady',True)
        if setting(db, 'storage:policy') is None:
            put(db, 'storage:policy', dict(enabled=False, managedSince=wb.clock(), days=10, maxBackups=2))


def strings(value):
    if isinstance(value, str):
        yield value
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return
        if isinstance(decoded, (dict, list)):
            yield from strings(decoded)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from strings(item)


def file_candidate(wb, db, out, path, kind, owner, eligible_at, created_at, now, digest=None):
    if eligible_at is None or now <= eligible_at:
        return
    path = Path(path)
    # No symlink traversal, including parent directories.
    if '..' in path.parts or not path.is_relative_to(wb.store.root):
        return
    if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(wb.store.root)):
        return
    if not path.is_file():
        return
    rel = path.relative_to(wb.store.root).as_posix()
    actual = file_sha256(path)
    if digest and digest != actual:
        return
    policy = setting(db, 'storage:policy', {})
    stat = path.stat()
    out[rel] = dict(path=rel, kind=kind, owner=owner, sha256=actual, bytes=stat.st_size,
                    estimatedBytes=stat.st_blocks * 512 if stat.st_nlink == 1 else 0,
                    eligibleAt=eligible_at, historical=created_at < policy.get('managedSince', now) or bool(db.execute('SELECT 1 FROM storage_baseline WHERE relpath=?',(rel,)).fetchone()))


def plan(wb, db, now):
    """All paths come from records or a recorded manifest, never a recursive root purge."""
    out = {}
    policy = setting(db, 'storage:policy', {})
    active_episodes = {r[0] for r in db.execute("SELECT DISTINCT t.episode_id FROM run r JOIN task t ON t.id=r.task_id WHERE r.status IN ('queued','running','waiting_quota')")}
    active_episodes.update(r[0] for r in db.execute("SELECT episode_id FROM continuous_run WHERE state IN ('running','paused','waiting_external')"))
    active_episodes.update(r[0] for r in db.execute("SELECT DISTINCT b.episode_id FROM image_request r JOIN image_batch b ON b.id=r.batch_id WHERE r.state IN ('pending','submitted','unknown')"))
    done = {}
    for row in db.execute('SELECT episode_id FROM episode_completion WHERE completed_at IS NOT NULL'):
        status = current(wb, db, row[0])
        if status['completed'] and row[0] not in active_episodes:
            done[row[0]] = status['completedAt']

    protected = set()
    # Current adopted documents and all assets in unfinished episodes remain live.
    for row in db.execute('SELECT v.content FROM artifact a JOIN version v ON v.id=a.adopted_version_id'):
        protected.update(strings(row[0]))
    assets = [dict(r) for r in db.execute('SELECT v.*,a.episode_id,a.origin FROM asset_version v JOIN asset a ON a.id=v.asset_id')]
    for a in assets:
        if a['episode_id'] not in done or a['origin'] != 'generated':
            protected.update((a['id'], a['sha256']))
    # These records represent current uses (historical provenance alone is not a live use).
    queries = [
        'SELECT adopted_version_id FROM asset_need WHERE removed=0',
        "SELECT asset_version_id FROM image_choice WHERE mark IN ('required','adopted')",
        "SELECT asset_version_id FROM library_entry WHERE status NOT IN ('retired','rejected')",
        'SELECT * FROM episode_role_baseline', 'SELECT * FROM cast_cell', 'SELECT * FROM episode_product',
        'SELECT * FROM fixed_role_asset', 'SELECT * FROM episode_resource_version',
        'SELECT * FROM historical_archive', 'SELECT * FROM import_record', 'SELECT * FROM project_series_file',
        'SELECT * FROM stage_project_reference', 'SELECT * FROM baseline_revision',
        'SELECT * FROM historical_episode', 'SELECT * FROM episode_source', 'SELECT * FROM ref_source',
        'SELECT * FROM ref_question', 'SELECT * FROM version_baseline_source', 'SELECT * FROM episode_baseline_change',
    ]
    for query in queries:
        for row in db.execute(query):
            protected.update(strings(tuple(row)))

    runs = [dict(r) for r in db.execute('SELECT r.*,t.episode_id,t.state AS task_state FROM run r JOIN task t ON t.id=r.task_id')]
    for run in runs:
        terminal = run['status'] == 'succeeded' or (run['status'] in ('failed','stopped') and run['task_state'] in ('closed','abandoned'))
        due = (run['ended_at'] or now) + RETENTION
        can_clean = terminal and run['episode_id'] not in active_episodes and now > due
        manifest = wb.store.root / run['manifest_relpath']
        try:
            entries = json.loads(manifest.read_text())['inputs']
        except (OSError, ValueError, KeyError):
            continue
        if not can_clean:
            protected.update(e['sha256'] for e in entries)
            continue
        # Only redundant images with a verified central original; retain input/result records.
        hashes = {e['sha256'] for e in entries if e.get('binary')}
        directory = wb.store.run_dir(run['id'])
        for path in directory.glob('**/work/*'):
            if path.is_file() and path.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                digest = file_sha256(path)
                source = wb.store.blob_path(digest)
                if digest in hashes and source.is_file() and file_sha256(source) == digest:
                    file_candidate(wb, db, out, path, 'task_image', run['id'], due, run['created_at'], now, digest)

    # Chat attempts use the same frozen inputs but have a separate lifecycle.
    for chat in db.execute('SELECT f.*,s.attempt_id AS stored_attempt,s.created_at,s.ended_at FROM free_turn f LEFT JOIN storage_chat s ON s.turn_id=f.id'):
        try:
            entries = json.loads((wb.store.root/chat['manifest_relpath']).read_text())['inputs']
        except (OSError,ValueError,KeyError):
            continue
        due = chat['ended_at'] + RETENTION if chat['ended_at'] else None
        if chat['status'] != 'succeeded' or due is None or now <= due:
            protected.update(e['sha256'] for e in entries)
            continue
        hashes = {e['sha256'] for e in entries if e.get('binary')}
        for path in (wb.store.run_dir(chat['stored_attempt'])/'work').glob('*'):
            if path.is_file() and path.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                digest = file_sha256(path)
                source = wb.store.blob_path(digest)
                if digest in hashes and source.is_file() and file_sha256(source)==digest:
                    file_candidate(wb,db,out,path,'task_image',chat['stored_attempt'],due,chat['created_at'],now,digest)

    # Retain one final full delivery and its latest successful package per episode.
    keep_deliveries = set()
    replacement_times = {}
    for eid in done:
        row = db.execute("SELECT id,created_at FROM delivery WHERE episode_id=? AND scope='full' AND state='deliverable' ORDER BY created_at DESC,rowid DESC LIMIT 1", (eid,)).fetchone()
        if row:
            keep_deliveries.add(row[0])
            replacement_times[eid]=row[1]
    deliveries = [dict(r) for r in db.execute('SELECT * FROM delivery')]
    for d in deliveries:
        root = wb.store.root / d['relpath']
        try:
            manifest = json.loads((root/'manifest.json').read_text())
            if __import__('hashlib').sha256(canonical(manifest)).hexdigest() != d['manifest_hash']:
                continue
        except (OSError, ValueError):
            continue
        if d['episode_id'] not in done or d['id'] in keep_deliveries:
            protected.update(manifest['files'].values())
            continue
        due = max(done[d['episode_id']], replacement_times.get(d['episode_id'],d['created_at']), d['created_at']) + RETENTION
        for rel, digest in manifest['files'].items():
            file_candidate(wb, db, out, root/rel, 'delivery', d['id'], due, d['created_at'], now, digest)
        # Keep the tiny manifest as historical evidence; view uses removal records.

    packages = [dict(r) for r in db.execute('SELECT p.*,d.episode_id FROM package p JOIN delivery d ON d.id=p.delivery_id ORDER BY p.created_at DESC,p.rowid DESC')]
    retained_packages = set()
    package_replacements = {}
    for p in packages:
        if p['episode_id'] not in done:
            protected.update(strings(p['project_json']))
            continue
        if p['delivery_id'] in keep_deliveries and p['episode_id'] not in retained_packages:
            retained_packages.add(p['episode_id'])
            package_replacements[p['episode_id']]=p['created_at']
            protected.update(strings(p['project_json']))
            continue
        file_candidate(wb, db, out, wb.store.root/p['relpath'], 'package', p['id'],
                       max(done[p['episode_id']], p['created_at'],package_replacements.get(p['episode_id'],replacement_times.get(p['episode_id'],0)))+RETENTION, p['created_at'], now, p['sha256'])

    # Content hashes shared by several asset records are retained whenever one use remains.
    protected.update(a['sha256'] for a in assets if a['id'] in protected)
    for a in assets:
        if a['episode_id'] in done and a['origin']=='generated' and a['id'] not in protected and a['sha256'] not in protected:
            file_candidate(wb, db, out, wb.store.blob_path(a['sha256']), 'unused_image', a['id'],
                           max(done[a['episode_id']], a['created_at'])+RETENTION, min(x['created_at'] for x in assets if x['sha256']==a['sha256']), now, a['sha256'])

    for r in db.execute("SELECT r.*,b.provider,b.episode_id,b.created_at FROM image_request r JOIN image_batch b ON b.id=r.batch_id WHERE r.state='succeeded' AND b.provider='openai-image-api'"):
        if r['episode_id'] in active_episodes: continue
        source = wb.store.blob_path(r['sha256'])
        if not source.is_file() or file_sha256(source) != r['sha256']: continue
        path = wb.store.root/'image-api'/r['local_request_id']/'result.png'
        file_candidate(wb, db, out, path, 'image_output', r['local_request_id'],
                       (r['ended_at'] or now)+RETENTION, r['created_at'], now, r['sha256'])
        # Never remove the fallback original in the same sweep as the output copy.
        if path.relative_to(wb.store.root).as_posix() in out:
            out.pop(source.relative_to(wb.store.root).as_posix(), None)

    backups = [dict(r) for r in db.execute('SELECT b.*,s.completed_at FROM backup b JOIN storage_backup s ON s.backup_id=b.id ORDER BY s.completed_at DESC,b.rowid DESC')]
    if len(backups)>1 and not verified_backup(wb,backups[0]):
        backups=[]
    for i, b in enumerate(backups):
        if i == 0 or (i == 1 and now <= b['completed_at']+RETENTION): continue
        root = wb.store.root/b['relpath']
        try:
            manifest = json.loads((root/'manifest.json').read_text())
            if __import__('hashlib').sha256(canonical(manifest)).hexdigest() != b['manifest_hash']: continue
        except (OSError, ValueError): continue
        due = b['completed_at']+RETENTION if i == 1 else backups[0]['completed_at']-0.001
        for rel, digest in manifest['files'].items():
            file_candidate(wb, db, out, root/'files'/rel, 'backup', b['id'], due, b['created_at'], now, digest)
        file_candidate(wb, db, out, root/'workbench.sqlite', 'backup', b['id'], due, b['created_at'], now, manifest['db'])
    return list(out.values())


def summary(candidates):
    groups = {}
    for c in candidates:
        group = groups.setdefault(c['kind'], dict(files=0, bytes=0))
        group['files'] += 1
        group['bytes'] += c['bytes']
    return dict(files=len(candidates), bytes=sum(c['bytes'] for c in candidates),
                estimatedBytes=sum(c['estimatedBytes'] for c in candidates), groups=groups,
                historicalFiles=sum(c['historical'] for c in candidates))


def scan(wb):
    with connect(wb.db_path) as db:
        db.execute('BEGIN')
        candidates = plan(wb, db, wb.clock())
    return dict(**summary(candidates), candidates=candidates)


def removed(db, relpath):
    r = db.execute('SELECT removed_at FROM storage_removed WHERE relpath=?', (relpath,)).fetchone()
    return r[0] if r and r[0] > 0 else None


def _maintain(wb, *, force=False):
    """Serial write transaction prevents new adoptions while eligibility is rechecked.

    Existing files are always preview-only. Enabling execution is a separate policy
    change; this release ships with execution disabled, preserving current data.
    """
    now = wb.clock()
    journal = wb.store.root / 'storage-cleanup-journal.json'
    with transaction(wb.db_path) as db:
        if journal.is_file():
            for c in json.loads(journal.read_text()):
                path = wb.store.root/c['path']
                if not path.exists() and not path.is_symlink() and '..' not in Path(c['path']).parts and path.is_relative_to(wb.store.root):
                    db.execute('INSERT OR IGNORE INTO storage_removed VALUES(?,?,?,?,?,?)',
                               (c['path'],c['kind'],c['owner'],c['sha256'],c['bytes'],c['attemptedAt']))

        previous = setting(db, 'storage:last', {})
        if not force and now-previous.get('at', 0) < DAY:
            return previous
        policy = setting(db, 'storage:policy', {})
        candidates = plan(wb, db, now)
        cleaned = 0
        failures = []
        if policy.get('enabled'):
            write_atomic(journal, canonical([dict(c,attemptedAt=now) for c in candidates if not c['historical']]))
            for c in candidates:
                if c['historical']: continue
                path = wb.store.root/c['path']
                try:
                    if file_sha256(path) != c['sha256']: continue
                    if c['kind']=='image_output':
                        write_atomic(path.parent/'stored-result.json', canonical(dict(sha256=c['sha256'])))
                    path.unlink()
                    db.execute('INSERT OR REPLACE INTO storage_removed VALUES(?,?,?,?,?,?)',
                               (c['path'],c['kind'],c['owner'],c['sha256'],c['bytes'],now))
                    cleaned += 1
                except OSError as exc:
                    failures.append(dict(path=c['path'],error=str(exc)))
        result = dict(at=now, mode='automatic' if policy.get('enabled') else 'preview',
                      **summary(candidates), cleaned=cleaned, failures=failures)
        put(db, 'storage:last', result)
    if policy.get('enabled'):
        write_atomic(journal, canonical([]))
    return result


def status(wb, db):
    checks = [x for x in (setting(db,'storage:preview'),setting(db,'storage:last')) if x]
    return dict(policy=setting(db,'storage:policy',{}), last=max(checks,key=lambda x:x['at']) if checks else None)


def maintain(wb, *, force=False):
    import fcntl
    # The advisory lock spans the database commit and crash-journal reset.
    with (wb.store.root/'storage-maintenance.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with connect(wb.db_path) as db:
                return setting(db, 'storage:last', {})
        with connect(wb.db_path) as db:
            policy = setting(db,'storage:policy',{})
            previous = setting(db,'storage:last',{})
        if not force and wb.clock()-previous.get('at',0) < DAY:
            return previous
        if not policy.get('enabled') and not (wb.store.root/'storage-cleanup-journal.json').is_file():
            report = scan(wb)
            result = {k:v for k,v in report.items() if k != 'candidates'}
            result.update(at=wb.clock(),mode='preview',cleaned=0,failures=[])
            with transaction(wb.db_path) as db:
                put(db,'storage:last',result)
            return result
        return _maintain(wb,force=force)


def verified_backup(wb, backup):
    root = wb.store.root/backup['relpath']
    try:
        manifest=json.loads((root/'manifest.json').read_text())
        from .storage import sha256_bytes
        if sha256_bytes(canonical(manifest)) != backup['manifest_hash']:
            return False
        if file_sha256(root/'workbench.sqlite') != manifest['db']:
            return False
        for rel,digest in manifest['files'].items():
            path=root/'files'/rel
            if '..' in Path(rel).parts or Path(rel).is_absolute() or path.is_symlink() or file_sha256(path)!=digest:
                return False
        return True
    except (OSError,ValueError,KeyError):
        return False
