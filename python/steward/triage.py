"""Cheap-model triage stage. Extracts structured features from raw messages."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypedDict


Urgency = Literal["high", "medium", "low"]


class TriageFeatures(TypedDict):
    deadline: str | None
    amount: str | None
    waiting_on_user: bool
    category: str
    urgency: Urgency


@dataclass
class TriageResult:
    features: TriageFeatures
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {"features": dict(self.features), "snippet": self.snippet}


TriageFn = Callable[[dict[str, Any]], Awaitable[TriageResult]]


TRIAGE_SYSTEM = """You are an email triage assistant. Extract structured features from the email.
Respond with valid JSON only, no markdown, no explanation.

Schema:
{
  "features": {
    "deadline": "<ISO date string or null if no deadline>",
    "amount": "<monetary amount as string or null if none>",
    "waiting_on_user": <true if the sender is waiting for the user to respond>,
    "category": "<one of: newsletter, transaction, personal, work, notification, marketing, other>",
    "urgency": "<one of: high, medium, low>"
  },
  "snippet": "<one-sentence factual summary of what the email is about, no personal names or account numbers>"
}"""


def create_triage(client: Any, model: str = "claude-haiku-4-5-20251001") -> TriageFn:
    """Factory for a triage function backed by a cheap frontier model."""

    async def triage(message: dict[str, Any]) -> TriageResult:
        user_content = f"From: {message['from']}\nSubject: {message['subject']}\n\n{message.get('body', '')}"
        response = await client.messages.create(
            model=model,
            max_tokens=512,
            system=TRIAGE_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        data = json.loads(text)
        return TriageResult(features=data["features"], snippet=data["snippet"])

    return triage


def default_triage_result() -> TriageResult:
    return TriageResult(
        features={
            "deadline": None,
            "amount": None,
            "waiting_on_user": False,
            "category": "other",
            "urgency": "low",
        },
        snippet="No triage available.",
    )
