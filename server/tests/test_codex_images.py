"""Native transport and quota receipts; fixture process only, no model calls."""
import json
from pathlib import Path

import pytest

from helpers import Env
from workbench.codeximages import CodexImageProvider
from workbench.errors import Rejected
from workbench.images import FakeImageProvider


def test_native_rpc_uses_exact_model_and_recovers_same_request(tmp_path, monkeypatch):
    import sys
    profile = tmp_path / "login"
    profile.mkdir()
    (profile / "auth.json").write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(profile))
    binary = tmp_path / "codex-fixture"
    log = tmp_path / "rpc.jsonl"
    binary.write_text(f'''#!{sys.executable}
import json,sys,base64,zlib,struct
def chunk(t,b):return struct.pack('>I',len(b))+t+b+struct.pack('>I',zlib.crc32(t+b)&0xffffffff)
image=b'\\x89PNG\\r\\n\\x1a\\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1024,1536,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\\0'+b'\\x20\\x40\\x60'*1024)*1536))+chunk(b'IEND',b'')
def send(x):print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line)
 with open({str(log)!r},'a') as f:f.write(json.dumps(m)+'\\n')
 if m.get('method')=='initialize':send(dict(id=1,result={{}}))
 if m.get('method')=='thread/start':send(dict(id=2,result=dict(thread=dict(id='fixture-thread'),model=m['params']['model'])))
 if m.get('method')=='turn/start':
  send(dict(id=3,result=dict(turn=dict(id='fixture-turn'))))
  send(dict(method='item/completed',params=dict(item=dict(id='fixture-image',type='imageGeneration',status='completed',result=base64.b64encode(image).decode()))))
  send(dict(method='turn/completed',params=dict(turn=dict(id='fixture-turn',status='completed'))))
''')
    binary.chmod(0o700)
    provider = CodexImageProvider(tmp_path / "jobs", str(binary), timeout=5)
    request = dict(local_request_id="fixture-1",model="gpt-6-astra",operation="generate",prompt="离线协议夹具",size="1024x1536",inputs=[])
    result = provider.submit(request)
    assert result.state == "succeeded" and result.data.startswith(b"\x89PNG")
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    start = next(m["params"] for m in messages if m.get("method")=="turn/start")
    assert start["model"] == request["model"] and "effort" not in start
    receipt = json.loads((tmp_path / "jobs/fixture-1/receipt.json").read_text())
    assert receipt["nativeTool"] == "imageGeneration" and receipt["threadId"] == "fixture-thread"
    before = log.read_bytes()
    assert provider.submit(request).data == result.data
    assert log.read_bytes() == before
    assert provider.submit(dict(request,model="gpt-6.1-sol")).state == "failed"
    assert log.read_bytes() == before


def test_codex_usage_confirmation_is_separate_from_money_budget(tmp_path):
    env = Env(tmp_path)
    _, eid = env.new_episode()
    provider = FakeImageProvider("codex-native-fixture")
    provider.currency = "Codex额度"
    provider.price_cents = None
    provider.billing_mode = "subscription_usage"
    provider.capabilities = dict(provider.capabilities, models=["gpt-6.1-sol"])
    env.wb.image_providers_map[provider.name] = provider
    batch = env.wb.create_batch(eid,provider.name,"generate",[dict(prompt="离线额度回执",inputs=[],target=dict(name="夹具",category="identity"))],"64x64",model="gpt-6.1-sol")
    view = env.wb.batch_view(batch["batchId"])
    assert view["cost_state"] == "subscription_usage" and view["estimate_cents"] is None
    env.wb.authorize_batch(batch["batchId"],batch["summaryHash"],"confirm")
    assert env.wb.execute_batch(batch["batchId"])["state"] == "completed"
    auth = env.wb.batch_view(batch["batchId"])["authorization"]
    assert auth["costPolicy"] == "not_gated" and auth["budget_cents"] is None
