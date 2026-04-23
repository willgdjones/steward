"""Slices 013 (TUI + WebSocket) + 014 (browser sub-agent read-only)."""
import asyncio
import json
from pathlib import Path

import aiohttp

from steward.browser.subagent import create_fake_browser_sub_agent
from steward.planner import Goal
from steward.rules import ReversibilityDecl
from tests.conftest import empty_rules


# -------- Slice 013 --------


def ws_url(fixture) -> str:
    base = fixture.url.replace("http://", "ws://").replace("https://", "wss://")
    return base + "/ws"


async def test_websocket_receives_initial_queue_state(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
    )
    async with fixture.client.post("/refill") as r:
        await r.json()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url(fixture)) as ws:
            raw = await ws.receive(timeout=2.0)
            parsed = json.loads(raw.data)
    assert parsed["type"] == "queue_update"
    assert parsed["depth"] == 1
    assert len(parsed["cards"]) == 1


async def test_websocket_live_update_on_decision(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url(fixture)) as ws:
            initial = await ws.receive(timeout=2.0)
            async with fixture.client.post(
                f"/card/{goal['id']}/decision", json={"decision": "reject"}
            ) as r:
                await r.json()
            update = await ws.receive(timeout=2.0)
    initial_data = json.loads(initial.data)
    update_data = json.loads(update.data)
    assert initial_data["depth"] == 1
    assert update_data["depth"] == 0


async def test_multiple_ws_clients_receive_same_broadcast(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url(fixture)) as ws1, session.ws_connect(ws_url(fixture)) as ws2:
            init1 = json.loads((await ws1.receive(timeout=2.0)).data)
            init2 = json.loads((await ws2.receive(timeout=2.0)).data)
            assert init1["depth"] == 1
            assert init2["depth"] == 1
            async with fixture.client.post(
                f"/card/{goal['id']}/decision", json={"decision": "reject"}
            ) as r:
                await r.json()
            upd1 = json.loads((await ws1.receive(timeout=2.0)).data)
            upd2 = json.loads((await ws2.receive(timeout=2.0)).data)
    assert upd1["depth"] == 0
    assert upd2["depth"] == 0


async def test_ws_updates_include_card_details(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
    )
    async with fixture.client.post("/refill") as r:
        await r.json()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url(fixture)) as ws:
            raw = await ws.receive(timeout=2.0)
    parsed = json.loads(raw.data)
    card = parsed["cards"][0]
    assert card["title"] is not None
    assert card["reason"] is not None
    assert card["transport"] == "gmail"
    assert card["action"] == "archive"
    assert card["messageId"] == "m1"


# -------- Slice 014 --------


async def test_browser_read_dispatches_and_journals(make_server):
    rules = empty_rules()
    rules.reversibility = [ReversibilityDecl(action="browser_read", reversible=True)]
    browser = create_fake_browser_sub_agent({
        "https://example.com/invoice": {
            "title": "Invoice #1234",
            "text": "Amount due: £250.00\nDue date: 2026-05-01",
        }
    })

    async def plan_fn(_):
        return {
            "id": "g-m1",
            "title": "Extract invoice amount from billing page",
            "reason": "Need to verify the invoice amount",
            "messageId": "m1",
            "transport": "browser",
            "action": "browser_read",
            "targetUrl": "https://example.com/invoice",
        }

    fixture = await make_server(
        messages=[{
            "id": "m1", "from": "billing@example.com",
            "subject": "Invoice", "body": "See invoice at https://example.com/invoice",
            "unread": True,
        }],
        rules=rules,
        plan=plan_fn,
        browser_sub_agent=browser,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    assert goal["transport"] == "browser"
    assert goal["action"] == "browser_read"
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["outcomes"][0]["success"] is True
    assert body["outcomes"][0]["pageTitle"] == "Invoice #1234"
    assert "£250.00" in body["outcomes"][0]["textContent"]
    assert body["verification"]["verified"] is True
    lines = Path(fixture.journal_path).read_text().strip().split("\n")
    entry = json.loads(lines[-1])
    assert entry["kind"] == "action"
    assert entry["transport"] == "browser"
    assert entry["action"] == "browser_read"


async def test_browser_read_only_enforces_capability():
    agent = create_fake_browser_sub_agent({})
    outcome = await agent.dispatch({
        "capability": "browser_write",
        "url": "https://example.com",
        "instruction": "Submit the form",
    })
    assert outcome["success"] is False
    assert "unknown capability" in outcome["error"]


async def test_browser_failure_handled_gracefully(make_server):
    rules = empty_rules()
    rules.reversibility = [ReversibilityDecl(action="browser_read", reversible=True)]
    # Empty responses map → every URL is unknown and returns failure
    browser = create_fake_browser_sub_agent({})

    async def plan_fn(_):
        return {
            "id": "g-m1",
            "title": "Extract something",
            "reason": "Need to extract",
            "messageId": "m1",
            "transport": "browser",
            "action": "browser_read",
            "targetUrl": "https://nowhere.example",
        }

    fixture = await make_server(
        messages=[{
            "id": "m1", "from": "a@b.com",
            "subject": "x", "body": "y", "unread": True,
        }],
        rules=rules,
        plan=plan_fn,
        browser_sub_agent=browser,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    # Failure is captured in the outcome
    assert body["outcomes"][0]["success"] is False
    assert body["verification"]["verified"] is False
