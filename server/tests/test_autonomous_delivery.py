import re

from test_continuous_finishing import finishing_fixture
from test_image_assess import reply


def test_product_master_binds_without_scene_observation(tmp_path,monkeypatch):
    env,case,cid,_=finishing_fixture(tmp_path,config='puggy',spec_overrides=dict(targetDuration='story_driven'));wb=env.wb;eid=case['eid']
    master=wb.use_project_product(eid,'02','cup')['versionId']
    def place(cid,kind,obj,instruction,materials,validate,**kwargs):
        answer=dict(placements=[dict(code='02',shotId='S01',intent='马克杯在窗台')])
        assert validate(answer)==[]
        return answer,'basis'
    monkeypatch.setattr(wb,'_continuous_bounded_json',place)
    wb._continuous_products(cid)
    p=wb.product_placements(eid,check_prompt_refs=False)
    assert p['verified']==1 and p['records'][0]['directReference'] and not p['records'][0]['observation']
    assert wb.product_placements(eid,candidate={'segments':{'G01':{'refs':[{'assetVersionId':master}]}}})['verified']==1
    assert wb.product_placements(eid,candidate={'segments':{'G02':{'refs':[{'assetVersionId':master}]}}})['verified']==0
    # Freeze a handoff with the independent reference, then verify actual archive bytes.
    import json,zipfile
    from hashlib import sha256
    prompt=env.prompts_for(case['plan'],case['img']['versionId'])
    segment=prompt['segments']['G01']
    segment['refs'].append(dict(assetVersionId=master,label='图片2',kind='image',role='马克杯',shots=['S01']))
    segment['txt']+=' 图片2 中的马克杯放在窗台。'
    version,_=env.save_adopt(eid,'prompts',prompt,'master-prompts')
    env.review_segments(version,['G01','G02'])
    publish=env.art(eid,'publish')['adopted_version_id']
    env.save_adopt(eid,'publish',wb._continuous_version(publish),'reviewed-publish')
    problems,_=wb.delivery_problems(eid,'full')
    assert not problems,problems
    did=wb.deliver(eid,'full','master-delivery')['deliveryId']
    assert wb.verify_delivery(did)['ok']
    pid=wb.package(did,['G01','G02'],'master-package')['packageId']
    with zipfile.ZipFile(wb.package_file(pid)) as archive:
        project=json.loads(archive.read('project.json'))
        refs={s['id']:{r['assetId'] for r in s['refs']} for s in project['segments']}
        assert master in refs['G01'] and master not in refs['G02']
        asset=next(a for a in project['assets'] if a['id']==master)
        assert sha256(archive.read(asset['path'])).hexdigest()==asset['sha256']==wb.asset_version(master)['sha256']



def test_two_grids_one_visual_call_and_resume(tmp_path,monkeypatch):
    env,case,cid,_=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid']
    plan=case['plan']
    for seg in plan['segments']:seg['strategy']='grid'
    plan_id=env.save_adopt(eid,'plan',plan,'grid-plan')
    with env.db() as db:
        baseline={r['stage']:r['adopted_version_id'] for r in db.execute('select stage,adopted_version_id from artifact where episode_id=? and adopted_version_id is not null',(eid,))}
        stamp=wb._continuous_user_stamp(db,eid,wb._continuous_record(cid)['cursor']['ownedRuns'])
    wb._continuous_cursor(cid,base=baseline,userStamp=stamp,pendingUserChange=False)
    # Produce a real 07 worksheet version in the same isolated episode.
    need=wb.asset_worksheet(eid)['rows'][0]
    assert wb._image_need_round(cid,need) is None
    rows=wb.asset_worksheet(eid)['rows'];base_image=rows[0]['adopted_version_id']
    assert base_image and not wb.image_assessments(eid)
    from threading import Barrier,Event
    from hashlib import sha256
    gate=Barrier(2);second_done=Event();finished=[];content_hash={}
    submit=env.images.submit
    def reverse(req):
        sid='G01' if 'S01' in req['prompt'] else 'G02'
        gate.wait(timeout=5)
        if sid=='G01':assert second_done.wait(5)
        result=submit(req)
        content_hash[sid]=sha256(result.data).hexdigest()
        finished.append(sid)
        if sid=='G02':second_done.set()
        return result
    monkeypatch.setattr(env.images,'submit',reverse)
    before=env.images.sent
    env.images.max_concurrency=2
    grids=wb._grid_artwork(cid,eid,baseline['plan'])
    assert set(grids)=={'G01','G02'} and grids['G01']['versionId']!=grids['G02']['versionId']
    assert finished==['G02','G01']
    assert all(wb.asset_version(g['versionId'])['sha256']==content_hash[sid] for sid,g in grids.items())
    assert wb._grid_artwork(cid,eid,baseline['plan'])==grids and env.images.sent==before+2
    monkeypatch.setattr(env.images,'submit',submit)
    def assess(req):
        ids=[re.search('候选-([a-f0-9-]+)',p.name)[1] for p in req.images if '候选-' in p.name]
        assert set(ids)=={base_image,*(g['versionId'] for g in grids.values())}
        return reply([dict(versionId=i,verdict='blocked',defect='整幅缺少轨道主体') if i==base_image else dict(versionId=i,verdict='usable') for i in ids])(req)
    env.codex.script=[assess]
    wb._episode_image_review(cid,eid,rows,grids)
    wb._episode_image_review(cid,eid,rows,grids)
    replacement=wb.asset_worksheet(eid)['rows'][0]['adopted_version_id']
    assert replacement!=base_image and wb.asset_version(replacement)['sha256']!=wb.asset_version(base_image)['sha256']
    assert wb._continuous_record(cid)['cursor']['reviewReplacements'][base_image]==replacement
    assert env.images.sent==before+3
    assert len([c for c in env.codex.calls if c.purpose=='image_assess'])==1


