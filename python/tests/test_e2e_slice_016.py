"""Slice 016 — payments capability e2e tests.

Exercises the full approve-charge flow end-to-end:
    plan → queue → approve → halt (irreversible) → re-approve → limits check →
    credential check → dispatch → verify → journal.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from steward.payments.fake import FakePaymentProvider
from steward.payments.subagent import create_fake_payments_sub_agent
from steward.rules import (
    CredentialScopeDecl,
    ReversibilityDecl,
    SpendingLimits,
    load_rules,
)
from tests.conftest import empty_rules


@dataclass
class CountingResolver:
    values: dict[str, str] = field(default_factory=dict)
    unlocked: bool = True
    resolve_calls: list[str] = field(default_factory=list)

    def resolve(self, ref: str) -> str:
        self.resolve_calls.append(ref)
        if not self.unlocked:
            raise RuntimeError("vault locked")
        if ref not in self.values:
            raise RuntimeError(f"cannot resolve {ref}")
        return self.values[ref]

    def is_unlocked(self) -> bool:
        return self.unlocked


def payments_rules(
    *,
    max_per_charge_pence: int | None = None,
    max_per_day_pence: int | None = None,
    credential_refs: list[str] | None = None,
):
    r = empty_rules()
    r.reversibility = [ReversibilityDecl(action="charge", reversible=False)]
    r.credential_scopes = [
        CredentialScopeDecl(action="charge", refs=credential_refs or ["op://vault/stripe/api_key"])
    ]
    r.spending_limits = SpendingLimits(
        max_per_charge_pence=max_per_charge_pence,
        max_per_day_pence=max_per_day_pence,
    )
    return r


def charge_plan(amount_pence: int = 5000, payee: str = "Waitrose"):
    async def plan_fn(_input):
        return {
            "id": "g-charge-1",
            "title": f"Pay {payee} £{amount_pence / 100:.2f}",
            "reason": "Weekly shop",
            "messageId": "m1",
            "transport": "payments",
            "action": "charge",
            "amount_pence": amount_pence,
            "currency": "GBP",
            "payee": payee,
            "cardRef": "op://vault/cards/shopping",
        }
    return plan_fn


async def test_charge_halts_then_dispatches_and_verifies(make_server):
    provider = FakePaymentProvider()
    resolver = CountingResolver(values={"op://vault/stripe/api_key": "sk_test"})
    fixture = await make_server(
        messages=[{"id": "m1", "from": "cart@shop.example", "subject": "Confirm your order",
                   "body": "", "unread": True}],
        rules=payments_rules(max_per_charge_pence=10000, max_per_day_pence=50000),
        plan=charge_plan(amount_pence=5000, payee="Waitrose"),
        payments_sub_agent=create_fake_payments_sub_agent(provider),
        credential_resolver=resolver,
    )

    # Initial approve → halt (charge is irreversible)
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    assert halt["halted"] is True

    # Re-approval carries the amount/payee/cardRef through
    async with fixture.client.get("/card") as r:
        re_goal = await r.json()
    assert re_goal["action"] == "charge"
    assert re_goal["amount_pence"] == 5000
    assert re_goal["payee"] == "Waitrose"

    # Approve re-approval → dispatch + verify
    async with fixture.client.post(f"/card/{re_goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["outcomes"][0]["success"] is True
    assert body["outcomes"][0]["amount_pence"] == 5000
    assert body["outcomes"][0]["payee"] == "Waitrose"
    assert body["verification"]["verified"] is True

    # Charge persisted in provider
    assert len(provider.charges) == 1
    assert provider.charges[0]["amount_pence"] == 5000


async def test_per_charge_limit_refuses_before_dispatch(make_server):
    provider = FakePaymentProvider()
    resolver = CountingResolver(values={"op://vault/stripe/api_key": "sk"})
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=payments_rules(max_per_charge_pence=2000),
        plan=charge_plan(amount_pence=5000),
        payments_sub_agent=create_fake_payments_sub_agent(provider),
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 403
        body = await r.json()
    assert body["error"] == "limit_exceeded"
    assert "per-charge" in body["reason"]
    # Nothing charged
    assert provider.charges == []
    # Nothing resolved either — limit check short-circuits before credential resolution
    assert resolver.resolve_calls == []
    # Journal records the limit-exceeded refusal
    entries = [json.loads(line) for line in Path(fixture.journal_path).read_text().strip().split("\n")]
    assert any(e.get("kind") == "limit_exceeded" for e in entries)


async def test_vault_locked_refuses_before_dispatch(make_server):
    provider = FakePaymentProvider()
    resolver = CountingResolver(values={}, unlocked=False)
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=payments_rules(max_per_charge_pence=10000),
        plan=charge_plan(amount_pence=5000),
        payments_sub_agent=create_fake_payments_sub_agent(provider),
        credential_resolver=resolver,
    )
    # Approve → halt → re-approve (limit + credential checks run on re-approval too)
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    async with fixture.client.post(f"/card/{halt['reApprovalId']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 403
        body = await r.json()
    assert body["error"] == "credential_refused"
    assert "locked" in body["reason"]
    assert provider.charges == []


async def test_per_day_limit_aggregates_from_journal(make_server):
    from steward.journal import append_journal
    provider = FakePaymentProvider()
    resolver = CountingResolver(values={"op://vault/stripe/api_key": "sk"})
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=payments_rules(max_per_day_pence=10000),
        plan=charge_plan(amount_pence=5000),
        payments_sub_agent=create_fake_payments_sub_agent(provider),
        credential_resolver=resolver,
    )
    # Seed journal with a recent successful charge that eats into the day budget
    import time
    now_ms = int(time.time() * 1000)
    append_journal(fixture.journal_path, {
        "kind": "action",
        "action": "charge",
        "goalId": "g-earlier",
        "messageId": "m-earlier",
        "outcomes": [{
            "success": True,
            "action_taken": "charge",
            "amount_pence": 8000,
            "ts_ms": now_ms - 60_000,  # 1 min ago
        }],
    })
    # New 5000 charge would push to 13000 > 10000 cap → refused
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 403
        body = await r.json()
    assert body["error"] == "limit_exceeded"
    assert "per-day" in body["reason"]


async def test_journal_records_ref_not_resolved_value(make_server):
    provider = FakePaymentProvider()
    resolver = CountingResolver(values={"op://vault/stripe/api_key": "sk_live_SENSITIVE"})
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=payments_rules(max_per_charge_pence=10000),
        plan=charge_plan(amount_pence=2500, payee="Tesco"),
        payments_sub_agent=create_fake_payments_sub_agent(provider),
        credential_resolver=resolver,
    )
    # Approve → halt → re-approve → charge
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    async with fixture.client.post(f"/card/{halt['reApprovalId']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    raw = Path(fixture.journal_path).read_text()
    assert "sk_live_SENSITIVE" not in raw
    assert "op://vault/cards/shopping" in raw  # card ref IS in journal
    # Find the charge action entry
    entries = [json.loads(line) for line in raw.strip().split("\n") if line]
    charge = next(e for e in entries if e.get("action") == "charge" and e.get("kind") == "action")
    assert charge["amount_pence"] == 2500
    assert charge["payee"] == "Tesco"
    assert charge["cardRef"] == "op://vault/cards/shopping"
    assert charge["verification"]["verified"] is True


async def test_invalid_amount_type_rejected(make_server):
    provider = FakePaymentProvider()
    resolver = CountingResolver(values={"op://vault/stripe/api_key": "sk"})

    async def bad_plan(_input):
        return {
            "id": "g-bad", "title": "pay", "reason": "r", "messageId": "m1",
            "transport": "payments", "action": "charge",
            "amount_pence": "5000",  # wrong type — string instead of int
            "currency": "GBP", "payee": "X", "cardRef": "op://v/c",
        }

    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=payments_rules(),
        plan=bad_plan,
        payments_sub_agent=create_fake_payments_sub_agent(provider),
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 400
        body = await r.json()
    assert body["error"] == "invalid_charge"


def test_spending_limits_parsed_from_principles(tmp_path):
    (tmp_path / "principles.md").write_text(
        "spending_limits:\n"
        "  max_per_charge_pence: 5000\n"
        "  max_per_day_pence: 20000\n"
        "  max_per_week_pence: 100000\n"
    )
    rules = load_rules(tmp_path)
    assert rules.spending_limits.max_per_charge_pence == 5000
    assert rules.spending_limits.max_per_day_pence == 20000
    assert rules.spending_limits.max_per_week_pence == 100000


def test_spending_limits_defaults_none(tmp_path):
    (tmp_path / "principles.md").write_text("blacklist: []\n")
    rules = load_rules(tmp_path)
    assert rules.spending_limits.max_per_charge_pence is None
    assert rules.spending_limits.max_per_day_pence is None
    assert rules.spending_limits.max_per_week_pence is None
