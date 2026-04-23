"""Slice 002 — end-to-end skeleton: read gmail → card → approve → journal."""
import json
from pathlib import Path

from steward.executor.planner_client import sanitise_env_for_planner
from steward.redactor import redact


async def test_reads_gmail_produces_card_approve_writes_journal(make_server, tmp_path):
    fixture = await make_server(messages=[
        {"id": "m1", "from": "alice@example.com", "subject": "hello",
         "body": "sensitive body content", "unread": True},
    ])
    async with fixture.client.get("/card") as r:
        assert r.status == 200
        goal = await r.json()
    assert "example.com" in goal["title"]

    async with fixture.client.post(
        f"/card/{goal['id']}/decision",
        json={"decision": "approve"},
    ) as r:
        assert r.status == 200

    journal_path = Path(fixture.journal_path)
    assert journal_path.exists()
    lines = journal_path.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    # Trivial planner's action is 'archive' → executor dispatches to sub-agent → journal kind is 'action'
    assert entry["kind"] == "action"
    assert entry["goalId"] == goal["id"]
    assert entry["messageId"] == "m1"
    assert entry["outcomes"][0]["success"] is True
    assert entry["outcomes"][0]["action_taken"] == "archive"
    assert entry["verification"]["verified"] is True


async def test_serves_web_client_at_root(make_server):
    fixture = await make_server(messages=[])
    async with fixture.client.get("/") as r:
        assert r.status == 200
        html = await r.text()
    assert "steward" in html
    assert "approve" in html


async def test_returns_204_when_no_unread(make_server):
    fixture = await make_server(messages=[])
    async with fixture.client.get("/card") as r:
        assert r.status == 204


def test_redactor_drops_body_reduces_from_to_domain():
    r = redact({"id": "m1", "from": "alice@example.com", "subject": "hi", "body": "secret", "unread": True})
    assert r == {"id": "m1", "fromDomain": "example.com", "subject": "hi"}
    assert "body" not in r


def test_sanitise_env_strips_credential_bearing_vars():
    env = sanitise_env_for_planner({
        "PATH": "/usr/bin",
        "STEWARD_CREDENTIALS_DIR": "/secret/creds",
        "GMAIL_OAUTH_TOKEN": "abc",
        "MY_API_KEY": "def",
        "MY_SECRET": "ghi",
        "USER_PASSWORD": "jkl",
        "HARMLESS": "ok",
    })
    assert env.get("PATH") == "/usr/bin"
    assert env.get("HARMLESS") == "ok"
    assert "STEWARD_CREDENTIALS_DIR" not in env
    assert "GMAIL_OAUTH_TOKEN" not in env
    assert "MY_API_KEY" not in env
    assert "MY_SECRET" not in env
    assert "USER_PASSWORD" not in env
