"""Slice 015 — authenticated browser sub-agent e2e tests.

Exercises the full dispatch path: goal with op:// refs → executor resolves →
fake sub-agent returns content that reflects credentials → redactor scrubs
before the outcome leaves the executor boundary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from steward.browser.subagent import create_fake_browser_sub_agent
from steward.rules import CredentialScopeDecl, ReversibilityDecl
from tests.conftest import empty_rules


@dataclass
class CountingResolver:
    """Tracks resolve() calls so tests can assert the sub-agent was never called
    when the vault check should short-circuit."""

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


class CountingBrowser:
    """Wraps FakeBrowserSubAgent to count dispatch/verify calls."""

    def __init__(self, inner):
        self.inner = inner
        self.dispatch_calls: list[dict] = []
        self.verify_calls: list[str] = []

    async def dispatch(self, instruction):
        self.dispatch_calls.append(instruction)
        return await self.inner.dispatch(instruction)

    async def verify(self, url):
        self.verify_calls.append(url)
        return await self.inner.verify(url)


def authenticated_rules() -> object:
    rules = empty_rules()
    rules.reversibility = [ReversibilityDecl(action="browser_authenticated_read", reversible=True)]
    rules.credential_scopes = [
        CredentialScopeDecl(
            action="browser_authenticated_read",
            refs=["op://vault/bank/username", "op://vault/bank/password"],
        )
    ]
    return rules


def _plan_fn(resolver_refs=None):
    refs = resolver_refs or ("op://vault/bank/username", "op://vault/bank/password")

    async def plan_fn(_input):
        return {
            "id": "g-m1",
            "title": "Read account balance",
            "reason": "Need current balance to reconcile",
            "messageId": "m1",
            "transport": "browser",
            "action": "browser_authenticated_read",
            "usernameRef": refs[0],
            "passwordRef": refs[1],
            "loginUrl": "https://bank.example/login",
            "targetUrl": "https://bank.example/account",
            "usernameSelector": "#email",
            "passwordSelector": "#pass",
            "submitSelector": "button[type=submit]",
        }

    return plan_fn


async def test_authenticated_read_happy_path_redacts_reflected_creds(make_server):
    resolver = CountingResolver(
        values={
            "op://vault/bank/username": "alice@example.com",
            "op://vault/bank/password": "SuperSecret99",
        }
    )
    browser = CountingBrowser(create_fake_browser_sub_agent({
        "https://bank.example/account": {
            "title": "Account Balance",
            "text": "Balance: £1,234.56",
            "reflects_credentials": True,
        }
    }))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "bank@example.com", "subject": "Balance ready",
                   "body": "", "unread": True}],
        rules=authenticated_rules(),
        plan=_plan_fn(),
        browser_sub_agent=browser,
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    text = body["outcomes"][0]["textContent"]
    assert "alice@example.com" not in text
    assert "SuperSecret99" not in text
    assert text.count("[REDACTED]") >= 2


async def test_authenticated_read_redacts_page_title(make_server):
    resolver = CountingResolver(
        values={
            "op://vault/bank/username": "alice@example.com",
            "op://vault/bank/password": "SuperSecret99",
        }
    )
    browser = CountingBrowser(create_fake_browser_sub_agent({
        "https://bank.example/account": {
            "title": "Account",
            "text": "Balance ok",
            "reflects_in_title": True,
        }
    }))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=authenticated_rules(),
        plan=_plan_fn(),
        browser_sub_agent=browser,
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    title = body["outcomes"][0]["pageTitle"]
    assert "alice@example.com" not in title
    assert "SuperSecret99" not in title
    assert "[REDACTED]" in title


async def test_vault_locked_refuses_403_and_does_not_call_sub_agent(make_server):
    resolver = CountingResolver(
        values={
            "op://vault/bank/username": "alice@example.com",
            "op://vault/bank/password": "SuperSecret99",
        },
        unlocked=False,
    )
    browser = CountingBrowser(create_fake_browser_sub_agent({
        "https://bank.example/account": {"title": "x", "text": "y"},
    }))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=authenticated_rules(),
        plan=_plan_fn(),
        browser_sub_agent=browser,
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 403
        body = await r.json()
    assert body["error"] == "credential_refused"
    assert "locked" in body["reason"]
    assert browser.dispatch_calls == []
    assert browser.verify_calls == []
    entries = [json.loads(line) for line in Path(fixture.journal_path).read_text().strip().split("\n")]
    assert any(e.get("kind") == "credential_refused" for e in entries)


async def test_verify_refetches_target_url_not_login_url(make_server):
    resolver = CountingResolver(
        values={
            "op://vault/bank/username": "alice@example.com",
            "op://vault/bank/password": "SuperSecret99",
        }
    )
    browser = CountingBrowser(create_fake_browser_sub_agent({
        "https://bank.example/account": {"title": "Account", "text": "Balance ok"},
    }))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=authenticated_rules(),
        plan=_plan_fn(),
        browser_sub_agent=browser,
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    assert body["ok"] is True
    assert browser.verify_calls == ["https://bank.example/account"]
    assert "https://bank.example/login" not in browser.verify_calls


async def test_defense_in_depth_short_cred_not_scrubbed_long_is(make_server):
    # Username resolves to a 3-char value ("bob") — below MIN_CRED_LEN, must not be scrubbed.
    # Password resolves to a long value — must be scrubbed.
    resolver = CountingResolver(
        values={
            "op://vault/bank/username": "bob",
            "op://vault/bank/password": "VerySpecificTokenXYZ",
        }
    )
    browser = CountingBrowser(create_fake_browser_sub_agent({
        "https://bank.example/account": {
            "title": "Welcome bob",
            "text": "Logged in as bob — token VerySpecificTokenXYZ",
            "reflects_credentials": False,  # reflection is already in the canned text
        }
    }))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=authenticated_rules(),
        plan=_plan_fn(),
        browser_sub_agent=browser,
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    text = body["outcomes"][0]["textContent"]
    # Long cred scrubbed
    assert "VerySpecificTokenXYZ" not in text
    # Short cred survives — documents the MIN_CRED_LEN limitation
    assert "bob" in text


async def test_journal_records_refs_not_resolved_values(make_server):
    resolver = CountingResolver(
        values={
            "op://vault/bank/username": "alice@example.com",
            "op://vault/bank/password": "SuperSecret99",
        }
    )
    browser = CountingBrowser(create_fake_browser_sub_agent({
        "https://bank.example/account": {"title": "x", "text": "y"},
    }))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=authenticated_rules(),
        plan=_plan_fn(),
        browser_sub_agent=browser,
        credential_resolver=resolver,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    raw = Path(fixture.journal_path).read_text()
    # Critical: the full journal text must NEVER contain a resolved credential.
    assert "alice@example.com" not in raw
    assert "SuperSecret99" not in raw
    # But the refs themselves SHOULD be present — that's how we audit what
    # action touched which scope.
    assert "op://vault/bank/username" in raw
    assert "op://vault/bank/password" in raw
