"""Small offline fixtures; cleanup execution is simulated without deleting files."""
import json
from pathlib import Path
import pytest
from helpers import Env, png_bytes
from workbench.db import transaction, connect
from workbench.storage import Store, file_sha256
from workbench.storage_maintenance import scan, maintain, RETENTION, put
from workbench.apiimages import OpenAIImageAPIProvider


def complete(tmp_path):
    env = Env(tmp_path)
    case = env.full_episode(segments={'G01':[4000,4000],'G02':[8000]}, deliver=False)
    return env, case


def test_copy_is_independent_and_repeat_reuses_file(tmp_path):
    store = Store(tmp_path)
    digest = store.put_blob(png_bytes(8,8))
    target = tmp_path/'work'/'image.png'
    store.materialize_blob(digest,target)
    ino = target.stat().st_ino
    store.materialize_blob(digest,target)
    assert target.stat().st_ino == ino
    assert ino != store.blob_path(digest).stat().st_ino
    target.write_bytes(b'edited work image')
    assert file_sha256(store.blob_path(digest)) == digest


def test_publish_adoption_is_endpoint_and_reading_does_not_reset(tmp_path):
    env, case = complete(tmp_path); wb=env.wb; eid=case['eid']
    status = wb.episode_view(eid)['completion']
    assert status['completed']
    wb.clock=lambda:status['completedAt']+500
    assert wb.episode_view(eid)['completion'] == status
    with env.db() as db:
        did=db.execute("SELECT id FROM delivery WHERE episode_id=? ORDER BY rowid DESC",(eid,)).fetchone()[0]
    d=wb.delivery_view(did)
    assert d['versions']['publish']==status['publishVersionId']
    assert wb.verify_delivery(did)['ok']
    env.save_adopt(eid,'plan',dict(case['plan'],notes='updated'),'updated-plan')
    assert not wb.episode_view(eid)['completion']['completed']


def test_missing_publish_is_not_full_completion(tmp_path):
    env, case = complete(tmp_path)
    with env.db() as db:
        db.execute("UPDATE artifact SET adopted_version_id=NULL WHERE episode_id=? AND stage='publish'",(case['eid'],)); db.commit()
    problems,_=env.wb.delivery_problems(case['eid'])
    assert any(p['stage']=='publish' for p in problems)
    assert not env.wb.episode_view(case['eid'])['completion']['completed']


def test_unused_generated_image_ten_days_shared_and_preview(tmp_path):
    env, case = complete(tmp_path); wb=env.wb
    image=wb.register_asset(case['pid'],'未采用图',png_bytes(8,8,(100,0,0)),'scene',episode_id=case['eid'],origin='generated')
    with env.db() as db:
        at=db.execute('SELECT created_at FROM asset_version WHERE id=?',(image['versionId'],)).fetchone()[0]
    wb.clock=lambda:at+RETENTION
    assert not any(c['owner']==image['versionId'] for c in scan(wb)['candidates'])
    wb.clock=lambda:at+RETENTION+1
    assert any(c['owner']==image['versionId'] for c in scan(wb)['candidates'])
    result=maintain(wb,force=True)
    assert result['cleaned']==0 and result['mode']=='preview'
    assert wb.store.blob_path(image['sha256']).is_file()
    # The same bytes used by another unfinished episode protect the shared original.
    eid2=wb.create_episode(case['pid'],'E02')['episodeId']
    wb.register_asset(case['pid'],'另一集采用',png_bytes(8,8,(100,0,0)),'scene',episode_id=eid2)
    assert not any(c['sha256']==image['sha256'] and c['kind']=='unused_image' for c in scan(wb)['candidates'])


def test_historical_files_not_executed_even_when_enabled(tmp_path,monkeypatch):
    env,case=complete(tmp_path);wb=env.wb
    image=wb.register_asset(case['pid'],'旧图',png_bytes(8,8,(90,0,0)),'scene',episode_id=case['eid'],origin='generated')
    future=wb.clock()+RETENTION+5
    wb.clock=lambda:future
    with transaction(wb.db_path) as db:
        put(db,'storage:policy',dict(enabled=True,managedSince=future,days=10,maxBackups=2))
    calls=[]
    monkeypatch.setattr(Path,'unlink',lambda path,*a,**k:calls.append(path))
    result=maintain(wb,force=True)
    assert result['historicalFiles']>0 and result['cleaned']==0
    assert not calls


