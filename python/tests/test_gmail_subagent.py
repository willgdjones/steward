import pytest

from steward.gmail.fake import FakeGmail
from steward.gmail.subagent import create_gmail_sub_agent


@pytest.fixture
def gmail(tmp_path):
    g = FakeGmail(tmp_path / "fake_inbox.json")
    g.save([
        {"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True, "archived": False},
        {"id": "m2", "from": "bob@example.com", "subject": "meeting", "body": "details", "unread": True, "archived": False},
    ])
    return g


async def test_archive_returns_structured_outcome(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({
        "capability": "archive",
        "messageId": "m1",
        "instruction": "Archive this newsletter",
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == "archive"
    assert outcome["messageId"] == "m1"
    msg = gmail.get_by_id("m1")
    assert msg["archived"] is True


async def test_verification_confirms_archive(gmail):
    agent = create_gmail_sub_agent(gmail)
    await agent.dispatch({"capability": "archive", "messageId": "m1", "instruction": "x"})
    v = await agent.verify("m1", "archive")
    assert v["verified"] is True
    assert v["actual_state"] == "archived"


async def test_verification_detects_non_archived(gmail):
    agent = create_gmail_sub_agent(gmail)
    v = await agent.verify("m1", "archive")
    assert v["verified"] is False
    assert v["actual_state"] == "not_archived"


async def test_archive_unknown_message(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({"capability": "archive", "messageId": "nonexistent", "instruction": "x"})
    assert outcome["success"] is False
    assert "not found" in outcome["error"]


async def test_unknown_capability(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({"capability": "delete", "messageId": "m1", "instruction": "x"})
    assert outcome["success"] is False
    assert "unknown capability" in outcome["error"]


async def test_archived_messages_not_in_search(gmail):
    agent = create_gmail_sub_agent(gmail)
    await agent.dispatch({"capability": "archive", "messageId": "m1", "instruction": "x"})
    results = gmail.search("is:unread")
    assert len(results) == 1
    assert results[0]["id"] == "m2"


async def test_draft_reply_creates_draft(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({
        "capability": "draft_reply",
        "messageId": "m1",
        "instruction": "Reply to alice",
        "draftBody": "Thanks!",
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == "draft_reply"
    assert "draftId" in outcome
    draft = gmail.get_draft(outcome["draftId"])
    assert draft is not None
    assert draft["to"] == "alice@example.com"
    assert draft["subject"] == "Re: hello"
    assert draft["body"] == "Thanks!"


async def test_draft_reply_verification(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({
        "capability": "draft_reply",
        "messageId": "m1",
        "instruction": "x",
        "draftBody": "Thanks!",
    })
    v = await agent.verify("m1", "draft_reply", {"draftId": outcome["draftId"]})
    assert v["verified"] is True
    assert v["actual_state"] == "draft_exists"


async def test_draft_reply_fails_for_nonexistent_message(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({
        "capability": "draft_reply",
        "messageId": "nonexistent",
        "instruction": "x",
        "draftBody": "Hello",
    })
    assert outcome["success"] is False
    assert "not found" in outcome["error"]


async def test_draft_reply_verification_without_draft_id(gmail):
    agent = create_gmail_sub_agent(gmail)
    v = await agent.verify("m1", "draft_reply")
    assert v["verified"] is False
    assert v["actual_state"] == "no_draft_id"


async def test_send_draft_success(gmail):
    agent = create_gmail_sub_agent(gmail)
    d = await agent.dispatch({"capability": "draft_reply", "messageId": "m1", "instruction": "x", "draftBody": "hi"})
    s = await agent.dispatch({"capability": "send_draft", "messageId": "m1", "instruction": "send", "draftId": d["draftId"]})
    assert s["success"] is True
    assert s["action_taken"] == "send_draft"
    assert s["draftId"] == d["draftId"]


async def test_send_draft_verification(gmail):
    agent = create_gmail_sub_agent(gmail)
    d = await agent.dispatch({"capability": "draft_reply", "messageId": "m1", "instruction": "x", "draftBody": "hi"})
    await agent.dispatch({"capability": "send_draft", "messageId": "m1", "instruction": "send", "draftId": d["draftId"]})
    v = await agent.verify("m1", "send_draft", {"draftId": d["draftId"]})
    assert v["verified"] is True
    assert v["actual_state"] == "sent"


async def test_send_draft_fails_without_draft_id(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({"capability": "send_draft", "messageId": "m1", "instruction": "x"})
    assert outcome["success"] is False
    assert "no draftId" in outcome["error"]


async def test_send_draft_fails_for_nonexistent_draft(gmail):
    agent = create_gmail_sub_agent(gmail)
    outcome = await agent.dispatch({
        "capability": "send_draft",
        "messageId": "m1",
        "instruction": "x",
        "draftId": "nonexistent",
    })
    assert outcome["success"] is False
    assert "not found" in outcome["error"]
