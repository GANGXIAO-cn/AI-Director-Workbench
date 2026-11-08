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
    for invalid in (-1, True, 1.1, "30", 2**53):
        with pytest.raises(Rejected):
            create(invalid)
    quote = create(5)
    bid, digest = quote["batchId"], quote["summaryHash"]
    assert quote["summary"]["currency"] == wb.image_providers()["fake-image"]["currency"] == "USD"
    with pytest.raises(Rejected):
        wb.execute_batch_precheck(bid)
    wb.authorize_batch(bid, digest, "approve", budget_cents=8)
    assert wb.authorize_batch(bid, digest, "approve", budget_cents=8)["state"] == "authorized"
    with pytest.raises(Conflict):
        wb.authorize_batch(bid, digest, "approve", budget_cents=9)
    assert wb.batch_view(bid)["authorization"]["budget_cents"] == 8
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


def test_legacy_receipts_and_currency_change_cannot_dispatch(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    aid = wb.authorize_expert(pid, [eid], "story", {}, budget_cents=100)["authId"]
    args = (eid, "fake-image", "generate", [dict(prompt="雨景")], "64x64")
    batch = wb.create_batch(*args, budget_cents=10)
    bid = batch["batchId"]
    assert wb.expert_run(aid)["currency"] == "USD"
    env.images.currency = "CNY"
    with pytest.raises(Rejected, match="币种"):
        wb.authorize_batch(bid, batch["summaryHash"], "inconsistent")
    env.images.currency = "USD"
    wb.authorize_batch(bid, batch["summaryHash"], "original")
    view = wb.batch_view(bid)
    assert view["authorization"]["currency"] == "USD"
    with transaction(wb.db_path) as db:
        spec = dict(view["spec"])
        spec.pop("currency")
        auth = dict(view["authorization"])
        auth.pop("currency")
        db.execute("UPDATE image_batch SET spec=?,authorization=? WHERE id=?",(json.dumps(spec),json.dumps(auth),bid))
    old = wb.batch_view(bid)
    assert old["currency"] is None and not old["billingCurrent"]
    with pytest.raises(Rejected, match="币种"):
        wb.execute_batch(bid)
    wb.cancel_batch(bid)
    env.images.currency = "CNY"
    new = wb.create_batch(*args, budget_cents=8)
    with pytest.raises(Rejected, match="专家预算"):
        wb.authorize_batch(new["batchId"], new["summaryHash"], "wrong-scope")
    assert env.images.sent == 0


def test_unknown_price_requires_positive_budget_and_explicit_consent(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    env.images.price_cents = None
    _, eid = env.new_episode()
    b = wb.create_batch(eid, "fake-image", "generate", [dict(prompt="无报价")], "64x64")
    for accepted, amount in [(False, 6), ("true", 6), (True, -1), (True, 0)]:
        with pytest.raises(Rejected):
            wb.authorize_batch(b["batchId"], b["summaryHash"], "retry", accepted, amount)
    wb.authorize_batch(b["batchId"], b["summaryHash"], "retry", True, 6)
    receipt = wb.batch_view(b["batchId"])
    assert receipt["authorization"]["acceptUnknownCost"] and receipt["estimate_cents"] is None
    assert receipt["requests"][0]["submitted_at"] is None and env.images.sent == 0