def test_execution_is_journaled_and_skips_final_result(tmp_path,monkeypatch):
    env,case=complete(tmp_path);wb=env.wb
    image=wb.register_asset(case['pid'],'过程图',png_bytes(8,8,(80,0,0)),'scene',episode_id=case['eid'],origin='generated')
    now=wb.clock()+RETENTION+5;wb.clock=lambda:now
    wb.set_storage_cleanup(True)
    calls=[]
    def remove(path,*args,**kwargs):
        journal=json.loads((wb.store.root/'storage-cleanup-journal.json').read_text())
        assert any(c['path']==path.relative_to(wb.store.root).as_posix() for c in journal)
        calls.append(path)
    monkeypatch.setattr(Path,'unlink',remove)
    result=maintain(wb,force=True)
    assert result['cleaned']>0
    assert wb.store.blob_path(image['sha256']) in calls
    assert wb.store.blob_path(case['img']['sha256']) not in calls
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM storage_removed').fetchone()[0]==len(calls)


def test_image_lookup_uses_central_copy_without_resubmission(tmp_path):
    store=Store(tmp_path);digest=store.put_blob(png_bytes(8,8))
    job=tmp_path/'image-api'/'adw-test';job.mkdir(parents=True)
    (job/'receipt.json').write_text(json.dumps(dict(state='succeeded',providerRequestId='request')))
    (job/'stored-result.json').write_text(json.dumps(dict(sha256=digest)))
    provider=OpenAIImageAPIProvider(tmp_path/'image-api')
    result=provider.inspect('adw-test')
    assert result.state=='succeeded' and result.data==store.read_blob(digest)


def test_backup_limit_latest_survives_and_restore(tmp_path):
    env=Env(tmp_path);wb=env.wb
    pid,eid=env.new_episode()
    origin=wb.clock()
    ids=[]
    for i in range(3):
        wb.clock=lambda i=i:origin+i
        ids.append(wb.backup()['backupId'])
    candidates=scan(wb)['candidates']
    assert {c['owner'] for c in candidates if c['kind']=='backup'}=={ids[0]}
    wb.clock=lambda:origin+RETENTION+10
    assert {c['owner'] for c in scan(wb)['candidates'] if c['kind']=='backup'}==set(ids[:2])
    restored=wb.restore(ids[-1],tmp_path/'restore')
    with connect(Path(restored['restoredTo'])/'workbench.sqlite') as db:
        assert db.execute('SELECT id FROM episode').fetchone()[0]==eid
    assert all((wb.store.root/'backups'/i/'workbench.sqlite').is_file() for i in ids)


def test_expert_finishes_publication_without_repeating_images(tmp_path):
    from test_continuous_wake import run_record
    env,case=complete(tmp_path);wb=env.wb;eid=case['eid']
    with env.db() as db:
        db.execute("UPDATE artifact SET adopted_version_id=NULL WHERE episode_id=? AND stage='publish'",(eid,))
        db.execute('UPDATE episode_completion SET completed_at=NULL WHERE episode_id=?',(eid,));db.commit()
    cid=run_record(env,eid)
    sent=env.images.sent
    assert wb._continuous_delivery(cid)
    assert wb._continuous_record(cid)['state']=='done'
    assert wb._continuous_record(cid)['cursor']['stage']=='publish'
    assert wb.episode_view(eid)['completion']['completed']
    assert env.images.sent==sent


def test_corrupt_latest_backup_does_not_expire_previous(tmp_path):
    env=Env(tmp_path);wb=env.wb
    old=wb.backup()['backupId'];latest=wb.backup()['backupId']
    (wb.store.root/'backups'/latest/'workbench.sqlite').write_bytes(b'broken backup')
    now=wb.clock()+RETENTION+10;wb.clock=lambda:now
    assert not any(c['kind']=='backup' for c in scan(wb)['candidates'])
    assert (wb.store.root/'backups'/old/'workbench.sqlite').is_file()


def test_waiting_task_keeps_its_episode_images(tmp_path):
    from test_continuous_wake import run_record
    env,case=complete(tmp_path);wb=env.wb
    image=wb.register_asset(case['pid'],'保留图',png_bytes(8,8,(40,0,0)),'scene',episode_id=case['eid'],origin='generated')
    run_record(env,case['eid'])
    now=wb.clock()+RETENTION+10;wb.clock=lambda:now
    assert not any(c['sha256']==image['sha256'] for c in scan(wb)['candidates'])


