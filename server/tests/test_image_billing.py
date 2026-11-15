import json

import pytest

from helpers import Env
from workbench.db import transaction
from workbench.errors import Conflict, Rejected


def test_budget_receipt_survives_cancellation_and_replacement(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    def create(budget):
        return wb.create_batch(eid, "fake-image", "generate", [dict(prompt="第一张")], "64x64", budget_cents=budget)
    quote = create(5)
    bid, digest = quote["batchId"], quote["summaryHash"]
    assert quote["summary"]["currency"] == wb.image_providers()["fake-image"]["currency"] == "USD"
    with pytest.raises(Rejected):
        wb.execute_batch_precheck(bid)
    wb.authorize_batch(bid, digest, "approve", budget_cents=8)
    assert wb.authorize_batch(bid, digest, "approve", budget_cents=8)["state"] == "authorized"
    with pytest.raises(Conflict):
        wb.authorize_batch(bid, digest, "approve", budget_cents=9)
    assert wb.batch_view(bid)["authorization"]["budget_cents"] is None
    assert env.images.sent == 0
    wb.cancel_batch(bid)
    with pytest.raises(Rejected):
        wb.execute_batch(bid)
    replacement = create(5)
    assert replacement["summaryHash"] != digest
    with pytest.raises(Conflict):
        create(5)
    wb.authorize_batch(replacement["batchId"], replacement["summaryHash"], "replacement")
    assert wb.execute_batch(replacement["batchId"])["requests"][0]["provider_request_id"]
    assert env.images.sent == 1


def test_legacy_receipts_and_currency_change_do_not_gate_generation(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    args = (eid, "fake-image", "generate", [dict(prompt="雨景")], "64x64")
    batch = wb.create_batch(*args, budget_cents=10)
    bid = batch["batchId"]
    assert wb.batch_view(bid)["currency"] == "USD"
    env.images.currency = "CNY"
    wb.authorize_batch(bid, batch["summaryHash"], "original")
    view = wb.batch_view(bid)
    assert view["authorization"]["currency"] == "CNY"
    with transaction(wb.db_path) as db:
        spec = dict(view["spec"])
        spec.pop("currency")
        auth = dict(view["authorization"])
        auth.pop("currency")
        db.execute("UPDATE image_batch SET spec=?,authorization=? WHERE id=?",(json.dumps(spec),json.dumps(auth),bid))
    old = wb.batch_view(bid)
    assert old["currency"] is None and old["billingCurrent"]
    assert wb.execute_batch(bid)["state"] == "completed"
    assert env.images.sent == 1


def test_unknown_price_needs_only_generation_approval(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    env.images.price_cents = None
    _, eid = env.new_episode()
    b = wb.create_batch(eid, "fake-image", "generate", [dict(prompt="无报价")], "64x64")
    wb.authorize_batch(b["batchId"], b["summaryHash"], "approve")
    receipt = wb.batch_view(b["batchId"])
    assert receipt["authorization"]["costPolicy"] == "not_gated" and receipt["estimate_cents"] is None
    assert receipt["requests"][0]["submitted_at"] is None and env.images.sent == 0


def test_bulk_approval_is_atomic_and_idempotent(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    _, eid = env.new_episode()
    batches = [wb.create_batch(eid,"fake-image","generate",[dict(prompt=str(i))],"64x64") for i in range(2)]
    selection = {b["batchId"]:b["summaryHash"] for b in batches}
    bad = dict(selection)
    bad[batches[1]["batchId"]] = "stale"
    with pytest.raises(Rejected):
        wb.authorize_batches(eid,bad,"invalid")
    assert all(wb.batch_view(b["batchId"])["state"] == "awaiting_authorization" for b in batches)
    result = wb.authorize_batches(eid,selection,"bulk")
    assert wb.authorize_batches(eid,selection,"bulk") == result
    assert env.images.sent == 0
    for bid in result["batchIds"]:
        assert wb.execute_batch(bid)["state"] == "completed"
    assert env.images.sent == 2
