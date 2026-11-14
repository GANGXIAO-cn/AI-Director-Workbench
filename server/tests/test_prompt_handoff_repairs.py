"""Frozen assembly, exact time projection and candidate-bound note repair."""
import copy
import json
import pytest
from workbench import readable, rhythm, promptdraft, productionnotes as pn
from workbench.promptassembly import assemble
from workbench.executors.fake import draft
from test_continuous_finishing import finishing_fixture
from test_production_notes import basis, response
from test_image_assess import reply


def test_exact_time_prefix_only():
    assert readable.strip_matching_window_prefix('段内00:00.000—00:04.000。抬手。',0,4000)=='抬手。'
    for text in ('段内00:00.000—00:03.000。抬手。','抬手，段内00:00.000—00:04.000。','用2秒说完。'):
        assert readable.strip_matching_window_prefix(text,0,4000)==text


def test_multiple_locations_and_separate_owner_error():
    b=basis();b['notes']=b['notes'][:1]
    b['stages']['asset_design']={'productionNoteResponses':[response('P01','prompts','assigned',reason='正文落实')]}
    content=dict(segments={'G01':{},'G02':{}},productionNoteResponses=[response('P01','prompts',whereIds=['G01','G02'])])
    assert pn.check('prompts',content,b,True)==[]
    content['productionNoteResponses'][0]['whereIds']=['missing']
    errors=pn.check('prompts',content,b,True)
    assert any('missing' in e for e in errors) and not any('owner' in e for e in errors)
    for item in ({'whereIds':[]},{'whereIds':['G01','G01']},{'where':'G01','whereIds':['G02']}):
        with pytest.raises(ValueError):pn.normalize_where_ids(item)
    assert pn.normalize_where_ids({'where':'G01'})==['G01']


@pytest.mark.parametrize('headings',[readable.FLEX,readable.GRID])
def test_frozen_assembly_preserves_explicit_reference_scope(headings):
    blocks={h:'' for h in headings};fixed={h:[] for h in headings};fixed[headings[0]]=['固定身份描述。']
    scaffold=dict(blocks=blocks,fixedSections=fixed,refs=[dict(assetVersionId='image-a',kind='image',label='图片1',shots=['S1'],role='身份')])
    entry=dict(authoredBlocks=dict(blocks),refs=[dict(scaffold['refs'][0],shots=['S2'],role='外观')],sound={})
    assembled=assemble(entry,scaffold)
    assert assembled['txt'].count('固定身份描述。')==1
    assert assembled['refs'][0]['shots']==['S2'] and assembled['refs'][0]['role']=='外观'
    assert assemble({'authoredBlocks':blocks},scaffold)['refs']==scaffold['refs']
    assert assemble({'txt':'旧格式'},scaffold)=={'txt':'旧格式'}
    with pytest.raises(ValueError):assemble(dict(entry,refs=[dict(entry['refs'][0],assetVersionId='unknown')]),scaffold)
    with pytest.raises(ValueError):assemble(dict(entry,txt='混合协议'),scaffold)


@pytest.mark.parametrize('provider',['codex','antigravity'])
def test_frozen_scaffold_round_trip_and_notes_real_service(tmp_path,provider):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb;eid=case['eid']
    plan=copy.deepcopy(case['plan'])
    first=plan['shots'][0];first['action']='段内00:00.000—00:04.000。'+first['action']
    env.save_adopt(eid,'plan',plan,'prefix')
    frozen=promptdraft.draft(wb,eid)
    rhythm.check(plan,frozen['segments'])
    rows=rhythm.plan_rows(plan)
    rows[0]['TXT定位短句']='段内00:00.000—00:04.000。'+rows[0]['TXT定位短句']
    columns=list(rows[0]);custom='| '+' | '.join(columns)+' |\n| '+' | '.join(['---']*len(columns))+' |\n'+'\n'.join('| '+' | '.join(row[c] for c in columns)+' |' for row in rows)
    custom_plan=dict(plan,rhythmTable=custom)
    rhythm.check(custom_plan,frozen['segments'])
    assert custom_plan['rhythmTable']==custom
    entries={}
    for sid,s in frozen['segments'].items():
        free=copy.deepcopy(s['blocks'])
        for heading,phrases in s['fixedSections'].items():
            for phrase in phrases:free[heading]=free[heading].replace(phrase,'',1)
        entries[sid]=dict(authoredBlocks=free,sound=s['sound'])
    if provider=='codex':env.codex.script=[lambda req:draft(req,segments=json.dumps(entries),notes='仅用于制作管理')]
    else:
        env.ag.strict_schema=False
        env.ag.script=[lambda req:dict(kind='draft',fields=dict(segments=entries,notes='仅用于制作管理'))]
    job=wb.create_task(eid,'prompts','固定栏目装配',base_version_id=vid,prompt_images=False,executor={'create':{'provider':provider}})
    env.drain(job['runId'])
    with env.db() as db:
        run=dict(db.execute('SELECT * FROM run WHERE id=?',(job['runId'],)).fetchone())
        assert run['status']=='succeeded',run['diagnostic']
        saved=json.loads(db.execute('SELECT content FROM version WHERE source_run_id=?',(job['runId'],)).fetchone()[0])
    assert saved.get('notes')=='仅用于制作管理',[(p.name,p.read_text()) for p in wb.store.run_dir(job['runId']).glob('validation-attempt-*.json')]
    assert all('authoredBlocks' not in e for e in saved['segments'].values())
    rhythm.check(plan,saved['segments'])


