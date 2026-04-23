"""Cluster triaged candidates by (sender domain, category) into batched cards."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from steward.triage import TriageResult


@dataclass
class TriagedCandidate:
    message: dict[str, Any]
    result: TriageResult


@dataclass
class Cluster:
    domain: str
    category: str
    candidates: list[TriagedCandidate] = field(default_factory=list)


def _sender_domain(from_addr: str) -> str:
    at = from_addr.rfind("@")
    return from_addr[at + 1:].lower() if at >= 0 else from_addr.lower()


def cluster_candidates(
    candidates: list[TriagedCandidate],
    batch_threshold: int,
) -> tuple[list[Cluster], list[TriagedCandidate]]:
    """Return (batches, remaining) — batches have ≥ threshold candidates each."""
    groups: dict[str, list[TriagedCandidate]] = {}
    for c in candidates:
        domain = _sender_domain(c.message["from"])
        category = c.result.features["category"]
        key = f"{domain}::{category}"
        groups.setdefault(key, []).append(c)

    batches: list[Cluster] = []
    remaining: list[TriagedCandidate] = []
    for key, group in groups.items():
        if len(group) >= batch_threshold:
            domain, category = key.split("::", 1)
            batches.append(Cluster(domain=domain, category=category, candidates=group))
        else:
            remaining.extend(group)
    return batches, remaining
