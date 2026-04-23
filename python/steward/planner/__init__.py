"""Frontier-model planner. Takes redacted message + triage features, produces a goal."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, TypedDict

from steward.redactor import RedactedMessage
from steward.triage import TriageFeatures


class PlannerInput(TypedDict):
    message: RedactedMessage
    features: TriageFeatures
    snippet: str


@dataclass
class Goal:
    id: str
    title: str
    reason: str
    messageId: str
    transport: str | None = None
    action: str | None = None
    messageIds: list[str] | None = None
    batchSize: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        return d


PlanFn = Callable[[PlannerInput], Awaitable[Goal]]


PLANNER_SYSTEM = """You are a personal email assistant. Given a redacted email summary and triage features, produce a single goal for the user.

Respond with valid JSON only, no markdown, no explanation.

Schema:
{
  "title": "<short action-oriented title, max 80 chars>",
  "reason": "<one-sentence explanation of why this goal matters>",
  "transport": "gmail",
  "action": "<one of: archive, reply, read, flag, other>"
}"""


def plan_goal(message: RedactedMessage) -> Goal:
    """Trivial planner for slice-002 tracer bullet."""
    return Goal(
        id=f"g-{message['id']}",
        title=f"Review message from {message['fromDomain']}",
        reason=f"Subject: {message['subject']}",
        messageId=message["id"],
        transport="gmail",
        action="archive",
    )


def create_planner(client: Any, model: str = "claude-sonnet-4-5") -> PlanFn:
    async def plan(input: PlannerInput) -> Goal:
        m = input["message"]
        f = input["features"]
        user_content = "\n".join([
            f"Domain: {m['fromDomain']}",
            f"Subject: {m['subject']}",
            f"Snippet: {input['snippet']}",
            f"Category: {f['category']}",
            f"Urgency: {f['urgency']}",
            f"Deadline: {f.get('deadline') or 'none'}",
            f"Amount: {f.get('amount') or 'none'}",
            f"Waiting on user: {f['waiting_on_user']}",
        ])
        response = await client.messages.create(
            model=model,
            max_tokens=512,
            system=PLANNER_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        parsed = json.loads(text)
        return Goal(
            id=f"g-{m['id']}",
            title=parsed["title"],
            reason=parsed["reason"],
            messageId=m["id"],
            transport=parsed.get("transport") or "gmail",
            action=parsed.get("action") or "archive",
        )

    return plan