def test_legacy_stop_migration_preserves_receipts_and_user_pause(tmp_path):
    import json,sqlite3
    import pytest
    from workbench.db import SCHEMA,connect,_migrate_continuous_states
    with connect(tmp_path/'old.sqlite') as db:
        db.executescript("CREATE TABLE episode(id TEXT PRIMARY KEY); CREATE TABLE delivery(id TEXT,episode_id TEXT);")
        db.execute(SCHEMA.split(';',1)[0].replace("'waiting_external'","'blocked','waiting_choice','waiting_quota'"))
        db.execute("CREATE TABLE child(run_id TEXT REFERENCES continuous_run(id))")
        db.execute("INSERT INTO delivery VALUES('sent','preview')")
        rows=[
            ('direction','waiting_choice',{},dict(kind='direction',candidates=[dict(recommended=True,story='A')]),0,'running'),
            ('e10','blocked',dict(judgmentKind='商品入景选择'),{},0,'running'),
            ('preview','waiting_choice',dict(deliveryId='sent'),dict(kind='preview'),0,'done'),
            ('restart','paused',dict(resumeAfterRestart=True),{},0,'running'),
            ('user','paused',dict(resumeAfterRestart=True),{},1,'paused'),
            ('unknown','blocked',{}, {},0,'failed'),
            ('external','waiting_quota',dict(blocks=[dict(kind='quota',id='quota')]),{},0,'waiting_external'),
            ('ended','cancelled',{}, {},0,'cancelled'),
        ]
        for key,state,cursor,choice,paused,_ in rows:
            db.execute('INSERT INTO episode VALUES(?)',(key,))
            db.execute('INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,choice,pause_requested,created_at,updated_at) VALUES(?,?,?, ?,?,?,?,0,0)',
                       (key,key,'delivery',state,json.dumps(cursor),json.dumps(choice),paused))
            db.execute('INSERT INTO child VALUES(?)',(key,))
        _migrate_continuous_states(db)
        _migrate_continuous_states(db)
        assert dict(db.execute('SELECT id,state FROM continuous_run'))=={key:expected for key,*_,expected in rows}
        assert db.execute('PRAGMA foreign_key_list(child)').fetchone()[2]=='continuous_run'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT count(*) FROM child JOIN continuous_run ON id=run_id').fetchone()[0]==len(rows)
        assert db.execute("SELECT json_extract(cursor,'$.storyGoal') FROM continuous_run WHERE id='direction'").fetchone()[0]=='A'
        for state in ('blocked','waiting_choice'):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE continuous_run SET state=? WHERE id='e10'",(state,))


