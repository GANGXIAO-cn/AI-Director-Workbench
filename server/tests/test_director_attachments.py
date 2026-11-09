import hashlib
import pytest
from helpers import Env, png_bytes
from workbench.errors import Rejected


def test_kickoff_and_message_files_reach_only_their_target(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid = wb.create_project("_template","通用项目")["projectId"]
    kickoff_image = png_bytes(36,42,(9,1,2))
    message_image = png_bytes(40,32,(3,7,21))
    args = dict(episode_id=None,project_id=pid,display_code="E01",idea_text="末班车离站",source_type="idea",
                executor_plan={"create":{"provider":"codex"},"review":{"provider":"claude"}},command_id="start")
    with pytest.raises(Rejected):
        wb.start_episode(**args,files=[("坏图.png",b"bad")])
    assert wb.project_overview(pid)["episodes"] == []
    start = wb.start_episode(**args,files=[("车站.png",kickoff_image)])
    assert wb.start_episode(**args,files=[("车站.png",kickoff_image)]) == start
    eid = start["episodeId"]
    asset, = wb.episode_assets(eid)
    assert asset["sha256"] == hashlib.sha256(kickoff_image).hexdigest()
    files = [("门.jpg",message_image),("走位.md","先回头再关门".encode())]
    task = wb.create_task(eid,"story","按参考起草",files=files,command_id="send")
    assert wb.create_task(eid,"story","按参考起草",files=files,command_id="send") == task
    env.drain(task["runId"])
    req = env.codex.calls[-1]
    assert [p.read_bytes() for p in req.images] == [kickoff_image,message_image]
    assert "先回头再关门" in req.prompt and "PUGGY" not in req.prompt
    follow = wb.post_message(task["taskId"],"重写末段","next")
    env.drain(follow["runId"])
    assert [p.read_bytes() for p in env.codex.calls[-1].images] == [kickoff_image]
    assert "先回头再关门" not in env.codex.calls[-1].prompt
    _, other = env.new_episode(code="E02",project_id=pid)
    env.drain(wb.create_task(other,"story","写下一集")["runId"])
    assert not env.codex.calls[-1].images
    with pytest.raises(Rejected,match="原图输入"):
        wb.create_task(eid,"story","测试不可读图执行器",executor={"create":{"provider":"antigravity"}})
