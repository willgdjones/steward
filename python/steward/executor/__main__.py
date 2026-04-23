"""Executor entry point."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aiohttp import web

from steward.executor.server import ServerDeps, create_executor_server
from steward.gmail.fake import FakeGmail
from steward.planner import plan_goal
from steward.rules import Rules, load_rules, watch_rules


def main() -> None:
    state_dir = Path(os.environ.get("STEWARD_STATE_DIR", "state")).resolve()
    port = int(os.environ.get("STEWARD_PORT", "8731"))
    rules_dir = Path(os.environ.get("STEWARD_RULES_DIR", str(state_dir))).resolve()
    search_query = os.environ.get("STEWARD_GMAIL_QUERY", "is:unread")

    gmail = FakeGmail(state_dir / "fake_inbox.json")
    journal_path = str(state_dir / "journal.jsonl")

    rules: Rules = load_rules(rules_dir)

    def on_change(updated: Rules) -> None:
        nonlocal rules
        rules = updated
        print("rules reloaded")

    watch_rules(rules_dir, on_change)

    async def trivial_plan(input_):
        return plan_goal(input_["message"])

    deps = ServerDeps(
        gmail=gmail,
        journal_path=journal_path,
        plan=trivial_plan,
        get_rules=lambda: rules,
        search_query=search_query,
        rules_dir=str(rules_dir),
    )
    server = create_executor_server(deps)
    print(f"steward executor listening on http://localhost:{port}")
    web.run_app(server.build_app(), port=port, print=None)


if __name__ == "__main__":
    main()