def test_final_delivery_excludes_unadopted_gallery_images(tmp_path):
    env,case=complete(tmp_path);wb=env.wb
    unused=wb.register_asset(case['pid'],'废弃候选',png_bytes(8,8,(7,0,0)),'scene',episode_id=case['eid'],origin='generated')
    did=wb.deliver(case['eid'],'full','final-gallery')['deliveryId']
    d=wb.delivery_view(did)
    assert unused['sha256'] not in d['files'].values()
    assert case['img']['sha256'] in d['files'].values()


def test_existing_blob_reused_by_new_record_remains_historical(tmp_path):
    from workbench.storage_maintenance import initialize
    from workbench.service import Workbench
    initial=Store(tmp_path/'data')
    original=initial.put_blob(png_bytes(8,8,(6,0,0)))
    env,case=complete(tmp_path);wb=env.wb
    candidate=wb.register_asset(case['pid'],'使用既有文件',png_bytes(8,8,(6,0,0)),'scene',episode_id=case['eid'],origin='generated')
    now=wb.clock()+RETENTION+10;wb.clock=lambda:now
    found=next(c for c in scan(wb)['candidates'] if c['sha256']==original)
    assert found['historical']


def test_h3_handoff_can_precede_publish_without_completing_episode(tmp_path):
    env,case=complete(tmp_path);wb=env.wb;eid=case['eid']
    with env.db() as db:
        db.execute("UPDATE artifact SET adopted_version_id=NULL WHERE episode_id=? AND stage='publish'",(eid,))
        db.execute('UPDATE episode_completion SET completed_at=NULL WHERE episode_id=?',(eid,))
        db.execute("UPDATE episode SET state='in_production' WHERE id=?",(eid,));db.commit()
    did=wb.deliver(eid,'full','pre-publication',pre_publication=True)['deliveryId']
    assert not wb.episode_view(eid)['completion']['completed']
    assert wb.episode_view(eid)['episode']['state']=='in_production'
    assert not wb.delivery_view(did)['versions']['publish']
    package=wb.package(did,list(case['plan']['segments'][i]['id'] for i in range(2)),'pre-h3')
    assert wb.package_file(package['packageId']).is_file()


def test_delivery_indicator_uses_current_receipt_and_completion_ignores_old_advisory(tmp_path):
    from workbench.completion import backfill, pending_completion_impacts
    env,case=complete(tmp_path);wb=env.wb;eid=case['eid']
    with env.db() as db:
        original=db.execute("SELECT max(d.created_at) FROM adoption d JOIN artifact a ON a.id=d.artifact_id WHERE a.episode_id=? AND a.stage='publish'",(eid,)).fetchone()[0]
        db.execute("INSERT INTO impact VALUES('old-story',?,?,'story_change','old-story-version','分场剧本','早期起草提示','pending',0)",(case['pid'],eid))
        db.execute('UPDATE episode_completion SET completed_at=NULL WHERE episode_id=?',(eid,));db.commit()
    view=wb.episode_view(eid)
    assert view['productionStatus']['delivery'] and not view['productionStatus']['package']
    with transaction(wb.db_path) as db:
        backfill(wb,db)
    assert wb.episode_view(eid)['completion']['completedAt']==original
    with env.db() as db:
        assert db.execute("SELECT state FROM impact WHERE id='old-story'").fetchone()[0]=='pending'
        db.execute("UPDATE delivery SET state='needs_recheck' WHERE episode_id=?",(eid,));db.commit()
    assert not wb.episode_view(eid)['productionStatus']['delivery']


def test_live_publication_dependency_still_blocks_completion(tmp_path):
    from workbench.completion import backfill
    env,case=complete(tmp_path);wb=env.wb;eid=case['eid']
    with env.db() as db:
        db.execute("INSERT INTO impact VALUES('live',?,?,'workflow_change','new-script','workflow:publish','文案需要更新','pending',0)",(case['pid'],eid))
        db.execute('UPDATE episode_completion SET completed_at=NULL WHERE episode_id=?',(eid,));db.commit()
    with transaction(wb.db_path) as db:backfill(wb,db)
    assert not wb.episode_view(eid)['completion']['completed']
