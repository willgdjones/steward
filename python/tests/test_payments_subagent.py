"""Tests for the payments sub-agent (dispatch + verify)."""
from __future__ import annotations

from steward.payments.fake import FakePaymentProvider
from steward.payments.subagent import (
    CHARGE_CAPABILITY,
    create_fake_payments_sub_agent,
)


async def test_charge_happy_path():
    agent = create_fake_payments_sub_agent()
    outcome = await agent.dispatch({
        "capability": CHARGE_CAPABILITY,
        "amount_pence": 5000,
        "currency": "GBP",
        "payee": "Waitrose",
        "card_ref": "op://vault/cards/shopping",
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == "charge"
    assert outcome["amount_pence"] == 5000
    assert outcome["currency"] == "GBP"
    assert outcome["payee"] == "Waitrose"
    assert outcome["charge_id"].startswith("ch_")


async def test_charge_verifies_amount_and_payee():
    provider = FakePaymentProvider()
    agent = create_fake_payments_sub_agent(provider)
    outcome = await agent.dispatch({
        "capability": CHARGE_CAPABILITY,
        "amount_pence": 5000,
        "currency": "GBP",
        "payee": "Waitrose",
        "card_ref": "op://vault/cards/shopping",
    })
    v = await agent.verify(outcome["charge_id"], 5000, "Waitrose")
    assert v["verified"] is True
    assert v["actual_amount_pence"] == 5000
    assert v["actual_payee"] == "Waitrose"


async def test_verify_detects_amount_mismatch():
    provider = FakePaymentProvider()
    agent = create_fake_payments_sub_agent(provider)
    outcome = await agent.dispatch({
        "capability": CHARGE_CAPABILITY,
        "amount_pence": 5000,
        "currency": "GBP",
        "payee": "W",
        "card_ref": "op://v/c",
    })
    v = await agent.verify(outcome["charge_id"], 9999, "W")
    assert v["verified"] is False


async def test_verify_detects_payee_mismatch():
    provider = FakePaymentProvider()
    agent = create_fake_payments_sub_agent(provider)
    outcome = await agent.dispatch({
        "capability": CHARGE_CAPABILITY,
        "amount_pence": 100,
        "currency": "GBP",
        "payee": "GoodCo",
        "card_ref": "op://v/c",
    })
    v = await agent.verify(outcome["charge_id"], 100, "BadCo")
    assert v["verified"] is False


async def test_verify_not_found():
    agent = create_fake_payments_sub_agent()
    v = await agent.verify("ch_nonexistent", 100, "X")
    assert v["verified"] is False
    assert v["actual_state"] == "not_found"


async def test_unknown_capability_rejected():
    agent = create_fake_payments_sub_agent()
    outcome = await agent.dispatch({"capability": "refund", "amount_pence": 100})
    assert outcome["success"] is False
    assert "unknown capability" in outcome["error"]


async def test_rejects_negative_amount():
    agent = create_fake_payments_sub_agent()
    outcome = await agent.dispatch({
        "capability": CHARGE_CAPABILITY,
        "amount_pence": -100,
        "currency": "GBP",
        "payee": "X",
        "card_ref": "op://v/c",
    })
    assert outcome["success"] is False
    assert "invalid amount" in outcome["error"]


async def test_rejects_missing_required_fields():
    agent = create_fake_payments_sub_agent()
    outcome = await agent.dispatch({
        "capability": CHARGE_CAPABILITY,
        "amount_pence": 100,
        # missing currency, payee, card_ref
    })
    assert outcome["success"] is False
    assert "missing required field" in outcome["error"]


async def test_issuer_failure_returned_as_error():
    provider = FakePaymentProvider()
    provider.next_charge_raises = RuntimeError("card declined")
    agent = create_fake_payments_sub_agent(provider)
    outcome = await agent.dispatch({
        "capability": CHARGE_CAPABILITY,
        "amount_pence": 100,
        "currency": "GBP",
        "payee": "X",
        "card_ref": "op://v/c",
    })
    assert outcome["success"] is False
    assert "card declined" in outcome["error"]
