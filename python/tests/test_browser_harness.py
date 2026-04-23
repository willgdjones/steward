"""Unit tests for the browser-harness sub-agent — no live browser invoked.

The real-browser integration runs against the user's Chrome and cannot be
exercised by pytest without external setup. These tests cover:

- Script generation is deterministic and does NOT contain resolved creds
- STEWARD_RESULT parsing handles both success and failure payloads
- dispatch() for the authenticated capability passes creds via env vars
- dispatch() for unknown capability returns the standard unknown error
- verify() works via the injectable runner

Live-browser exercise is manual: with Chrome running + browser-harness --setup
done, call dispatch() against a real login page. Not automated.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from steward.browser.harness import (
    CRED_PASS_ENV,
    CRED_USER_ENV,
    RESULT_SENTINEL,
    BrowserHarnessSubAgent,
    build_authenticated_script,
    parse_result,
)
from steward.browser.subagent import AUTHENTICATED_READ_CAPABILITY, READ_CAPABILITY


# --- script generation ---


def test_script_does_not_contain_resolved_credentials():
    script = build_authenticated_script(
        login_url="https://bank.example/login",
        target_url="https://bank.example/account",
        username_selector="#email",
        password_selector="#pass",
        submit_selector="button[type=submit]",
    )
    # The script must read creds from env at runtime — never bake them in.
    assert "alice@example.com" not in script
    assert "SuperSecret99" not in script
    # It must reference the env var names so the runtime read works.
    assert CRED_USER_ENV in script
    assert CRED_PASS_ENV in script
    # URLs and selectors are fine as literals (not sensitive).
    assert "https://bank.example/login" in script
    assert "#email" in script


def test_script_has_result_sentinel_print():
    script = build_authenticated_script(
        login_url="https://x/login",
        target_url="https://x/account",
        username_selector="#u",
        password_selector="#p",
        submit_selector="#go",
    )
    assert RESULT_SENTINEL in script
    assert "print(" in script


def test_script_optional_extract_selector():
    with_sel = build_authenticated_script(
        login_url="https://x/login",
        target_url="https://x/account",
        username_selector="#u",
        password_selector="#p",
        submit_selector="#go",
        extract_selector=".balance",
    )
    without_sel = build_authenticated_script(
        login_url="https://x/login",
        target_url="https://x/account",
        username_selector="#u",
        password_selector="#p",
        submit_selector="#go",
    )
    assert ".balance" in with_sel
    assert "_EXTRACT_SELECTOR = None" in without_sel


# --- result parsing ---


def test_parse_result_finds_sentinel_line():
    stdout = f"some chatter from daemon\nmore chatter\n{RESULT_SENTINEL}" + json.dumps({
        "success": True, "url": "https://x", "title": "T", "text": "body",
    }) + "\ntrailing output\n"
    result = parse_result(stdout)
    assert result["success"] is True
    assert result["url"] == "https://x"


def test_parse_result_missing_sentinel_raises():
    with pytest.raises(RuntimeError) as exc:
        parse_result("no sentinel anywhere\njust noise")
    assert RESULT_SENTINEL in str(exc.value)


# --- dispatch with injected runner ---


async def test_authenticated_dispatch_passes_creds_via_env():
    captured_env: dict[str, str] = {}
    captured_script: str = ""

    async def fake_runner(script: str, env: dict[str, str], timeout: float) -> str:
        nonlocal captured_env, captured_script
        captured_env = dict(env)
        captured_script = script
        payload = {
            "success": True,
            "url": "https://bank.example/account",
            "title": "Account",
            "text": "Balance: £1,234.56",
        }
        return f"{RESULT_SENTINEL}{json.dumps(payload)}\n"

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    outcome = await agent.dispatch({
        "capability": AUTHENTICATED_READ_CAPABILITY,
        "login_url": "https://bank.example/login",
        "target_url": "https://bank.example/account",
        "username_selector": "#email",
        "password_selector": "#pass",
        "submit_selector": "button[type=submit]",
        "resolved_creds": ["alice@example.com", "SuperSecret99"],
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == AUTHENTICATED_READ_CAPABILITY
    assert outcome["pageTitle"] == "Account"
    assert outcome["textContent"] == "Balance: £1,234.56"
    # Creds reached the subprocess via env, not script text
    assert captured_env[CRED_USER_ENV] == "alice@example.com"
    assert captured_env[CRED_PASS_ENV] == "SuperSecret99"
    assert "alice@example.com" not in captured_script
    assert "SuperSecret99" not in captured_script


async def test_authenticated_dispatch_harness_failure_returns_error():
    async def fake_runner(script: str, env: dict[str, str], timeout: float) -> str:
        payload = {"success": False, "error": "login form not found"}
        return f"{RESULT_SENTINEL}{json.dumps(payload)}\n"

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    outcome = await agent.dispatch({
        "capability": AUTHENTICATED_READ_CAPABILITY,
        "login_url": "https://x",
        "target_url": "https://x/a",
        "username_selector": "#u",
        "password_selector": "#p",
        "submit_selector": "#s",
        "resolved_creds": ["u", "p"],
    })
    assert outcome["success"] is False
    assert "login form not found" in outcome["error"]


async def test_authenticated_dispatch_subprocess_crash_returns_error():
    async def fake_runner(script: str, env: dict[str, str], timeout: float) -> str:
        raise RuntimeError("harness crashed")

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    outcome = await agent.dispatch({
        "capability": AUTHENTICATED_READ_CAPABILITY,
        "login_url": "https://x",
        "target_url": "https://x/a",
        "username_selector": "#u",
        "password_selector": "#p",
        "submit_selector": "#s",
        "resolved_creds": ["u", "p"],
    })
    assert outcome["success"] is False
    assert "browser-harness invocation failed" in outcome["error"]


async def test_authenticated_dispatch_malformed_output_returns_error():
    async def fake_runner(script: str, env: dict[str, str], timeout: float) -> str:
        return "daemon noise only, no sentinel here\n"

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    outcome = await agent.dispatch({
        "capability": AUTHENTICATED_READ_CAPABILITY,
        "login_url": "https://x",
        "target_url": "https://x/a",
        "username_selector": "#u",
        "password_selector": "#p",
        "submit_selector": "#s",
        "resolved_creds": ["u", "p"],
    })
    assert outcome["success"] is False
    assert "could not parse harness output" in outcome["error"]


async def test_unknown_capability_rejected():
    async def fake_runner(script, env, timeout):
        raise AssertionError("runner should not be called for unknown capability")

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    outcome = await agent.dispatch({"capability": "browser_submit", "url": "https://x"})
    assert outcome["success"] is False
    assert "unknown capability" in outcome["error"]


async def test_public_browser_read_also_supported():
    async def fake_runner(script: str, env: dict[str, str], timeout: float) -> str:
        payload = {"success": True, "url": "https://public.example",
                   "title": "Public Page", "text": "body text"}
        return f"{RESULT_SENTINEL}{json.dumps(payload)}\n"

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    outcome = await agent.dispatch({
        "capability": READ_CAPABILITY,
        "url": "https://public.example",
        "instruction": "Read this page",
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == READ_CAPABILITY
    assert outcome["pageTitle"] == "Public Page"


async def test_verify_via_runner():
    async def fake_runner(script: str, env: dict[str, str], timeout: float) -> str:
        payload = {"success": True, "url": "https://bank.example/account", "title": "Account"}
        return f"{RESULT_SENTINEL}{json.dumps(payload)}\n"

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    v = await agent.verify("https://bank.example/account")
    assert v["verified"] is True
    assert v["actual_url"] == "https://bank.example/account"
    assert v["actual_title"] == "Account"


async def test_verify_harness_failure_returns_unverified():
    async def fake_runner(script: str, env: dict[str, str], timeout: float) -> str:
        raise RuntimeError("harness down")

    agent = BrowserHarnessSubAgent(runner=fake_runner)
    v = await agent.verify("https://x")
    assert v["verified"] is False
    assert v["actual_url"] == ""
