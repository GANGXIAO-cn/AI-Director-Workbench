import json

from helpers import Env
from workbench.executors.fake import draft


def test_convert_discussion_pins_conversation_and_records_confirmed_wording(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    _,eid=env.new_episode()
    vid=env.adopt_story(eid)
    art=env.art(eid,"story")
    options=dict(auto_dialogue=True,workspace_revision=art["revision"],base_version_id=vid)
    talk=wb.create_task(eid,"story","结尾怎样更清楚？",**options)
    env.drain(talk["runId"])
    msg=env.art(eid,"story")["tasks"][-1]["messages"][-1]
    assert json.loads(msg["payload"])["stageDiscussion"]
    later=wb.post_message(talk["taskId"],"另一个思路先搁置","later",**options)
    env.drain(later["runId"])
    wording="结尾保留交接。"
    result=wb.create_task(eid,"story",wording,discussion_id=msg["id"],keep_instruction=True,**options)
    env.drain(result["runId"])
    after=env.art(eid,"story")
    assert after["adopted_version_id"]==vid and len(after["versions"])==2,after["tasks"][-1]["runs"]
    assert wb.director_decisions(eid)[-1]["text"]==wording
    prompt=env.codex.calls[-1].prompt
    assert msg["text"] in prompt and "另一个思路先搁置" not in prompt


def test_missing_scene_requires_explicit_removal(tmp_path):
    env=Env(tmp_path)
    wb=env.wb
    _,eid=env.new_episode()
    env.adopt_story(eid)
    scenes=[dict(id="场01",action="看钟"),dict(id="场02",action="推门")]
    vid,_=env.save_adopt(eid,"script",dict(scenes=scenes))
    art=env.art(eid,"script")
    goal="只保留第一场"
    opts=dict(auto_dialogue=True,workspace_revision=art["revision"],base_version_id=vid)
    def response(removed):
        return lambda r:dict(draft(r,scenes=json.dumps(scenes[:1])),directorRequest=goal,reply="",removedIds=removed)
    env.codex.script.extend([response([]),response([])])
    first=wb.create_task(eid,"script",goal,**opts)
    env.drain(first["runId"])
    assert len(env.art(eid,"script")["versions"])==1
    env.codex.script.append(response([dict(field="scenes",id="场02")]))
    second=wb.create_task(eid,"script",goal,**opts)
    env.drain(second["runId"])
    assert len(env.art(eid,"script")["versions"])==2
