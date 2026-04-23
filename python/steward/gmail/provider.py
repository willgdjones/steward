"""GmailProvider — the surface the executor and sub-agent require.

FakeGmail implements this structurally (Python protocols are duck-typed, no
inheritance needed). RealGmail must match the same surface so the swap is
local: `STEWARD_GMAIL=real` flips one line in __main__.py and nothing else.

Messages are plain dicts keyed identically across providers:
    {"id": str, "from": str, "subject": str, "body": str,
     "unread": bool, "archived": bool}

Drafts:
    {"id": str, "inReplyTo": str, "to": str, "subject": str,
     "body": str, "sent": bool}
"""
from __future__ import annotations

from typing import Any, Protocol


class GmailProvider(Protocol):
    def search(self, query: str) -> list[dict[str, Any]]: ...
    def get_by_id(self, message_id: str) -> dict[str, Any] | None: ...
    def archive(self, message_id: str) -> bool: ...
    def create_draft(self, in_reply_to: str, body: str) -> dict[str, Any] | None: ...
    def get_draft(self, draft_id: str) -> dict[str, Any] | None: ...
    def list_drafts(self) -> list[dict[str, Any]]: ...
    def send_draft(self, draft_id: str) -> dict[str, Any] | None: ...
