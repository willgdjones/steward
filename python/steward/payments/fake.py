"""In-memory fake payment provider. Stands in for Stripe Issuing or equivalent.

Amounts are minor units (pence for GBP, cents for USD) — always int. Never
float. Avoids all the usual pain of representing money in binary.
"""
from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakePaymentProvider:
    """Records charges in memory. Tests can assert against `.charges`.

    A real provider (Stripe Issuing, etc.) implements the same surface —
    `charge(...)` and `get_charge(...)` — so the sub-agent doesn't care.
    """

    charges: list[dict[str, Any]] = field(default_factory=list)
    # Simulate issuer-side failures by setting this to raise
    next_charge_raises: Exception | None = None

    def charge(
        self,
        amount_pence: int,
        currency: str,
        payee: str,
        card_ref: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record a charge and return the structured receipt.

        idempotency_key: if provided and already seen, returns the existing
        charge unchanged. Mirrors Stripe's idempotency semantics.
        """
        if self.next_charge_raises is not None:
            err = self.next_charge_raises
            self.next_charge_raises = None
            raise err

        if idempotency_key is not None:
            for existing in self.charges:
                if existing.get("idempotency_key") == idempotency_key:
                    return dict(existing)

        charge_id = "ch_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        record = {
            "id": charge_id,
            "amount_pence": int(amount_pence),
            "currency": currency,
            "payee": payee,
            "card_ref": card_ref,
            "idempotency_key": idempotency_key,
            "ts_ms": int(time.time() * 1000),
            "status": "succeeded",
        }
        self.charges.append(record)
        return dict(record)

    def get_charge(self, charge_id: str) -> dict[str, Any] | None:
        for c in self.charges:
            if c["id"] == charge_id:
                return dict(c)
        return None
