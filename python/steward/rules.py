"""Rules loader. Parses principles.md as YAML. Supports file watching via a poll thread."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypedDict

import yaml


class BlacklistEntry(TypedDict):
    transport: str
    action: str


class RedactionRule(TypedDict, total=False):
    field: str
    pattern: str


@dataclass
class FloorReservation:
    match: dict[str, Any]
    slots: int


@dataclass
class QueueConfig:
    target_depth: int = 5
    low_water_mark: int = 2
    batch_threshold: int = 3
    exploration_slots: int = 1


@dataclass
class ReversibilityDecl:
    action: str
    reversible: bool


@dataclass
class VerifierConfig:
    interval_minutes: int = 60


@dataclass
class PromotionConfig:
    threshold: int = 5
    cooldown_minutes: int = 1440
    interval_minutes: int = 120


@dataclass
class CredentialScopeDecl:
    action: str
    refs: list[str]


@dataclass
class SpendingLimits:
    """Per-payment-class limits enforced by the executor before dispatch.
    Amounts are minor units (pence). None means no limit (issuer-side limit
    is still active as a second defence layer)."""
    max_per_charge_pence: int | None = None
    max_per_day_pence: int | None = None
    max_per_week_pence: int | None = None


@dataclass
class Rules:
    blacklist: list[BlacklistEntry] = field(default_factory=list)
    redaction: list[RedactionRule] = field(default_factory=list)
    queue: QueueConfig = field(default_factory=QueueConfig)
    urgent_senders: list[str] = field(default_factory=list)
    floor: list[FloorReservation] = field(default_factory=list)
    reversibility: list[ReversibilityDecl] = field(default_factory=list)
    credential_scopes: list[CredentialScopeDecl] = field(default_factory=list)
    verifier: VerifierConfig = field(default_factory=VerifierConfig)
    promotion: PromotionConfig = field(default_factory=PromotionConfig)
    spending_limits: SpendingLimits = field(default_factory=SpendingLimits)


def _load_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None
    parsed = yaml.safe_load(content)
    return parsed if isinstance(parsed, dict) else None


def load_rules(directory: str | Path) -> Rules:
    d = Path(directory)
    principles = _load_file(d / "principles.md")
    _load_file(d / "gmail.md")  # reserved for future per-surface rules

    if not principles:
        return Rules()

    blacklist: list[BlacklistEntry] = []
    for entry in principles.get("blacklist") or []:
        blacklist.append({"transport": entry["transport"], "action": entry["action"]})

    redaction: list[RedactionRule] = []
    for entry in principles.get("redaction") or []:
        rule: RedactionRule = {"field": entry["field"]}
        if entry.get("pattern"):
            rule["pattern"] = entry["pattern"]
        redaction.append(rule)

    queue_raw = principles.get("queue") or {}
    queue = QueueConfig(
        target_depth=queue_raw.get("target_depth", 5),
        low_water_mark=queue_raw.get("low_water_mark", 2),
        batch_threshold=queue_raw.get("batch_threshold", 3),
        exploration_slots=queue_raw.get("exploration_slots", 1),
    )

    urgent_senders = [str(s).lower() for s in (principles.get("urgent_senders") or [])]

    floor: list[FloorReservation] = []
    for entry in principles.get("floor") or []:
        floor.append(
            FloorReservation(
                match=entry.get("match") or {},
                slots=int(entry.get("slots", 1)),
            )
        )

    reversibility: list[ReversibilityDecl] = []
    for entry in principles.get("reversibility") or []:
        reversibility.append(
            ReversibilityDecl(
                action=str(entry.get("action")),
                reversible=entry.get("reversible") is True,
            )
        )

    verifier_raw = principles.get("verifier") or {}
    verifier = VerifierConfig(interval_minutes=verifier_raw.get("interval_minutes", 60))

    promotion_raw = principles.get("promotion") or {}
    promotion = PromotionConfig(
        threshold=promotion_raw.get("threshold", 5),
        cooldown_minutes=promotion_raw.get("cooldown_minutes", 1440),
        interval_minutes=promotion_raw.get("interval_minutes", 120),
    )

    credential_scopes: list[CredentialScopeDecl] = []
    for entry in principles.get("credential_scopes") or []:
        credential_scopes.append(
            CredentialScopeDecl(
                action=str(entry.get("action")),
                refs=[str(r) for r in (entry.get("refs") or [])],
            )
        )

    spending_raw = principles.get("spending_limits") or {}
    spending_limits = SpendingLimits(
        max_per_charge_pence=spending_raw.get("max_per_charge_pence"),
        max_per_day_pence=spending_raw.get("max_per_day_pence"),
        max_per_week_pence=spending_raw.get("max_per_week_pence"),
    )

    return Rules(
        blacklist=blacklist,
        redaction=redaction,
        queue=queue,
        urgent_senders=urgent_senders,
        floor=floor,
        reversibility=reversibility,
        credential_scopes=credential_scopes,
        verifier=verifier,
        promotion=promotion,
        spending_limits=spending_limits,
    )


@dataclass
class RulesWatcher:
    stop: Callable[[], None]


def watch_rules(
    directory: str | Path,
    on_change: Callable[[Rules], None],
    poll_interval: float = 1.0,
) -> RulesWatcher:
    """Simple polling watcher — reloads when principles.md or gmail.md mtime changes."""
    d = Path(directory)
    watched = [d / "principles.md", d / "gmail.md"]
    stop_event = threading.Event()

    def snapshot() -> tuple[float, ...]:
        return tuple(p.stat().st_mtime if p.exists() else 0.0 for p in watched)

    last = snapshot()

    def loop() -> None:
        nonlocal last
        while not stop_event.wait(poll_interval):
            current = snapshot()
            if current != last:
                last = current
                try:
                    on_change(load_rules(d))
                except Exception:
                    pass

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return RulesWatcher(stop=stop_event.set)
