"""Executor entry point."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from aiohttp import web

from steward.browser.harness import create_browser_harness_sub_agent
from steward.credentials import OpResolver
from steward.executor.server import ServerDeps, create_executor_server
from steward.gmail.fake import FakeGmail
from steward.gmail.provider import GmailProvider
from steward.planner import plan_goal
from steward.rules import Rules, load_rules, watch_rules


def _build_gmail(state_dir: Path, mode: str) -> GmailProvider:
    """Pick the Gmail provider based on STEWARD_GMAIL.

    Default: fake — reads state/fake_inbox.json.
    `real`: RealGmail. Requires 1Password with client_id / client_secret /
      refresh_token resolvable at startup. Paths are env-configurable so
      the user can point at different vault items without editing source.
    """
    if mode != "real":
        return FakeGmail(state_dir / "fake_inbox.json")

    from steward.gmail.real import RealGmail

    resolver = OpResolver()
    if not resolver.is_unlocked():
        print("STEWARD_GMAIL=real but 1Password vault is locked. Unlock with `eval $(op signin)`.", file=sys.stderr)
        sys.exit(2)
    client_id_ref = os.environ.get("STEWARD_GMAIL_CLIENT_ID_REF", "op://vault/gmail/client_id")
    client_secret_ref = os.environ.get("STEWARD_GMAIL_CLIENT_SECRET_REF", "op://vault/gmail/client_secret")
    refresh_token_ref = os.environ.get("STEWARD_GMAIL_REFRESH_TOKEN_REF", "op://vault/gmail/refresh_token")
    try:
        client_id = resolver.resolve(client_id_ref)
        client_secret = resolver.resolve(client_secret_ref)
        refresh_token = resolver.resolve(refresh_token_ref)
    except Exception as e:
        print(f"Failed to resolve Gmail credentials from 1Password: {e}", file=sys.stderr)
        sys.exit(3)
    print(f"Gmail provider: real (client_id from {client_id_ref})")
    return RealGmail.from_credentials(client_id, client_secret, refresh_token)


def main() -> None:
    state_dir = Path(os.environ.get("STEWARD_STATE_DIR", "state")).resolve()
    port = int(os.environ.get("STEWARD_PORT", "8731"))
    rules_dir = Path(os.environ.get("STEWARD_RULES_DIR", str(state_dir))).resolve()
    search_query = os.environ.get("STEWARD_GMAIL_QUERY", "is:unread")

    gmail = _build_gmail(state_dir, os.environ.get("STEWARD_GMAIL", ""))
    journal_path = str(state_dir / "journal.jsonl")

    rules: Rules = load_rules(rules_dir)

    def on_change(updated: Rules) -> None:
        nonlocal rules
        rules = updated
        print("rules reloaded")

    watch_rules(rules_dir, on_change)

    async def trivial_plan(input_):
        return plan_goal(input_["message"])

    # Browser sub-agent: opt in via STEWARD_BROWSER=harness.
    browser_sub_agent = None
    if os.environ.get("STEWARD_BROWSER", "") == "harness":
        browser_sub_agent = create_browser_harness_sub_agent()
        print("browser sub-agent: browser-harness (real Chrome via CDP)")

    # Credential resolver: opt in via STEWARD_CREDENTIALS=op.
    credential_resolver = None
    if os.environ.get("STEWARD_CREDENTIALS", "") == "op":
        credential_resolver = OpResolver()
        print("credential resolver: 1Password CLI")

    deps = ServerDeps(
        gmail=gmail,
        journal_path=journal_path,
        plan=trivial_plan,
        get_rules=lambda: rules,
        search_query=search_query,
        rules_dir=str(rules_dir),
        browser_sub_agent=browser_sub_agent,
        credential_resolver=credential_resolver,
    )
    server = create_executor_server(deps)
    print(f"steward executor listening on http://localhost:{port}")
    web.run_app(server.build_app(), port=port, print=None)


if __name__ == "__main__":
    main()
