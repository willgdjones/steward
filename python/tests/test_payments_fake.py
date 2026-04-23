"""Tests for the fake payment provider."""
from __future__ import annotations

import pytest

from steward.payments.fake import FakePaymentProvider


def test_charge_records_record():
    p = FakePaymentProvider()
    r = p.charge(amount_pence=5000, currency="GBP", payee="Waitrose", card_ref="op://vault/cards/shopping")
    assert r["amount_pence"] == 5000
    assert r["currency"] == "GBP"
    assert r["payee"] == "Waitrose"
    assert r["card_ref"] == "op://vault/cards/shopping"
    assert r["status"] == "succeeded"
    assert r["id"].startswith("ch_")
    assert p.charges == [r]


def test_get_charge_round_trip():
    p = FakePaymentProvider()
    r = p.charge(amount_pence=100, currency="GBP", payee="X", card_ref="op://v/c/x")
    assert p.get_charge(r["id"]) == r
    assert p.get_charge("ch_nonexistent") is None


def test_idempotency_key_dedupes():
    p = FakePaymentProvider()
    first = p.charge(amount_pence=5000, currency="GBP", payee="W", card_ref="ref", idempotency_key="abc")
    second = p.charge(amount_pence=5000, currency="GBP", payee="W", card_ref="ref", idempotency_key="abc")
    assert first["id"] == second["id"]
    assert len(p.charges) == 1


def test_without_idempotency_each_charge_is_fresh():
    p = FakePaymentProvider()
    a = p.charge(amount_pence=100, currency="GBP", payee="W", card_ref="ref")
    b = p.charge(amount_pence=100, currency="GBP", payee="W", card_ref="ref")
    assert a["id"] != b["id"]
    assert len(p.charges) == 2


def test_issuer_failure_propagates():
    p = FakePaymentProvider()
    p.next_charge_raises = RuntimeError("issuer declined")
    with pytest.raises(RuntimeError, match="issuer declined"):
        p.charge(amount_pence=100, currency="GBP", payee="W", card_ref="ref")
    # Subsequent charges succeed again
    r = p.charge(amount_pence=100, currency="GBP", payee="W", card_ref="ref")
    assert r["status"] == "succeeded"
