import base64
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import httpx
import pytest

from helpers import Env
from workbench.apiimages import OpenAIImageAPIProvider
from workbench.errors import Rejected
from workbench.images import png_bytes


def provider(tmp_path, handler):
    credential = tmp_path / "credentials.env"
    credential.write_text('OPENAI_API_KEY="test-secret"\nOPENAI_PROJECT_ID=test-project\n')
    return OpenAIImageAPIProvider(tmp_path / "api", credential, httpx.MockTransport(handler))


def request(**changes):
    return dict(local_request_id="request-1", operation="generate", model="gpt-image-2.5-flare",
                prompt="橘猫", size="64x64", quality="low", inputs=[], **changes)


def image_response():
    return httpx.Response(200, headers={"x-request-id":"remote-id"},
                          json={"data":[{"b64_json":base64.b64encode(png_bytes(64,64)).decode()}]})


def test_direct_generate_edit_and_receipt(tmp_path):
    calls=[]
    def handle(req):
        calls.append(req)
        assert req.headers["authorization"] == "Bearer test-secret"
        assert req.headers["openai-project"] == "test-project"
        return image_response()
    p=provider(tmp_path,handle)
    req=request()
    assert p.submit(req).state == "succeeded"
    assert p.submit(req).state == "succeeded" and len(calls)==1
    assert json.loads(calls[0].content)["model"] == req["model"]
    assert calls[0].url.path.endswith("/generations")
    reference=tmp_path / "hash-without-extension"
    reference.write_bytes(png_bytes(64,64))
    req.update(local_request_id="edit-1",inputs=[str(reference)],operation="edit")
    assert p.submit(req).state == "succeeded"
    assert calls[1].url.path.endswith("/edits")
    assert b'name="image[]"; filename="reference.png"' in calls[1].content
    assert b"Content-Type: image/png" in calls[1].content
    restored=OpenAIImageAPIProvider(p.root,p.credential_path)
    assert restored.inspect("edit-1").data == png_bytes(64,64)
    assert "test-secret" not in json.dumps(p.settings())
    assert "test-secret" not in (p.root / "edit-1/receipt.json").read_text()


@pytest.mark.parametrize("status,state",[(401,"failed"),(429,"failed"),(500,"unknown")])
def test_api_errors_do_not_leak_or_repeat(tmp_path,status,state):
    calls=[]
    def handle(req):
        calls.append(req)
        return httpx.Response(status,json={"error":{"code":"bad_input","message":"invalid value test-secret"}})
    p=provider(tmp_path,handle)
    outcome=p.submit(request())
    assert outcome.state==state and "test-secret" not in outcome.error
    assert "bad_input" in outcome.error and "invalid value" in outcome.error
    assert p.inspect("request-1").error == outcome.error
    assert p.submit(request()).state==state and len(calls)==1


def test_timeout_is_retryable_under_a_new_request(tmp_path):
    calls=[]
    def handle(req):
        calls.append(req)
        raise httpx.ReadTimeout("test-secret")
    p=provider(tmp_path,handle)
    assert p.submit(request()).state=="failed"
    assert p.submit(request()).state=="failed" and len(calls)==1
    assert p.settings()["activeRequests"]==0
    receipt = p.root / "request-1" / "receipt.json"
    receipt.write_text(json.dumps(dict(state="unknown", error="API 连接中断或超时，请核对原请求")))
    assert p.inspect("request-1").state == "failed"
    receipt.write_text(json.dumps(dict(state="unknown")))
    assert p.inspect("request-1").state == "unknown"


def test_settings_persist_and_validate(tmp_path):
    p=provider(tmp_path,lambda req:image_response())
    p.save_settings(3)
    assert OpenAIImageAPIProvider(p.root,p.credential_path).max_concurrency==3
    for value in [0,-1,1.5,True,65,"2"]:
        with pytest.raises(Rejected):p.save_settings(value)


def test_batch_parallelism_and_shared_cap(tmp_path):
    env=Env(tmp_path)
    _,eid=env.new_episode()
    lock=threading.Lock()
    active=peak=sent=0
    def handle(req):
        nonlocal active,peak,sent
        with lock:
            active+=1;sent+=1;peak=max(peak,active)
        time.sleep(.06)
        with lock:active-=1
        return image_response()
    p=provider(tmp_path,handle)
    p.capabilities=dict(p.capabilities,sizes=["64x64"])
    env.wb.image_providers_map[p.name]=p
    env.wb.save_image_provider_settings(p.name,2)
    def batch(prefix):
        b=env.wb.create_batch(eid,p.name,"generate",[dict(prompt=f"{prefix}-{i}") for i in range(4)],"64x64",
                              model="gpt-image-2.5-flare")
        env.wb.authorize_batch(b["batchId"],b["summaryHash"],prefix)
        return b["batchId"]
    first=batch("single")
    assert env.wb.execute_batch(first)["state"]=="completed"
    assert peak==2 and sent==4  # Proves parallel requests within a single batch.
    bids=[batch("left"),batch("right")]
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(env.wb.execute_batch,bids))
    assert all(b["state"]=="completed" for b in results)
    assert peak==2 and sent==12  # Multiple batches still share the same limit.
    assert env.wb.image_providers()[p.name]["settings"]["maxConcurrency"]==2


def test_lowering_limit_waits_for_existing_requests(tmp_path):
    p=provider(tmp_path,lambda req:image_response())
    p.save_settings(2)
    entered=threading.Event()
    def enter():
        with p.slot():entered.set()
    with ThreadPoolExecutor(1) as pool:
        with p.slot():
            with p.slot():
                p.save_settings(1)
                future=pool.submit(enter)
                assert not entered.wait(.05)
            assert not entered.wait(.05)  # One active request still fills the reduced limit.
        future.result(timeout=2)
    assert entered.is_set() and p.settings()["activeRequests"]==0


def test_settings_http_route(tmp_path):
    from fastapi.testclient import TestClient
    from workbench.app import create_app
    env=Env(tmp_path)
    p=provider(tmp_path,lambda req:image_response())
    env.wb.image_providers_map[p.name]=p
    with TestClient(create_app(env.wb,inline_worker=True,token="test")) as client:
        result=client.post("/api/cmd/save_image_provider_settings",headers={"x-adw-token":"test"},
                           json={"provider":p.name,"max_concurrency":4})
        assert result.status_code==200 and result.json()["maxConcurrency"]==4
        channels=client.get("/api/q/image_providers").json()
        assert channels[p.name]["settings"]["maxConcurrency"]==4
        assert "test-secret" not in json.dumps(channels)


def test_missing_edit_input_is_not_sent(tmp_path):
    calls=[]
    p=provider(tmp_path,lambda req:calls.append(req))
    req=request()
    req["operation"]="edit"
    assert p.submit(req).state=="failed" and calls==[]


def test_queue_deadline_never_submits_or_claims_unknown(tmp_path,monkeypatch):
    calls=[]
    p=provider(tmp_path,lambda req:calls.append(req))
    def deadline(predicate,timeout):
        assert timeout==600
        return False
    monkeypatch.setattr(p.condition,'wait_for',deadline)
    result=p.submit(request())
    assert result.state=='failed' and '未发送' in result.error
    assert calls==[] and p.active==0
