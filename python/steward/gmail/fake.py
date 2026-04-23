"""Fake Gmail provider backed by a JSON file. Stand-in for real Gmail OAuth."""
from __future__ import annotations

import json
import random
import string
import time
from pathlib import Path
from typing import Any


class FakeGmail:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._drafts: list[dict[str, Any]] = []

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, messages: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(messages, indent=2))

    def read_one_unread(self) -> dict[str, Any] | None:
        for m in self.load():
            if m.get("unread"):
                return m
        return None

    def search(self, _query: str) -> list[dict[str, Any]]:
        return [m for m in self.load() if m.get("unread") and not m.get("archived")]

    def get_by_id(self, message_id: str) -> dict[str, Any] | None:
        for m in self.load():
            if m["id"] == message_id:
                return m
        return None

    def archive(self, message_id: str) -> bool:
        messages = self.load()
        for m in messages:
            if m["id"] == message_id:
                m["archived"] = True
                self.save(messages)
                return True
        return False

    def create_draft(self, in_reply_to: str, body: str) -> dict[str, Any] | None:
        msg = self.get_by_id(in_reply_to)
        if not msg:
            return None
        draft_id = f"draft-{int(time.time() * 1000)}-" + "".join(
            random.choices(string.ascii_lowercase + string.digits, k=6)
        )
        subject = msg["subject"]
        if not subject.startswith("Re: "):
            subject = f"Re: {subject}"
        draft = {
            "id": draft_id,
            "inReplyTo": in_reply_to,
            "to": msg["from"],
            "subject": subject,
            "body": body,
        }
        self._drafts.append(draft)
        return draft

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        return next((d for d in self._drafts if d["id"] == draft_id), None)

    def list_drafts(self) -> list[dict[str, Any]]:
        return list(self._drafts)

    def send_draft(self, draft_id: str) -> dict[str, Any] | None:
        draft = next((d for d in self._drafts if d["id"] == draft_id), None)
        if not draft:
            return None
        if draft.get("sent"):
            return None
        draft["sent"] = True
        return draft
