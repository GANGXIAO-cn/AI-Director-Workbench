"""Package A receipt, manifest and legacy tests; offline only."""
import json
from pathlib import Path
import pytest
from test_stress_repairs import fixture
from workbench.executors.fake import draft
from workbench import workflow

@pytest.mark.parametrize('number,missing,error', [('A-T1','productionNotes','productionNotes 须为数组'), ('A-T2','changesCore','changesCore 须为布尔值')])
def test_A_T1_A_T2_receipt_retries_once(tmp_path, number, missing, error):
    env,eid,_ = fixture(tmp_path)
    content = dict(retelling='值班员检查后交接',scope='01–05',diagnosis='完成核对',issues=[],options=[dict(issueId='I01',repairScope='场01',proposal='调整呈现',changesCore=False)],productionNotes=[])
    if missing == 'productionNotes':
        content.pop(missing)
    else:
        content['options'][0].pop(missing)
    def reply(req):
        return draft(req, **{k:json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v for k,v in content.items()})
    env.codex.script=[reply,reply]
    count=len(env.codex.calls)
    run=env.wb.create_task(eid,'stress','重新审查')['runId']
    env.drain(run)
    with env.db() as db:
        record=dict(db.execute('SELECT * FROM run WHERE id=?',(run,)).fetchone())
    assert record['status']=='failed', record
    assert error in str(record)
    assert len(env.codex.calls)-count==2
    assert record['format_attempts']==1  # one retry after the original call

def test_A_T3_production_notes_only_to_production(tmp_path):
    env,eid,report=fixture(tmp_path)
    content=next(v['content'] for v in env.art(eid,'stress')['versions'] if v['id']==report)
    notes=[dict(id='P01',sceneIds=['场01'],note='落实门锁结构'),dict(id='P02',sceneIds=['场02'],note='落实道具尺寸')]
    content.update(issues=[],options=[],productionNotes=notes)
    env.save_adopt(eid,'stress',content,'notes')
    for stage in ('asset_design','script'):
        run=env.wb.create_task(eid,stage,'按采用稿起草')['runId']
        entries=env.wb.store.manifest_texts(env.wb.run_manifest(run)['manifest'])
        actual=[e for e in entries if e['role']=='production_notes']
        if stage=='asset_design':
            assert len(actual)==1 and json.loads(actual[0]['text'])==notes
            assert any(e['name']=='制作提醒落实' for e in entries)
        else:
            assert not actual

def test_A_T4_legacy_report_read_and_adopt(tmp_path):
    env,eid,vid=fixture(tmp_path)
    art=next(a for a in env.wb.episode_view(eid)['artifacts'] if a['stage']=='stress')
    assert art['adopted_version_id']==vid
    content=next(v['content'] for v in art['versions'] if v['id']==vid)
    assert 'productionNotes' not in content
    assert not workflow.check('stress',content)
    assert {b['id'] for b in env.wb.stress_worklist(eid)['blockers']}=={'I01','I02'}

def test_A_T5_method_source_bytes():
    root=Path(__file__).resolve().parents[2]/'resources/skills/unified-film'
    sources=(root/'SOURCES.md').read_text()
    for name in ('stress.md','story_review.md'):
        assert f'{name} 文件为 {(root/name).stat().st_size} 字节' in sources
    assert '产品负责人 2026-11-11 决定（D2）' in sources
