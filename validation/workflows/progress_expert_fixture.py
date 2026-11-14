"""Reusable small material-only fixture; never imports daily data."""
import json
import sys
from pathlib import Path
import uvicorn
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'server'),str(ROOT/'server/tests')]
from helpers import Env
from test_image_assess import fixture,assessment
from workbench.app import create_app
from workbench.db import transaction

folder=ROOT/'output/validation/20261112/progress-expert'
folder.mkdir(parents=True,exist_ok=True)
metadata=folder/'fixture.json'
if metadata.exists():
    env=Env(folder);info=json.loads(metadata.read_text())
else:
    env,eid,vid=fixture(folder)
    original=env.wb._advance_continuous
    env.wb._advance_continuous=env.wb._continuous_record
    result=env.wb.start_continuous(eid,target_stage='delivery',command_id='fixture')
    env.wb._advance_continuous=original
    env.wb._continuous_update(result['id'],state='paused',reason='关键点界面夹具，不执行生成')
    need=env.wb._need(eid,'bell')
    with transaction(env.wb.db_path) as db:db.execute('INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)',(need['id'],vid))
    env.wb.adopt_asset_needs(eid,{'bell':dict(versionId=vid,revision=need['revision'])},'fixture-adopt')
    body=assessment(vid,verdict='blocked',critical=[dict(type='usage',where='支撑',text='门铃支撑关系仍有问题')])
    from workbench.db import dumps,new_id
    with transaction(env.wb.db_path) as db:db.execute('INSERT INTO image_assessment VALUES(?,?,?,?,?,?,?,?)',(new_id(),eid,need['id'],vid,'fixture','blocked',dumps(body),env.wb.clock()))
    env.wb._material_issue(result['id'],env.wb._need(eid,'bell'),body,'门铃支撑关系仍有问题，当前图片由 AI 暂用。')
    info=dict(episodeId=eid,projectId=env.wb.episode_view(eid)['episode']['project_id'])
    metadata.write_text(json.dumps(info))
print(json.dumps(info),flush=True)
uvicorn.run(create_app(env.wb),host='127.0.0.1',port=18779,log_level='warning')
