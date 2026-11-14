"""Small reusable preview fixture; no project, library or media directory copies."""
import json
from test_image_assess import reply
from workbench.db import transaction


def prepare(env,folder):
    marker=folder/'continuous-preview.json';wb=env.wb
    if marker.exists():
        saved=json.loads(marker.read_text());cid=saved['continuousId'];eid=saved['episodeId']
        with transaction(wb.db_path) as db:
            db.execute("UPDATE continuous_run SET state='done' WHERE episode_id=? AND id!=? AND state NOT IN ('done','cancelled')",(eid,cid))
            # Reuse the original delivery, but make its preview the current test
            # run rather than the discussion receipt from the previous pass.
            db.execute('UPDATE continuous_run SET created_at=? WHERE id=?',(wb.clock(),cid))
        run=wb._continuous_record(cid)
        wb._continuous_update(cid,state='waiting_choice',choice={k:v for k,v in run['choice'].items() if k not in ('selected','reply')},reason=run['choice']['title'])
    else:
        case=env.full_episode(deliver=False);eid=case['eid']
        run=wb.start_continuous(eid,command_id='preview-fixture');cid=run['id']
        wb._continuous_update(cid,state='running');wb._continuous_delivery(cid)
        saved=dict(continuousId=cid,episodeId=eid);marker.write_text(json.dumps(saved))
    env.codex.script=[reply(dict(kind='discussion',earliestStage=None,instruction='这是本集图文交付预览，可先查看再决定出包。',segments=[],reason='只讨论，不改稿'))]*8
    return saved
