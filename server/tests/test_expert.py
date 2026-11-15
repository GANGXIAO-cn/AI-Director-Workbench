import pytest

from helpers import Env
from workbench.errors import Conflict, Rejected
from workbench.executors.fake import review


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_retired_expert_commands_are_not_exposed(env):
    from workbench.app import COMMANDS, QUERIES, ASYNC
    old={'authorize_expert','expert_preview','expert_run','expert_history','expert_view',
         'pause_expert','manual_expert','claim_expert','choose_continuous'}
    assert old.isdisjoint(COMMANDS | QUERIES | ASYNC)
    assert not any(hasattr(env.wb,name) for name in old)


def test_historical_expert_ownership_cannot_lock_episode(env):
    import json
    wb=env.wb;pid,eid=env.new_episode()
    with env.db() as db:
        db.execute("INSERT INTO expert_auth(id,project_id,episodes,stop_stage,division,stops,state,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   ('old',pid,json.dumps([eid]),'story','{}','[]','running',0))
        db.execute("INSERT INTO expert_episode_owner VALUES(?,?)",(eid,'old'))
        db.commit()
    wb.start_continuous_precheck(eid)
    with env.db() as db:
        assert db.execute("SELECT state FROM expert_auth WHERE id='old'").fetchone()[0]=='running'


def test_expert_delivery_manifest_compared_to_database(env):
    import json
    from pathlib import Path
    ep = env.full_episode(segments={"G01":[8000]})
    d = ep["delivery"]
    root = Path(d["path"])
    manifest = root / "manifest.json"
    modified = json.loads(manifest.read_text())
    modified["files"].pop("episode.json")
    manifest.write_text(json.dumps(modified))
    result = env.wb.verify_delivery(d["deliveryId"])
    assert not result["ok"] and result["errors"][0]["file"] == "manifest.json"
