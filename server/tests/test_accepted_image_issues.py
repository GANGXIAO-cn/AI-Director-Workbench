"""H-T6d: accepted defects remain visible, unrelated conflicts remain blocking."""
import json
from pathlib import Path
import pytest
from helpers import Env
from workbench.db import transaction,dumps,new_id
from workbench.errors import Rejected
from workbench import shotrefs


def test_H_T6d_accepted_conflict_survives_observation_review_and_delivery(tmp_path):
    env=Env(tmp_path);wb=env.wb
    case=env.full_episode(segments={'G01':[8000]},deliver=False)
    eid=case['eid'];face=case['img']['versionId'];plan=case['plan']
    for shot in plan['shots']:shot.update(visibleRoles=['阿福'],propStates={})
    env.save_adopt(eid,'plan',plan,'scoped-plan')
    wb.set_role_baseline(eid,'阿福',face,'阿福','baseline')
    wb.set_cast(eid,'G01','阿福','ok',face)
    continuous=wb.start_continuous(eid,command_id='continuous')
    # Same persisted user-direction shape as _image_choose; H-T6b exercises the card.
    with transaction(wb.db_path) as db:
        decision=new_id()
        db.execute('INSERT INTO ai_decision(id,episode_id,continuous_id,stage,kind,ref,reason,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(decision,eid,continuous['id'],'assets','choose_image',face,'导演接受',dumps(dict(userDirection=dict(instruction='用这张，保留这个问题'))),wb.clock()))
        db.execute('INSERT INTO accepted_image_issue VALUES(?,?,?,?,?)',(face,'anatomy:左手','左手形态有缺陷',decision,wb.clock()))
    observed=wb.save_shot_observation(case['prompts'],'G01',face,wb.asset_version(face)['sha256'],['S01'],'人物左手形态有缺陷',{},[dict(text='左手形态有缺陷',acceptedIssue='anatomy:左手')],command_id='observed')['versionId']
    assert not wb.adoption_gate(observed)['formatErrors']
    wb.adopt(env.art(eid,'prompts')['id'],observed,case['prompts'],'adopt')
    env.review_segments(observed,['G01'])
    assert wb.prompt_review_status(observed)['G01']['ok']
    for executor in (env.codex,env.claude):
        for request in executor.calls:
            manifest=wb.run_manifest(request.run_id)['manifest']
            if request.purpose in ('cold_read','comparison') and manifest.get('targetVersionId')==observed:
                assert any(item['role']=='accepted_image_issue' for item in manifest['inputs'])
    output=wb.deliver(eid,'trial','delivery',['G01'])
    page=(Path(output['path'])/'00_图文对照.html').read_text()
    assert '用户已接受的已知问题' in page and '左手形态有缺陷' in page
    assert wb.verify_delivery(output['deliveryId'])['ok']
    bad=wb.save_shot_observation(observed,'G01',face,wb.asset_version(face)['sha256'],['S01'],'另有未接受冲突',{},[dict(text='未接受的问题',acceptedIssue='usage:其他')],command_id='bad')['versionId']
    assert not any('图片仍有冲突' in e for e in wb.adoption_gate(bad)['formatErrors'])
    # Historical conflicts remain in the record, not converted to acceptance.
    assert wb._continuous_version(bad)['segments']['G01']['refs'][0]['visualReviewId']


def test_H_T6d_blocking_waiver_requires_referenced_accepted_issue(tmp_path):
    from workbench.executors.fake import review
    env=Env(tmp_path);wb=env.wb;case=env.full_episode(segments={'G01':[8000]},deliver=False)
    eid=case['eid'];vid=case['prompts'];image=case['img']['versionId']
    wb.save_step_config(eid,'prompts',keep='保持手部实际缺陷')
    env.claude.script=[lambda req:review(req,'return',[dict(category='clarity',reason='左手形态缺陷')])]
    env.drain(wb.start_prompt_review(vid,'G01','comparison','review')['runId'])
    issue=wb.prompt_review_status(vid)['G01']['comparison']['issues'][0]
    with transaction(wb.db_path) as db:db.execute('UPDATE issue SET blocking=1 WHERE id=?',(issue['id'],))
    with pytest.raises(Rejected,match='阻断'):wb.waive_segment_issue(issue['id'],command_id='ordinary')
    with pytest.raises(Rejected,match='没有对应'):wb.waive_segment_issue(issue['id'],accepted_issue=dict(assetVersionId=image,issueKey='anatomy:左手'),command_id='missing')
    continuous=wb.start_continuous(eid,command_id='continuous')
    decision=wb._continuous_log(continuous['id'],'assets','choose_image',image,'用户接受',dict(userDirection=dict(instruction='用这张，保留这个问题')))
    with transaction(wb.db_path) as db:db.execute('INSERT INTO accepted_image_issue VALUES(?,?,?,?,?)',(image,'anatomy:左手','左手形态缺陷',decision,wb.clock()))
    # Review scope changed with the accepted list: obtain a current review first.
    env.review_segments(vid,['G01'],cid='fresh')
    wb.save_step_config(eid,'prompts',keep='手部实际缺陷保持原样')
    env.claude.script=[lambda req:review(req,'return',[dict(category='clarity',reason='左手形态缺陷')])]
    env.drain(wb.start_prompt_review(vid,'G01','comparison','fresh-issue')['runId'])
    issue=wb.prompt_review_status(vid)['G01']['comparison']['issues'][0]
    with transaction(wb.db_path) as db:db.execute('UPDATE issue SET blocking=1 WHERE id=?',(issue['id'],))
    wb.waive_segment_issue(issue['id'],'用户已接受的已知问题','accepted',accepted_issue=dict(assetVersionId=image,issueKey='anatomy:左手'))
    assert wb.prompt_review_status(vid)['G01']['ok']
