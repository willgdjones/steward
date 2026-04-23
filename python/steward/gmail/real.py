"""Real Gmail provider. Backed by google-api-python-client.

Credential flow (design.md §15):
- `client_id`, `client_secret`, `refresh_token` all come from 1Password refs
  resolved at startup. Client code never sees them as literals in source.
- Access tokens are refreshed on the fly by google-auth; never persisted
  to disk by us.
- The Gmail API client is constructed once and reused for the server's
  lifetime.

Translation (this module's job):
- Gmail API message → our flat dict shape.
- Labels `UNREAD` and `INBOX` drive our `unread` / `archived` flags.
- Body pulled from `snippet` for simplicity; recursive MIME-part decode is
  deferred until we need full body text for a specific feature.

What's NOT here:
- The OAuth bootstrap dance. That's `steward.gmail.oauth` — a one-off
  manual script that writes the refresh token, which this module then
  consumes.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_credentials(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Credentials:
    """Construct a Credentials object from the three op://-resolved strings.

    Credentials auto-refreshes the access token on first API call."""
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=GMAIL_SCOPES,
    )


def _header(headers: list[dict[str, str]], name: str) -> str:
    target = name.lower()
    for h in headers:
        if h.get("name", "").lower() == target:
            return h.get("value", "")
    return ""


def gmail_message_to_dict(msg: dict[str, Any]) -> dict[str, Any]:
    """Translate a Gmail users.messages.get response to our flat dict shape."""
    label_ids = msg.get("labelIds") or []
    payload = msg.get("payload") or {}
    headers = payload.get("headers") or []
    return {
        "id": msg["id"],
        "from": _header(headers, "From"),
        "subject": _header(headers, "Subject"),
        "body": msg.get("snippet") or "",
        "unread": "UNREAD" in label_ids,
        "archived": "INBOX" not in label_ids,
    }


def gmail_draft_to_dict(draft: dict[str, Any]) -> dict[str, Any]:
    """Translate a Gmail users.drafts.get response to our flat draft dict."""
    message = draft.get("message") or {}
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    # Decode body if present (optional; snippet is a safer fallback)
    body_part = payload.get("body") or {}
    body_data = body_part.get("data", "")
    try:
        decoded = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace") if body_data else ""
    except Exception:
        decoded = ""
    return {
        "id": draft["id"],
        "inReplyTo": _header(headers, "In-Reply-To") or message.get("threadId", ""),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "body": decoded or message.get("snippet") or "",
        "sent": False,  # Drafts listed via drafts.list are unsent by definition.
    }


def _build_raw_message(to: str, subject: str, body: str, in_reply_to_msg_id: str | None = None) -> str:
    """Build a base64url-encoded RFC-2822 message suitable for drafts.create."""
    lines = [
        f"To: {to}",
        f"Subject: {subject}",
        "Content-Type: text/plain; charset=UTF-8",
        "MIME-Version: 1.0",
    ]
    if in_reply_to_msg_id:
        lines.append(f"In-Reply-To: {in_reply_to_msg_id}")
        lines.append(f"References: {in_reply_to_msg_id}")
    raw = "\r\n".join(lines) + "\r\n\r\n" + body
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


@dataclass
class RealGmail:
    """Gmail provider implementation.

    Takes an already-built service (Resource) so tests can inject a mock
    without importing googleapiclient at test time. Call `from_credentials`
    to build a live one.
    """

    service: Any  # googleapiclient Resource — typed as Any to keep the mock-injection ergonomic
    user_id: str = "me"

    @classmethod
    def from_credentials(
        cls,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> "RealGmail":
        creds = build_credentials(client_id, client_secret, refresh_token)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return cls(service=service)

    # ---------- read path ----------

    def search(self, query: str) -> list[dict[str, Any]]:
        """users.messages.list then users.messages.get for each ID.

        Two round trips per message; fine for the modest queue sizes the
        planner works with. Cap at 50 to stop runaway refills.
        """
        try:
            listing = (
                self.service.users()
                .messages()
                .list(userId=self.user_id, q=query, maxResults=50)
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"gmail list failed: {e}") from e

        ids = [m["id"] for m in (listing.get("messages") or [])]
        out: list[dict[str, Any]] = []
        for mid in ids:
            msg = self._get_raw(mid)
            if msg is not None:
                out.append(gmail_message_to_dict(msg))
        return out

    def _get_raw(self, message_id: str) -> dict[str, Any] | None:
        try:
            return (
                self.service.users()
                .messages()
                .get(userId=self.user_id, id=message_id, format="metadata",
                     metadataHeaders=["From", "Subject"])
                .execute()
            )
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or "404" in str(e):
                return None
            raise RuntimeError(f"gmail get failed: {e}") from e

    def get_by_id(self, message_id: str) -> dict[str, Any] | None:
        msg = self._get_raw(message_id)
        if msg is None:
            return None
        return gmail_message_to_dict(msg)

    # ---------- write path ----------

    def archive(self, message_id: str) -> bool:
        """Remove the INBOX label — Gmail's definition of archived."""
        try:
            self.service.users().messages().modify(
                userId=self.user_id,
                id=message_id,
                body={"removeLabelIds": ["INBOX"]},
            ).execute()
            return True
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or "404" in str(e):
                return False
            raise RuntimeError(f"gmail archive failed: {e}") from e

    def create_draft(self, in_reply_to: str, body: str) -> dict[str, Any] | None:
        """Create a draft reply. Pulls the original message headers so the
        draft has the right To/Subject and In-Reply-To for threading."""
        original = self._get_full_message(in_reply_to)
        if original is None:
            return None
        headers = (original.get("payload") or {}).get("headers") or []
        to = _header(headers, "From")
        subject = _header(headers, "Subject") or ""
        if subject and not subject.startswith("Re: "):
            subject = f"Re: {subject}"
        msg_id_header = _header(headers, "Message-ID")
        raw = _build_raw_message(to=to, subject=subject, body=body, in_reply_to_msg_id=msg_id_header)
        try:
            draft = self.service.users().drafts().create(
                userId=self.user_id,
                body={"message": {"raw": raw, "threadId": original.get("threadId")}},
            ).execute()
        except HttpError as e:
            raise RuntimeError(f"gmail drafts.create failed: {e}") from e
        return {
            "id": draft["id"],
            "inReplyTo": in_reply_to,
            "to": to,
            "subject": subject,
            "body": body,
            "sent": False,
        }

    def _get_full_message(self, message_id: str) -> dict[str, Any] | None:
        try:
            return (
                self.service.users()
                .messages()
                .get(userId=self.user_id, id=message_id, format="full")
                .execute()
            )
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or "404" in str(e):
                return None
            raise RuntimeError(f"gmail get(full) failed: {e}") from e

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        try:
            draft = (
                self.service.users()
                .drafts()
                .get(userId=self.user_id, id=draft_id, format="full")
                .execute()
            )
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or "404" in str(e):
                return None
            raise RuntimeError(f"gmail drafts.get failed: {e}") from e
        return gmail_draft_to_dict(draft)

    def list_drafts(self) -> list[dict[str, Any]]:
        try:
            listing = self.service.users().drafts().list(userId=self.user_id).execute()
        except HttpError as e:
            raise RuntimeError(f"gmail drafts.list failed: {e}") from e
        out: list[dict[str, Any]] = []
        for ref in listing.get("drafts") or []:
            d = self.get_draft(ref["id"])
            if d is not None:
                out.append(d)
        return out

    def send_draft(self, draft_id: str) -> dict[str, Any] | None:
        """drafts.send marks the draft as sent. Returns the sent message
        metadata wrapped in our draft dict shape so the sub-agent's verify
        step can check `sent=True`."""
        # Fetch the pre-send draft so we can reconstruct the return shape.
        pre = self.get_draft(draft_id)
        if pre is None:
            return None
        try:
            self.service.users().drafts().send(
                userId=self.user_id,
                body={"id": draft_id},
            ).execute()
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or "404" in str(e):
                return None
            raise RuntimeError(f"gmail drafts.send failed: {e}") from e
        pre["sent"] = True
        return pre
