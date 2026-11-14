from workbench import unifiedmethods
from workbench.methodpack import method_entries
from workbench.db import transaction
from test_continuous_finishing import finishing_fixture


def test_adapted_stages_send_only_current_local_methods():
    pack=unifiedmethods.load()
    for stage in ('asset_design','acting','plan','prompts','cold_read','comparison'):
        entries=method_entries(pack,stage)
        assert entries and any(e['name']==stage+'.md' for e in entries)
        assert all('unified-film/' in e['methodSource']['path'] for e in entries)
        assert any(k.startswith(stage+'_source_') for k in pack['files'])  # provenance preserved


def test_retry_refreshes_only_method_leaves_frozen_materials_intact(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    job=wb.start_prompt_review(vid,'G01','cold_read','method-review')
    with env.db() as db:old=dict(db.execute('SELECT * FROM run WHERE id=?',(job['runId'],)).fetchone())
    header,entries=wb._snapshot_parts(old)
    entries.append(dict(role='method',name='obsolete-audit.md',text='旧审图规则'))
    with transaction(wb.db_path) as db:
        db.execute("UPDATE run SET status='failed' WHERE id=?",(old['id'],))
        rid=wb._enqueue_snapshot(db,header,entries,old['counted_round'])
        db.execute("UPDATE run SET status='failed' WHERE id=?",(rid,))
    before=wb.run_manifest(rid)['manifest']
    retry=wb.retry_run(rid,command_id='refresh-method')
    after=wb.run_manifest(retry['runId'])['manifest']
    assert after['methodRefresh']['sourceRunId']==rid
    assert not any(e['name']=='obsolete-audit.md' for e in after['inputs'])
    assert [e for e in before['inputs'] if e['role'] not in ('method','project_method')]==[e for e in after['inputs'] if e['role'] not in ('method','project_method')]
    assert any(e['name']=='obsolete-audit.md' for e in wb.run_manifest(rid)['manifest']['inputs'])


def test_actual_cold_and_comparison_requests_have_consistent_boundary(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    for kind in ('cold_read','comparison'):
        job=wb.start_prompt_review(vid,'G01',kind,'boundary-'+kind)
        env.drain(job['runId'])
        request=next(r for executor in (env.codex,env.claude) for r in executor.calls if r.run_id==job['runId'])
        assert '只在 observations 说明未测' in request.prompt
        for retired in ('关键站位要能从本段开场图或九宫格首格看出具体关系','文字里的用途说明不能解除图中冲突','镜头表本身也要对照原文','缺少图像或视频证据，记 continuity、uncertain'):
            assert retired not in request.prompt
