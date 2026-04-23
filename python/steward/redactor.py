"""Deterministic, non-LLM redactor. Sits between triage and planner."""
from __future__ import annotations

import re
from typing import Any, TypedDict

from steward.rules import RedactionRule


class RedactedMessage(TypedDict, total=False):
    id: str
    fromDomain: str
    subject: str


def redact(message: dict[str, Any]) -> RedactedMessage:
    """Strip body, reduce from to domain."""
    from_addr: str = message["from"]
    at = from_addr.rfind("@")
    from_domain = from_addr[at + 1:] if at >= 0 else from_addr
    return {
        "id": message["id"],
        "fromDomain": from_domain,
        "subject": message["subject"],
    }


def apply_redaction_rules(
    message: RedactedMessage,
    rules: list[RedactionRule],
) -> RedactedMessage:
    """Apply rule-driven redaction on top of the base. Returns a new dict."""
    if not rules:
        return message
    result: dict[str, Any] = dict(message)
    for rule in rules:
        field = rule.get("field")
        if not field or field not in result or field == "id":
            continue
        value = result[field]
        if not isinstance(value, str):
            continue
        pattern = rule.get("pattern")
        if pattern:
            result[field] = re.sub(pattern, "[REDACTED]", value)
        else:
            result[field] = "[REDACTED]"
    return result  # type: ignore[return-value]
