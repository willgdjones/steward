"""Gmail sub-agent: dispatch + verify for archive, draft_reply, send_draft.

Takes any `GmailProvider` (FakeGmail for tests, RealGmail for production)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from steward.gmail.provider import GmailProvider


@dataclass
class GmailSubAgent:
    gmail: GmailProvider

    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]:
        cap = instruction.get("capability")
        message_id = instruction.get("messageId", "")

        if cap == "archive":
            found = self.gmail.archive(message_id)
            if not found:
                return {
                    "success": False,
                    "action_taken": "archive",
                    "messageId": message_id,
                    "error": f"message not found: {message_id}",
                }
            return {"success": True, "action_taken": "archive", "messageId": message_id}

        if cap == "draft_reply":
            body = instruction.get("draftBody") or instruction.get("instruction", "")
            draft = self.gmail.create_draft(message_id, body)
            if not draft:
                return {
                    "success": False,
                    "action_taken": "draft_reply",
                    "messageId": message_id,
                    "error": f"message not found: {message_id}",
                }
            return {
                "success": True,
                "action_taken": "draft_reply",
                "messageId": message_id,
                "draftId": draft["id"],
            }

        if cap == "send_draft":
            draft_id = instruction.get("draftId")
            if not draft_id:
                return {
                    "success": False,
                    "action_taken": "send_draft",
                    "messageId": message_id,
                    "error": "no draftId provided",
                }
            sent = self.gmail.send_draft(draft_id)
            if not sent:
                return {
                    "success": False,
                    "action_taken": "send_draft",
                    "messageId": message_id,
                    "error": f"draft not found or already sent: {draft_id}",
                }
            return {
                "success": True,
                "action_taken": "send_draft",
                "messageId": message_id,
                "draftId": sent["id"],
            }

        return {
            "success": False,
            "action_taken": cap or "unknown",
            "messageId": message_id,
            "error": f"unknown capability: {cap}",
        }

    async def verify(
        self,
        message_id: str,
        expected_action: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if expected_action == "archive":
            msg = self.gmail.get_by_id(message_id)
            if not msg:
                return {"verified": False, "actual_state": "not_found", "messageId": message_id}
            archived = msg.get("archived") is True
            return {
                "verified": archived,
                "actual_state": "archived" if archived else "not_archived",
                "messageId": message_id,
            }

        if expected_action == "draft_reply":
            draft_id = (meta or {}).get("draftId")
            if not draft_id:
                return {"verified": False, "actual_state": "no_draft_id", "messageId": message_id}
            draft = self.gmail.get_draft(draft_id)
            if not draft:
                return {"verified": False, "actual_state": "draft_not_found", "messageId": message_id}
            return {
                "verified": draft["inReplyTo"] == message_id,
                "actual_state": "draft_exists",
                "messageId": message_id,
            }

        if expected_action == "send_draft":
            draft_id = (meta or {}).get("draftId")
            if not draft_id:
                return {"verified": False, "actual_state": "no_draft_id", "messageId": message_id}
            draft = self.gmail.get_draft(draft_id)
            if not draft:
                return {"verified": False, "actual_state": "draft_not_found", "messageId": message_id}
            sent = draft.get("sent") is True
            return {
                "verified": sent,
                "actual_state": "sent" if sent else "not_sent",
                "messageId": message_id,
            }

        return {"verified": False, "actual_state": "unknown", "messageId": message_id}


def create_gmail_sub_agent(gmail: GmailProvider) -> GmailSubAgent:
    return GmailSubAgent(gmail=gmail)
