"""Post-hoc verifier: detect user-unarchive and reply-after-archive anomalies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from steward.gmail.fake import FakeGmail
from steward.journal import read_journal

AnomalyType = Literal["unarchive", "reply_after_archive"]


@dataclass
class Anomaly:
    type: AnomalyType
    messageId: str
    goalId: str
    title: str
    description: str


async def detect_anomalies(journal_path: str, gmail: FakeGmail) -> list[Anomaly]:
    entries = read_journal(journal_path)

    reported: set[str] = set()
    for e in entries:
        if e.get("kind") == "verifier_anomaly" and isinstance(e.get("goalId"), str):
            reported.add(e["goalId"])

    actions = [e for e in entries if e.get("kind") == "action" and e.get("goalId") not in reported]
    anomalies: list[Anomaly] = []

    for entry in actions:
        goal_id = entry.get("goalId", "")
        title = entry.get("title", "") or ""
        msg_ids = entry.get("messageIds") if isinstance(entry.get("messageIds"), list) else [entry.get("messageId")]

        for msg_id in msg_ids:
            if not msg_id:
                continue
            msg = gmail.get_by_id(msg_id)
            if not msg:
                continue

            archived = msg.get("archived")
            if archived is False or (archived is None and msg.get("unread")):
                anomalies.append(
                    Anomaly(
                        type="unarchive",
                        messageId=msg_id,
                        goalId=goal_id,
                        title=title,
                        description=f'Message "{msg["subject"]}" was archived but has been unarchived by the user.',
                    )
                )
                continue

            if archived is True:
                all_messages = gmail.load()
                sender = msg["from"]
                sender_domain = sender.split("@", 1)[1] if "@" in sender else sender
                subject_root = msg["subject"].lower()
                if subject_root.startswith("re:"):
                    subject_root = subject_root[3:].strip()

                def is_reply(m: dict) -> bool:
                    if m["id"] == msg_id or not m.get("unread") or m.get("archived"):
                        return False
                    m_from = m["from"]
                    m_domain = m_from.split("@", 1)[1] if "@" in m_from else m_from
                    same_sender = m_from == sender or m_domain == sender_domain
                    same_thread = subject_root in m["subject"].lower()
                    return same_sender and same_thread

                if any(is_reply(m) for m in all_messages):
                    anomalies.append(
                        Anomaly(
                            type="reply_after_archive",
                            messageId=msg_id,
                            goalId=goal_id,
                            title=title,
                            description=f'A reply appeared in the thread after archiving "{msg["subject"]}" — should the archive rule be reconsidered?',
                        )
                    )

    return anomalies
