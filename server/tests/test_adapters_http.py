"""Adapter parsing (fake executables / recorded MCP shape) and the generic HTTP command layer."""
import json
import os
from pathlib import Path
import stat

from fastapi.testclient import TestClient

from helpers import Env
from workbench.app import create_app
from workbench.contracts import branch_schema
from workbench.executors.base import ExecRequest
from workbench.executors.cli import AntigravityExecutor
from workbench.images import OpenAIImageMCPProvider


def test_antigravity_adapter_parses_success_receipt(tmp_path):
    fake = tmp_path / "agy"
    fake.write_text("#!/bin/sh\n"
                    "echo '{\"status\":\"SUCCESS\",\"model\":\"gemini-3.8-flash-high\",\"structured_output\":"
                    "{\"schemaVersion\":1,\"kind\":\"draft\",\"runId\":\"r1\",\"inputFingerprint\":\"" + "a" * 64 + "\","
                    "\"baseVersionId\":null,\"changes\":[{\"targetId\":\"t\",\"field\":\"synopsis\",\"value\":\"v\"}]}}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "w"
    work.mkdir()
    out = AntigravityExecutor(str(fake)).start(ExecRequest(run_id="r1", purpose="create", kind="draft", prompt="p",
                                                          schema=branch_schema("draft"), session_mode="new", workdir=work,
                                                          model="gemini-3.8-flash-high"))
    assert out.ok and out.structured["runId"] == "r1" and out.reported_model == "gemini-3.8-flash-high"
    assert AntigravityExecutor.isolation == "unverified"


def test_antigravity_adapter_classifies_quota(tmp_path):
    fake = tmp_path / "agy"
    fake.write_text("#!/bin/sh\necho '{\"status\":\"ERROR\",\"error\":\"Quota exceeded, resets at 21:00\"}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "w"
    work.mkdir()
    out = AntigravityExecutor(str(fake)).start(ExecRequest(run_id="r2", purpose="create", kind="draft", prompt="p",
                                                          schema={}, session_mode="new", workdir=work))
    assert not out.ok and out.error_class == "quota" and out.quota_resets_at


def test_image_mcp_parses_recorded_job_status(monkeypatch):
    """Shape recorded from the local openai-image-mcp server on 2026-11-07 (job adw-phase0-img-001)."""
    p = OpenAIImageMCPProvider(root=Path("/nonexistent"))
    recorded = {"request_id": "x", "status": "completed", "api_request_id": "req_1", "image_path": __file__}
    monkeypatch.setattr(p, "_call", lambda tool, args: recorded)
    out = p.inspect("x")
    assert out.state == "succeeded" and out.provider_request_id == "req_1" and out.data == Path(__file__).read_bytes()
    monkeypatch.setattr(p, "_call", lambda tool, args: {"status": "running", "api_request_id": "req_1"})
    assert p.inspect("x").state == "unknown"


def test_http_generic_commands_and_queries(tmp_path):
    env = Env(tmp_path)
    client = TestClient(create_app(env.wb, inline_worker=True, token="t"))
    h = {"x-adw-token": "t"}
    assert client.post("/api/cmd/create_project", json={"config_id": "_template", "name": "夜班柴犬"}).status_code == 403
    pid = client.post("/api/cmd/create_project", json={"config_id": "_template", "name": "夜班柴犬", "command_id": "c1"},
                      headers=h).json()["projectId"]
    assert client.post("/api/cmd/delete_everything", json={}, headers=h).status_code == 404     # not whitelisted
    for name, body in [("create_episode",{"project_id":pid}),
                       ("execute_batch",{"batch_id":"example","unexpected":True})]:
        invalid = client.post("/api/cmd/"+name,json=body,headers=h)
        assert invalid.status_code == 422 and "调用参数无效" in invalid.json()["error"]
    for raw in ("[]", "{broken"):
        assert client.get("/api/q/list_projects",params={"args":raw}).status_code == 422
    eid = client.post("/api/cmd/create_episode", json={"project_id": pid, "display_code": "E01"}, headers=h).json()["episodeId"]
    ok = client.post("/api/cmd/record_leftovers", json={"episode_id": eid, "facts": ["鹦鹉"], "expected_revision": 1,
                                                        "command_id": "ui-always-sends-one"}, headers=h)
    assert ok.status_code == 200      # regression: UI-added command_id used to crash commands that don't take one
    r = client.post("/api/cmd/start_episode", json={"episode_id": eid, "source_type": "idea", "idea_text": "柴犬值夜班",
                                                     "executor_plan": {"create": {"provider": "codex"}, "review": {"provider": "claude"}},
                                                     "relation": "continue", "predecessor_ids": []}, headers=h)
    assert r.status_code == 422 and r.json()["error"]
    client.post("/api/cmd/start_episode", json={"episode_id": eid, "source_type": "idea", "idea_text": "柴犬值夜班",
                                                "executor_plan": {"create": {"provider": "codex"}, "review": {"provider": "claude"}}},
                headers=h).raise_for_status()
    t = client.post("/api/cmd/create_task", json={"episode_id": eid, "stage": "story", "goal": "写"}, headers=h).json()
    view = client.get("/api/q/episode_view", params={"args": json.dumps({"episode_id": eid})}).json()
    assert view["artifacts"][0]["versions"] and t["runId"]
    ov = client.get("/api/q/project_overview", params={"args": json.dumps({"project_id": pid})}).json()
    assert ov["episodes"][0]["label"] == "开发中"
    preview = client.get("/api/q/adoption_preview", params={"args": json.dumps({"version_id": view["artifacts"][0]["versions"][0]["id"]})})
    assert preview.status_code == 200
    bad = client.post("/api/cmd/deliver", json={"episode_id": eid, "scope": "full"}, headers=h)
    assert bad.status_code == 422 and isinstance(bad.json()["detail"], list)                 # 结构阻断逐条列出


def test_paid_tools_receive_authorized_inputs_over_one_session(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from workbench.images import png_bytes

    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    base = env.image(pid, eid, name="基础图", grid=True)
    ref = env.image(pid, eid, name="参考图", rgb=(70, 80, 90))
    provider = OpenAIImageMCPProvider(root=tmp_path / "mcp", poll_seconds=0)
    wb.image_providers_map[provider.name] = provider
    output = tmp_path / "result.png"
    output.write_bytes(png_bytes(864, 1536))
    sessions = []

    @contextmanager
    def session():
        calls = []
        sessions.append(calls)

        def call(tool, args):
            calls.append((tool, args))
            if len(calls) == 1:
                return {"status": "queued"}
            if len(calls) == 2:
                return {"status": "running"}
            return {"status": "completed", "image_path": str(output)}
        yield call

    monkeypatch.setattr(provider, "_session", session)
    for operation, inputs in [("generate", []), ("generate", [ref["versionId"]]),
                              ("edit", [base["versionId"], ref["versionId"]])]:
        if operation == "edit":
            b = wb.redraw_cells(eid, inputs[0], [5], inputs[1:], "借表情", provider.name, "864x1536",
                                budget_cents=200, quality="low")
        else:
            b = wb.create_batch(eid, provider.name, operation, [dict(prompt="绘制", inputs=inputs)],
                                "864x1536", quality="low", budget_cents=200)
        assert b["summary"]["quality"] == "low"
        wb.authorize_batch(b["batchId"], b["summaryHash"], "confirm-" + b["batchId"], accept_unknown_cost=True)
        done = wb.execute_batch(b["batchId"])
        assert done["state"] == "completed"
        calls = sessions[-1]
        assert [tool for tool, args in calls] == ["edit_image" if inputs else "generate_image",
                                                  "image_job_status", "image_job_status"]
        args = calls[0][1]
        assert {entry["request_id"] for _, entry in calls} == {done["requests"][0]["local_request_id"]}
        assert args["size"] == "864x1536" and args["quality"] == "low"
        assert [Path(path).read_bytes() for path in args.get("image_paths", [])] == [wb.asset_bytes(i) for i in inputs]
        if operation == "edit":
            assert "第 5 格" in args["prompt"]
    assert len(sessions) == 3
