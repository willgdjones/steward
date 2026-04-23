from steward.browser.subagent import (
    AUTHENTICATED_READ_CAPABILITY,
    create_fake_browser_sub_agent,
)


async def test_authenticated_dispatch_happy_path():
    agent = create_fake_browser_sub_agent({
        "https://bank.example/account": {
            "title": "Account Balance",
            "text": "Balance: £1,234.56",
        }
    })
    outcome = await agent.dispatch({
        "capability": AUTHENTICATED_READ_CAPABILITY,
        "login_url": "https://bank.example/login",
        "target_url": "https://bank.example/account",
        "username_selector": "#email",
        "password_selector": "#pass",
        "submit_selector": "button[type=submit]",
        "extraction_instruction": "Read the balance",
        "resolved_creds": ["alice@example.com", "SuperSecret99"],
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == AUTHENTICATED_READ_CAPABILITY
    assert outcome["pageTitle"] == "Account Balance"
    assert outcome["textContent"] == "Balance: £1,234.56"


async def test_authenticated_reflects_credentials_in_text_pre_redaction():
    # Proves the fake echoes credentials when asked. The executor is responsible
    # for running the redactor over this output before it leaves the boundary.
    agent = create_fake_browser_sub_agent({
        "https://example.com/account": {
            "title": "Account",
            "text": "Logged in",
            "reflects_credentials": True,
        }
    })
    outcome = await agent.dispatch({
        "capability": AUTHENTICATED_READ_CAPABILITY,
        "login_url": "https://example.com/login",
        "target_url": "https://example.com/account",
        "username_selector": "#u",
        "password_selector": "#p",
        "submit_selector": "#go",
        "extraction_instruction": "Extract",
        "resolved_creds": ["alice@example.com", "SuperSecret99"],
    })
    assert "alice@example.com" in outcome["textContent"]
    assert "SuperSecret99" in outcome["textContent"]


async def test_unknown_capability_rejected():
    agent = create_fake_browser_sub_agent({})
    outcome = await agent.dispatch({
        "capability": "browser_submit",
        "target_url": "https://example.com",
    })
    assert outcome["success"] is False
    assert "unknown capability" in outcome["error"]


async def test_authenticated_dispatch_unknown_url_returns_failure():
    agent = create_fake_browser_sub_agent({})
    outcome = await agent.dispatch({
        "capability": AUTHENTICATED_READ_CAPABILITY,
        "login_url": "https://example.com/login",
        "target_url": "https://nowhere.example",
        "username_selector": "#u",
        "password_selector": "#p",
        "submit_selector": "#go",
        "extraction_instruction": "x",
        "resolved_creds": ["a", "b"],
    })
    assert outcome["success"] is False
    assert "no canned response" in outcome["error"]
