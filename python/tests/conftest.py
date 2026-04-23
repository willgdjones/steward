"""Shared test helpers for e2e tests."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from steward.executor.server import ServerDeps, create_executor_server
from steward.gmail.fake import FakeGmail
from steward.planner import plan_goal
from steward.rules import (
    PromotionConfig,
    QueueConfig,
    Rules,
    VerifierConfig,
)


def empty_rules(**overrides) -> Rules:
    r = Rules(
        blacklist=[],
        redaction=[],
        queue=QueueConfig(target_depth=5, low_water_mark=2, batch_threshold=999, exploration_slots=0),
        urgent_senders=[],
        floor=[],
        reversibility=[],
        credential_scopes=[],
        verifier=VerifierConfig(interval_minutes=60),
        promotion=PromotionConfig(threshold=5, cooldown_minutes=1440, interval_minutes=120),
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


async def trivial_plan(input_data):
    return plan_goal(input_data["message"])


class ServerFixture:
    """Wraps aiohttp test server + client so tests can start/stop easily."""

    def __init__(self, server: TestServer, client: TestClient, executor) -> None:
        self.server = server
        self.client = client
        self.executor = executor

    @property
    def url(self) -> str:
        return str(self.server.make_url("/")).rstrip("/")

    async def close(self) -> None:
        await self.client.close()


async def start_server(deps: ServerDeps) -> ServerFixture:
    executor = create_executor_server(deps)
    app = executor.build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return ServerFixture(server=server, client=client, executor=executor)


@pytest.fixture
async def make_server(tmp_path):
    """Factory fixture: returns a function to create a server with specific deps."""
    fixtures: list[ServerFixture] = []

    async def factory(
        messages: list[dict[str, Any]] | None = None,
        rules: Rules | None = None,
        plan=None,
        triage=None,
        **kwargs,
    ) -> ServerFixture:
        gmail = FakeGmail(tmp_path / f"fake_inbox_{len(fixtures)}.json")
        if messages is not None:
            gmail.save(messages)
        r = rules if rules is not None else empty_rules()
        deps = ServerDeps(
            gmail=gmail,
            journal_path=str(tmp_path / f"journal_{len(fixtures)}.jsonl"),
            plan=plan or trivial_plan,
            get_rules=lambda rr=r: rr,
            triage=triage,
            **kwargs,
        )
        fixture = await start_server(deps)
        fixture.gmail = gmail  # type: ignore[attr-defined]
        fixture.journal_path = deps.journal_path  # type: ignore[attr-defined]
        fixture.rules = r  # type: ignore[attr-defined]
        fixture.deps = deps  # type: ignore[attr-defined]
        fixtures.append(fixture)
        return fixture

    yield factory

    for f in fixtures:
        await f.close()