def test_record_patch_preserves_prose_and_recovers_owned_candidate(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    b=basis();b['notes']=b['notes'][:1]
    b['stages']['asset_design']={'productionNoteResponses':[response('P01','prompts','assigned',reason='正文负责')]}
    monkeypatch.setattr(pn,'context',lambda db,eid:b)
    original=wb._continuous_version(vid)
    quote=original['segments']['G01']['txt'].splitlines()[1]
    before=len(env.codex.calls)
    env.codex.script=[reply({'resolutions':[dict(id='P01',mode='record_only',whereIds=['G01'],evidence=[dict(segmentId='G01',quote=quote)],reason='正文有对应动作') ]})]
    final=wb._repair_prompt_notes(cid,vid)
    updated=wb._continuous_version(final)
    assert final!=vid and updated['segments']==original['segments']
    assert updated['productionNoteResponses'][0]['whereIds']==['G01']
    assert wb._continuous_record(cid)['cursor']['promptCandidate']==final
    wb._set_prompt_candidate(cid,vid)
    assert wb._resume_prompt_candidate(cid,vid)==final
    assert wb._repair_prompt_notes(cid,final)==final
    assert len(env.codex.calls)==before+1
    manifest=wb.run_manifest(env.codex.calls[-1].run_id)['manifest']
    assert manifest['stage']=='prompts'


def test_legal_location_missing_content_revises_only_target_then_checks(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    b=basis();b['notes']=b['notes'][:1]
    b['stages']['asset_design']={'productionNoteResponses':[response('P01','prompts','assigned',reason='正文负责')]}
    monkeypatch.setattr(pn,'context',lambda db,eid:b)
    content=wb._continuous_version(vid)
    content.update(notes='原管理说明',decisionNotes=[],productionNoteResponses=[response('P01','prompts',where='G01')])
    vid=wb.save_manual(env.art(case['eid'],'prompts')['id'],vid,content,'with-record')['versionId']
    wb._continuous_internal_version(cid,vid)
    def revision(req):
        changed=copy.deepcopy(content)
        changed['segments']['G01']['txt']+='\n阿福扶稳箱子。'
        changed['segments']['G02']['txt']+='\n不应保留的越界文字。'
        return draft(req,segments=json.dumps(changed['segments']),notes='越界说明',productionNoteResponses='[]')
    env.codex.script=[reply(dict(resolutions=[dict(id='P01',mode='revise_segments',reason='当前正文没有扶稳',missingSegments=['G01'],revisionInstruction='补上扶稳箱子的动作')])),revision,
                      reply(dict(resolutions=[dict(id='P01',mode='record_only',reason='新正文已写扶稳',whereIds=['G01'],evidence=[dict(segmentId='G01',quote='阿福扶稳箱子。')])]))]
    before=len(env.codex.calls)
    final=wb._repair_prompt_notes(cid,vid)
    assert final==vid and len(env.codex.calls)==before
    assert wb._continuous_version(final)==content


def test_fabricated_quote_cannot_mark_applied(tmp_path,monkeypatch):
    from workbench.continuous import Halt
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    b=basis();b['notes']=b['notes'][:1]
    b['stages']['asset_design']={'productionNoteResponses':[response('P01','prompts','assigned',reason='正文负责')]}
    monkeypatch.setattr(pn,'context',lambda db,eid:b)
    invalid=dict(resolutions=[dict(id='P01',mode='record_only',reason='声称完成',whereIds=['G01'],evidence=[dict(segmentId='G01',quote='根本不存在的正文')])])
    before=len(env.art(case['eid'],'prompts')['versions'])
    env.codex.script=[reply(invalid),reply(invalid)]
    with pytest.raises(Halt):wb._repair_prompt_notes(cid,vid)
    assert len(env.art(case['eid'],'prompts')['versions'])==before
    assert '引文不属于' in wb._continuous_record(cid)['reason']


def test_real_grid_asset_scaffold_assembly(tmp_path):
    from helpers import Env
    env=Env(tmp_path);pid,eid=env.new_episode();env.adopt_story(eid);env.script(eid)
    plan=env.plan86({'G01':[8000]},{'G01':'grid'})
    env.save_adopt(eid,'plan',plan)
    image=env.image(pid,eid,'G01 开场九宫格','grid',grid=True)
    frozen=env.wb.draft_prompts(eid)['segments']['G01']
    free=copy.deepcopy(frozen['blocks'])
    for heading,phrases in frozen['fixedSections'].items():
        for phrase in phrases:free[heading]=free[heading].replace(phrase,'',1)
    actual=assemble(dict(authoredBlocks=free,sound=frozen['sound']),json.loads(json.dumps(frozen,sort_keys=True)))
    readable.validate(actual,'grid',8000)
    rhythm.check(plan,{'G01':actual})
    assert actual['refs'][0]['assetVersionId']==image['versionId']


def test_candidate_setter_preserves_user_change_flags(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    cursor=wb._continuous_record(cid)['cursor']
    wb._continuous_cursor(cid,pendingUserChange=True)
    wb._set_prompt_candidate(cid,vid)
    actual=wb._continuous_record(cid)['cursor']
    assert actual['pendingUserChange'] is True
    assert actual.get('ownedVersions')==cursor.get('ownedVersions')
