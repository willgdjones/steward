"""Payments sub-agent. `charge` action — declared irreversible in principles.md.

Instruction shape:
    {
        "capability": "charge",
        "amount_pence": int,
        "currency": str,            # "GBP", "USD", ...
        "payee": str,
        "card_ref": str,            # op:// reference (string only; resolution
                                    #                 happens at the executor
                                    #                 boundary, like send_draft)
        "idempotency_key": str|None,
    }

Outcome shape:
    {
        "success": bool,
        "action_taken": "charge",
        "charge_id": str | None,
        "amount_pence": int | None,
        "currency": str | None,
        "payee": str | None,
        "error": str | None,
    }

Verification re-fetches the charge and confirms amount/payee match the
instruction. A mismatch means the issuer charged differently than asked —
should never happen with idempotency keys + immediate confirmation, but we
check anyway.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from steward.payments.fake import FakePaymentProvider

CHARGE_CAPABILITY = "charge"


class PaymentsSubAgent(Protocol):
    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]: ...
    async def verify(
        self,
        charge_id: str,
        expected_amount_pence: int,
        expected_payee: str,
    ) -> dict[str, Any]: ...


@dataclass
class FakePaymentsSubAgent:
    provider: FakePaymentProvider

    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]:
        cap = instruction.get("capability")
        if cap != CHARGE_CAPABILITY:
            return {
                "success": False,
                "action_taken": cap or "unknown",
                "error": f"unknown capability: {cap}",
            }
        amount = instruction.get("amount_pence")
        currency = instruction.get("currency")
        payee = instruction.get("payee")
        card_ref = instruction.get("card_ref")
        idempotency_key = instruction.get("idempotency_key")

        if not isinstance(amount, int) or amount <= 0:
            return {
                "success": False,
                "action_taken": CHARGE_CAPABILITY,
                "error": f"invalid amount_pence: {amount!r}",
            }
        if not currency or not payee or not card_ref:
            return {
                "success": False,
                "action_taken": CHARGE_CAPABILITY,
                "error": "missing required field (currency, payee, card_ref)",
            }
        try:
            record = self.provider.charge(
                amount_pence=amount,
                currency=currency,
                payee=payee,
                card_ref=card_ref,
                idempotency_key=idempotency_key,
            )
        except Exception as e:
            return {
                "success": False,
                "action_taken": CHARGE_CAPABILITY,
                "error": f"issuer rejected: {e}",
            }
        return {
            "success": True,
            "action_taken": CHARGE_CAPABILITY,
            "charge_id": record["id"],
            "amount_pence": record["amount_pence"],
            "currency": record["currency"],
            "payee": record["payee"],
        }

    async def verify(
        self,
        charge_id: str,
        expected_amount_pence: int,
        expected_payee: str,
    ) -> dict[str, Any]:
        record = self.provider.get_charge(charge_id)
        if not record:
            return {
                "verified": False,
                "actual_state": "not_found",
                "charge_id": charge_id,
            }
        amount_ok = record["amount_pence"] == expected_amount_pence
        payee_ok = record["payee"] == expected_payee
        status_ok = record.get("status") == "succeeded"
        return {
            "verified": amount_ok and payee_ok and status_ok,
            "actual_state": record.get("status", "unknown"),
            "actual_amount_pence": record["amount_pence"],
            "actual_payee": record["payee"],
            "charge_id": charge_id,
        }


def create_fake_payments_sub_agent(provider: FakePaymentProvider | None = None) -> FakePaymentsSubAgent:
    return FakePaymentsSubAgent(provider=provider or FakePaymentProvider())
