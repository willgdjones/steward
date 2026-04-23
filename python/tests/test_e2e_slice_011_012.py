"""Slices 011 (irreversibility halts + draft_reply) + 012 (send_draft + credential gating)."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from steward.planner import Goal
from steward.rules import CredentialScopeDecl, ReversibilityDecl, load_rules
from tests.conftest import empty_rules


def rules_with(**kw):
    r = empty_rules()
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# -------- Slice 011 --------


async def test_halts_on_irreversible(make_server, tmp_path):
    async def plan_fn(_):
        return Goal(
            id="g-m1", title="Send reply to alice", reason="Reply needed",
            messageId="m1", transport="gmail", action="send_email",
        )

    rules = rules_with(reversibility=[
        ReversibilityDecl(action="send_email", reversible=False),
        ReversibilityDecl(action="archive", reversible=True),
    ])
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules,
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["halted"] is True
    assert "reapproval-" in body["reApprovalId"]
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 1
    assert "irreversible" in q["cards"][0]["title"]
    lines = Path(fixture.journal_path).read_text().strip().split("\n")
    halt = json.loads(lines[-1])
    assert halt["kind"] == "halt"
    assert halt["action"] == "send_email"


async def test_re_approval_card_shows_original_details(make_server):
    async def plan_fn(_):
        return Goal(
            id="g-m1", title="Send reply to alice", reason="Urgent reply needed",
            messageId="m1", transport="gmail", action="send_email",
        )

    rules = rules_with(reversibility=[ReversibilityDecl(action="send_email", reversible=False)])
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules,
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        re_goal = await r.json()
    assert "Send reply to alice" in re_goal["title"]
    assert "irreversible" in re_goal["reason"]
    assert "Urgent reply needed" in re_goal["reason"]
    assert re_goal["action"] == "send_email"


async def test_reversible_dispatches_without_halting(make_server):
    async def plan_fn(_):
        return Goal(
            id="g-m1", title="Archive newsletter", reason="Low priority",
            messageId="m1", transport="gmail", action="archive",
        )

    rules = rules_with(reversibility=[
        ReversibilityDecl(action="archive", reversible=True),
        ReversibilityDecl(action="send_email", reversible=False),
    ])
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules,
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    assert body["ok"] is True
    assert "halted" not in body
    assert fixture.gmail.get_by_id("m1")["archived"] is True


async def test_draft_reply_happy_path(make_server):
    async def plan_fn(_):
        goal = Goal(
            id="g-m1", title="Draft reply to alice", reason="Needs a response",
            messageId="m1", transport="gmail", action="draft_reply",
        )
        d = goal.to_dict()
        d["draftBody"] = "Thanks for your email!"
        # Hack: return dict instead of Goal
        return type("G", (), {"to_dict": lambda self: d})()

    rules = rules_with(reversibility=[ReversibilityDecl(action="draft_reply", reversible=True)])
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules,
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    assert body["ok"] is True
    assert body["outcomes"][0]["success"] is True
    assert body["outcomes"][0]["draftId"] is not None
    assert body["verification"]["verified"] is True
    drafts = fixture.gmail.list_drafts()
    assert len(drafts) == 1
    assert drafts[0]["to"] == "alice@example.com"
    assert drafts[0]["body"] == "Thanks for your email!"
    lines = Path(fixture.journal_path).read_text().strip().split("\n")
    action = json.loads(lines[-1])
    assert action["kind"] == "action"
    assert action["verification"]["verified"] is True


async def test_rejecting_reapproval_clears_queue(make_server):
    async def plan_fn(_):
        return Goal(
            id="g-m1", title="Send email to alice", reason="Reply needed",
            messageId="m1", transport="gmail", action="send_email",
        )

    rules = rules_with(reversibility=[ReversibilityDecl(action="send_email", reversible=False)])
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules,
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    re_id = body["reApprovalId"]
    async with fixture.client.post(f"/card/{re_id}/decision", json={"decision": "reject"}) as r:
        assert r.status == 200
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 0


def test_reversibility_parsed(tmp_path):
    (tmp_path / "principles.md").write_text(
        "reversibility:\n  - action: archive\n    reversible: true\n"
        "  - action: send_email\n    reversible: false\n"
        "  - action: draft_reply\n    reversible: true\n"
    )
    rules = load_rules(tmp_path)
    assert rules.reversibility == [
        ReversibilityDecl(action="archive", reversible=True),
        ReversibilityDecl(action="send_email", reversible=False),
        ReversibilityDecl(action="draft_reply", reversible=True),
    ]


# -------- Slice 012 --------


@dataclass
class FakeResolver:
    _resolve: Callable
    _unlocked: bool

    def resolve(self, ref):
        return self._resolve(ref)

    def is_unlocked(self):
        return self._unlocked


async def test_send_draft_halts_then_dispatches(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules_with(
            reversibility=[ReversibilityDecl(action="send_draft", reversible=False)],
            credential_scopes=[],
        ),
    )
    draft = fixture.gmail.create_draft("m1", "My reply")
    draft_id = draft["id"]

    async def plan_fn(_):
        d = {
            "id": "g-m1", "title": "Send reply to alice", "reason": "Reply to hello",
            "messageId": "m1", "transport": "gmail", "action": "send_draft",
            "draftId": draft_id,
        }
        return type("G", (), {"to_dict": lambda self: d})()

    # Re-init with the plan_fn
    fixture.deps.plan = plan_fn

    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    assert halt["halted"] is True
    async with fixture.client.get("/card") as r:
        re_goal = await r.json()
    assert "Send reply to alice" in re_goal["title"]
    assert "irreversible" in re_goal["reason"]
    async with fixture.client.post(f"/card/{re_goal['id']}/decision", json={"decision": "approve"}) as r:
        send = await r.json()
    assert send["ok"] is True
    assert send["outcomes"][0]["success"] is True
    assert send["verification"]["verified"] is True
    assert fixture.gmail.get_draft(draft_id)["sent"] is True


async def test_credential_refused_when_locked(make_server):
    def raise_locked(_):
        raise RuntimeError("vault locked")

    resolver = FakeResolver(_resolve=raise_locked, _unlocked=False)
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules_with(
            reversibility=[ReversibilityDecl(action="send_draft", reversible=False)],
            credential_scopes=[CredentialScopeDecl(action="send_draft", refs=["op://vault/gmail/token"])],
        ),
        credential_resolver=resolver,
    )
    draft = fixture.gmail.create_draft("m1", "My reply")

    async def plan_fn(_):
        d = {
            "id": "g-m1", "title": "Send reply", "reason": "Reply needed",
            "messageId": "m1", "transport": "gmail", "action": "send_draft",
            "draftId": draft["id"],
        }
        return type("G", (), {"to_dict": lambda self: d})()

    fixture.deps.plan = plan_fn

    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    async with fixture.client.post(f"/card/{halt['reApprovalId']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 403
        body = await r.json()
    assert body["error"] == "credential_refused"
    assert "locked" in body["reason"]
    lines = Path(fixture.journal_path).read_text().strip().split("\n")
    refused = json.loads(lines[-1])
    assert refused["kind"] == "credential_refused"


async def test_credential_allowed_when_unlocked(make_server):
    resolver = FakeResolver(_resolve=lambda _: "fake-token", _unlocked=True)
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules_with(
            reversibility=[ReversibilityDecl(action="send_draft", reversible=False)],
            credential_scopes=[CredentialScopeDecl(action="send_draft", refs=["op://vault/gmail/token"])],
        ),
        credential_resolver=resolver,
    )
    draft = fixture.gmail.create_draft("m1", "My reply")

    async def plan_fn(_):
        d = {
            "id": "g-m1", "title": "Send reply", "reason": "Reply",
            "messageId": "m1", "transport": "gmail", "action": "send_draft",
            "draftId": draft["id"],
        }
        return type("G", (), {"to_dict": lambda self: d})()

    fixture.deps.plan = plan_fn

    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    async with fixture.client.post(f"/card/{halt['reApprovalId']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["verification"]["verified"] is True
    assert fixture.gmail.get_draft(draft["id"])["sent"] is True


async def test_no_scope_passes_without_resolver(make_server):
    async def plan_fn(_):
        return Goal(
            id="g-m1", title="Archive message", reason="Low priority",
            messageId="m1", transport="gmail", action="archive",
        )

    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules_with(
            reversibility=[ReversibilityDecl(action="archive", reversible=True)],
            credential_scopes=[CredentialScopeDecl(action="send_draft", refs=["op://vault/gmail/token"])],
        ),
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
    assert fixture.gmail.get_by_id("m1")["archived"] is True


def test_credential_scopes_parsed(tmp_path):
    (tmp_path / "principles.md").write_text(
        "credential_scopes:\n  - action: send_draft\n    refs:\n"
        "      - op://vault/gmail/refresh_token\n"
        "      - op://vault/gmail/client_secret\n"
    )
    rules = load_rules(tmp_path)
    assert rules.credential_scopes == [
        CredentialScopeDecl(
            action="send_draft",
            refs=["op://vault/gmail/refresh_token", "op://vault/gmail/client_secret"],
        )
    ]
