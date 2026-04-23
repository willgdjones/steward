import pytest

from steward.browser.subagent import create_fake_browser_sub_agent


@pytest.fixture
def agent():
    return create_fake_browser_sub_agent({
        "https://example.com/invoice": {"title": "Invoice #1234", "text": "Amount due: £250.00\nDue date: 2026-05-01"},
        "https://example.com/status": {"title": "Order Status", "text": "Your order is shipped"},
    })


async def test_dispatch_browser_read_extracts_content(agent):
    outcome = await agent.dispatch({
        "capability": "browser_read",
        "url": "https://example.com/invoice",
        "instruction": "Extract the amount due and due date",
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == "browser_read"
    assert outcome["pageTitle"] == "Invoice #1234"
    assert "£250.00" in outcome["textContent"]
    assert "2026-05-01" in outcome["textContent"]


async def test_dispatch_unknown_url(agent):
    outcome = await agent.dispatch({
        "capability": "browser_read",
        "url": "https://unknown.com",
        "instruction": "Read",
    })
    assert outcome["success"] is False
    assert "no canned response" in outcome["error"]


async def test_dispatch_unknown_capability(agent):
    outcome = await agent.dispatch({
        "capability": "browser_write",
        "url": "https://example.com/invoice",
        "instruction": "Submit the form",
    })
    assert outcome["success"] is False
    assert "unknown capability" in outcome["error"]


async def test_verify_known_url(agent):
    v = await agent.verify("https://example.com/invoice")
    assert v["verified"] is True
    assert v["actual_title"] == "Invoice #1234"


async def test_verify_unknown_url(agent):
    v = await agent.verify("https://unknown.com")
    assert v["verified"] is False
