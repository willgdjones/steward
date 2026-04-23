"""Deterministic blacklist gate enforced before dispatching any approved action."""
from __future__ import annotations

from dataclasses import dataclass

from steward.rules import BlacklistEntry


@dataclass
class GateResult:
    allowed: bool
    reason: str | None = None


def check_blacklist(
    blacklist: list[BlacklistEntry],
    transport: str,
    action: str,
) -> GateResult:
    t = transport.lower()
    a = action.lower()
    for entry in blacklist:
        if entry["transport"].lower() == t and entry["action"].lower() == a:
            return GateResult(
                allowed=False,
                reason=f"Blacklisted: ({entry['transport']}, {entry['action']})",
            )
    return GateResult(allowed=True)
