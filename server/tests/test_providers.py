import json

import pytest

from helpers import Env
from workbench import providers
from workbench.db import connect
from workbench.errors import Conflict, Rejected


@pytest.mark.parametrize("address,auth", [
    ("https://user:password@api.example/v1", "bearer"), ("http://example.com/v1", "bearer"),
    ("https://example.com/v1", "local"), ("http://127.0.0.1/?key=bad", "local"),
    ("file:///v1", "local"), ("http://localhost:bad", "local"), ("http://evil.test\\@localhost/v1", "local"),
])
def test_invalid_provider_address(address, auth):
    with pytest.raises(Rejected):
        providers.validate_address(address, auth)


def test_catalog_keys_status_and_config_binding(tmp_path, monkeypatch):
    wb = Env(tmp_path).wb
    addr = "https://api.example/v1"
    cfg = dict(name="样本", address=addr, auth="bearer", command_id="first")
    pid = wb.save_provider(**cfg)["id"]
    assert wb.save_provider(**cfg)["id"] == pid
    [p] = wb.api_providers()
    assert not p["keyConfigured"] and p["checkedAt"] is None
    assert wb.read_provider_models(pid, 1)["diagnostic"] == "密钥未配置"
    key = "opaque-fixture-credential"
    monkeypatch.setenv(p["envName"], key)
    assert wb.api_providers()[0]["keyConfigured"]
    calls = []
    def directory(url, token, all_pages):
        calls.append((url, token, all_pages))
        return ["media", "other"]
    monkeypatch.setattr(providers, "catalog_ids", directory)
    wb.read_provider_models(pid, 1)
    assert not wb.api_providers()[0]["models"]
    wb.read_provider_models(pid, 1, sync=True)
    assert calls == [(addr, key, False), (addr, key, True)]
    wb.label_provider_model(pid, 1, "media", ["vision","image"], [])
    with pytest.raises(Conflict):
        wb.label_provider_model(pid, 1, "media", [], [])
    def down(*args):
        raise Rejected("模型目录返回 HTTP 429")
    monkeypatch.setattr(providers, "catalog_ids", down)
    assert not wb.read_provider_models(pid, 1, sync=True)["ok"]
    assert wb.api_providers()[0]["models"][0]["categories"] == ["image","vision"]
    wb.set_provider_key(pid, 1, "session-only-fixture")
    wb.save_provider("改名", addr+"/new", "bearer", pid, 2)
    [new] = wb.api_providers()
    assert new["envName"] != p["envName"] and not new["keyConfigured"] and new["models"] == []
    with pytest.raises(Conflict):
        wb.set_provider_key(pid, 2, key)
    with connect(wb.db_path) as db:
        db_text = "\n".join(db.iterdump())
    for secret in (key, "session-only-fixture"):
        assert secret not in json.dumps(new) and secret not in db_text
    assert wb.credentials.get(p["envName"]) is None


def test_inflight_sync_does_not_replace_reconfigured_catalog(tmp_path, monkeypatch):
    wb = Env(tmp_path).wb
    pid = wb.save_provider("样本", "http://localhost/v1", "local")["id"]
    def change(*args):
        wb.save_provider("样本", "http://localhost/v2", "local", pid, 1)
        return ["stale"]
    monkeypatch.setattr(providers, "catalog_ids", change)
    with pytest.raises(Conflict):
        wb.read_provider_models(pid, 1, True)
    assert wb.api_providers()[0]["models"] == []
