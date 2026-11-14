import pytest
from helpers import Env
from workbench.errors import Rejected, NotFound


def test_history_pages_and_metadata(tmp_path):
    env=Env(tmp_path);wb=env.wb
    _,eid=env.new_episode()
    task=wb.create_task(eid,'story','用户原话')
    with env.db() as db:
        thread=db.execute('SELECT id FROM thread WHERE task_id=?',(task['taskId'],)).fetchone()[0]
        for i in range(67):wb._message(db,thread,'user',str(i),None,None)
        db.execute('UPDATE message SET created_at=123 WHERE thread_id=?',(thread,))
    full=wb.episode_messages(eid,limit=100)['messages']
    first=wb.episode_messages(eid)
    assert len(first['messages'])==30 and first['hasMore']
    with env.db() as db:wb._message(db,thread,'user','new',None,None)
    recovered=list(first['messages']);page=first
    while page['hasMore']:
        page=wb.episode_messages(eid,before=page['before'])
        recovered=page['messages']+recovered
    assert [m['id'] for m in recovered]==[m['id'] for m in full]
    view=wb.episode_view(eid,chat_limit=30)
    assert len(view['chatPage']['messages'])==30
    tasks=[t for a in view['artifacts'] for t in a['tasks']]
    assert sum(len(t['messages']) for t in tasks)==30
    target=next(t for t in tasks if t['id']==task['taskId'])
    assert target['userMessageCount']==69
    assert target['messageCount']==69
    assert len(wb.episode_view(eid)['artifacts'][0]['tasks'][0]['messages'])>=1
    with pytest.raises(Rejected):wb.episode_messages(eid,limit=0)
    with pytest.raises(Rejected):wb.episode_messages(eid,before='invalid')


def test_internal_instructions_are_not_user_history_or_decisions(tmp_path):
    env=Env(tmp_path);wb=env.wb
    _,eid=env.new_episode()
    task=wb.create_task(eid,'story','内部指令')
    with env.db() as db:
        thread=db.execute('SELECT id FROM thread WHERE task_id=?',(task['taskId'],)).fetchone()[0]
        mid=db.execute('SELECT id FROM message WHERE thread_id=?',(thread,)).fetchone()[0]
        wb._message(db,thread,'assistant','内部结果',None,None,payload={'internalJudgment':'test'})
    assert wb.episode_messages(eid)['messages']==[]
    art=env.art(eid,'story')
    assert art['tasks'][0]['internal']
    assert wb.decision_candidates(art['id'])==[]
    with pytest.raises(NotFound):wb.record_message_decision(eid,mid,'internal-not-decision')