def test_explicit_grid_product_uses_real_generation_receipt(tmp_path,monkeypatch):
    env,case,cid,_=finishing_fixture(tmp_path,config='puggy',spec_overrides=dict(productDrawingReferences=['02']))
    wb=env.wb;eid=case['eid'];master=wb.use_project_product(eid,'02','cup')['versionId']
    plan=case['plan'];plan['segments'][0]['strategy']='grid'
    plan_id,_=env.save_adopt(eid,'plan',plan,'grid-product')
    with env.db() as db:
        from workbench.stressrepairs import adopted
        baseline=adopted(db,eid);stamp=wb._continuous_user_stamp(db,eid,[])
    wb._continuous_cursor(cid,base=baseline,userStamp=stamp,pendingUserChange=False)
    def place(cid,kind,obj,instruction,materials,validate,**kwargs):
        a=dict(placements=[dict(code='02',shotId='S01',intent='窗台上的马克杯')]);assert not validate(a);return a,'placement'
    monkeypatch.setattr(wb,'_continuous_bounded_json',place)
    wb._continuous_products(cid)
    assert env.art(eid,'plan')['adopted_version_id']==plan_id
    grids=wb._grid_artwork(cid,eid,plan_id);board=grids['G01']['versionId']
    assert master in grids['G01']['inputs']
    by_seg=lambda sid:dict(segments={sid:dict(refs=[dict(assetVersionId=board)])})
    placements=wb.product_placements(eid,candidate=by_seg('G01'))
    assert placements['verified']==1 and placements['records'][0]['drawingReference']
    assert not placements['records'][0]['observation'] and not placements['records'][0]['directReference']
    assert wb.product_placements(eid,candidate=by_seg('G02'))['verified']==0


def test_adopted_art_reaches_flex_prompt_and_grid_drawing_only(tmp_path, monkeypatch):
    from workbench import promptdraft
    from workbench.db import transaction

    env, case, cid, _ = finishing_fixture(tmp_path, scoped=True)
    wb, eid = env.wb, case['eid']
    plan = case['plan']
    plan['segments'][1]['strategy'] = 'grid'
    asset_design = dict(assets=[
        dict(id=key, kind='state', description=label, imagePrompt=label, sceneIds=['场02'])
        for key, label in [('before', '完好外观'), ('after', '破损外观'), ('unused', '备用外观')]
    ], notes='')
    env.save_adopt(eid, 'asset_design', asset_design, 'states')
    images = {}
    for i, need in enumerate(wb.asset_worksheet(eid)['rows']):
        vid = env.image(case['pid'], eid, need['title'], 'state', rgb=(i+1, 3, 5))['versionId']
        images[need['need_key']] = vid
        with transaction(wb.db_path) as db:
            db.execute('INSERT INTO asset_need_candidate VALUES(?,?)', (need['id'], vid))
        wb.set_asset_need(eid, need['need_key'], need['revision'], 'reuse', 'reuse-'+vid, version_id=vid)

    plan_id, _ = env.save_adopt(eid, 'plan', plan, 'mixed-board')

    def choose(cid, kind, plan_id, instruction, material, validate, **kwargs):
        assert kind == '采用素材逐段选图'
        assert all(len(seg['candidates']) == 3 for seg in material['segments'])
        selection = dict(segments={'G01': [dict(key='before', shots=['S01'])],
                                   'G02': [dict(key='after', shots=['S03'])]})
        assert validate(selection) == []
        return selection, 'selected'

    monkeypatch.setattr(wb, '_continuous_bounded_json', choose)
    grids = wb._grid_artwork(cid, eid, plan_id)
    assert images['after'] in grids['G02']['inputs']
    assert images['before'] not in grids['G02']['inputs']
    draft = promptdraft.draft(wb, eid)['segments']
    refs = {sid: {r['assetVersionId']: r for r in s['refs']} for sid, s in draft.items()}
    assert refs['G01'][images['before']]['shots'] == ['S01']
    assert set(refs['G02']) == {grids['G02']['versionId']}
    assert all(images['unused'] not in row for row in refs.values())
    assert not wb.image_assessments(eid)
    # Verify the exported files, not just the rows shown in the worksheet.
    import json
    from zipfile import ZipFile
    content = dict(segments=draft)
    version, _ = env.save_adopt(eid, 'prompts', content, 'scoped-art-prompts')
    env.review_segments(version, ['G01', 'G02'], cid='scoped-art')
    pub = env.art(eid, 'publish')['adopted_version_id']
    env.save_adopt(eid, 'publish', wb._continuous_version(pub), 'scoped-art-publish')
    problems, _ = wb.delivery_problems(eid, 'full')
    assert not problems, str(problems)
    delivery = wb.deliver(eid, 'full', 'scoped-art-deliver')['deliveryId']
    package = wb.package(delivery, ['G01', 'G02'], 'scoped-art-package')['packageId']
    with ZipFile(wb.package_file(package)) as archive:
        packed = json.loads(archive.read('project.json'))
    packed_refs = {s['id']: {r['assetId'] for r in s['refs']} for s in packed['segments']}
    assert packed_refs == {sid: set(row) for sid, row in refs.items()}
