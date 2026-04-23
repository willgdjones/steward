"""Unit tests for the real Gmail adapter. No live API — service is mocked.

The mock mirrors the googleapiclient chained-call pattern:
    service.users().messages().list(...).execute()
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from steward.gmail.real import (
    GMAIL_SCOPES,
    RealGmail,
    _build_raw_message,
    build_credentials,
    gmail_draft_to_dict,
    gmail_message_to_dict,
)


# ---------- pure translation helpers ----------


def test_gmail_message_to_dict_basic():
    raw = {
        "id": "m-1",
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
        "snippet": "Hello there",
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "Subject", "value": "Hi"},
                {"name": "Date", "value": "ignored"},
            ],
        },
    }
    out = gmail_message_to_dict(raw)
    assert out == {
        "id": "m-1",
        "from": "alice@example.com",
        "subject": "Hi",
        "body": "Hello there",
        "unread": True,
        "archived": False,
    }


def test_gmail_message_to_dict_archived_when_no_inbox_label():
    raw = {
        "id": "m-2",
        "labelIds": ["IMPORTANT"],  # no INBOX
        "snippet": "",
        "payload": {"headers": [{"name": "From", "value": "x@y"}]},
    }
    out = gmail_message_to_dict(raw)
    assert out["archived"] is True
    assert out["unread"] is False


def test_gmail_message_to_dict_header_case_insensitive():
    raw = {
        "id": "m-3",
        "labelIds": ["INBOX"],
        "snippet": "",
        "payload": {"headers": [{"name": "from", "value": "alice@x.y"}]},  # lowercase
    }
    assert gmail_message_to_dict(raw)["from"] == "alice@x.y"


def test_gmail_message_to_dict_missing_headers_defaults():
    raw = {"id": "m-4", "labelIds": [], "snippet": "", "payload": {}}
    out = gmail_message_to_dict(raw)
    assert out["from"] == ""
    assert out["subject"] == ""


def test_gmail_draft_to_dict_decodes_body():
    body_text = "Thanks for your email!"
    body_b64 = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
    raw = {
        "id": "r-1",
        "message": {
            "threadId": "thread-1",
            "snippet": body_text[:50],
            "payload": {
                "headers": [
                    {"name": "To", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Re: Hi"},
                    {"name": "In-Reply-To", "value": "<msg-id@x>"},
                ],
                "body": {"data": body_b64},
            },
        },
    }
    out = gmail_draft_to_dict(raw)
    assert out["id"] == "r-1"
    assert out["to"] == "alice@example.com"
    assert out["subject"] == "Re: Hi"
    assert out["inReplyTo"] == "<msg-id@x>"
    assert out["body"] == body_text
    assert out["sent"] is False


def test_gmail_draft_to_dict_falls_back_to_snippet_when_body_missing():
    raw = {
        "id": "r-2",
        "message": {
            "threadId": "t",
            "snippet": "fallback",
            "payload": {"headers": [{"name": "To", "value": "x@y"}]},
        },
    }
    out = gmail_draft_to_dict(raw)
    assert out["body"] == "fallback"


def test_build_raw_message_includes_threading_headers():
    raw_b64 = _build_raw_message(
        to="alice@example.com",
        subject="Re: Hi",
        body="Hello",
        in_reply_to_msg_id="<abc@example.com>",
    )
    decoded = base64.urlsafe_b64decode(raw_b64 + "==").decode("utf-8")
    assert "To: alice@example.com" in decoded
    assert "Subject: Re: Hi" in decoded
    assert "In-Reply-To: <abc@example.com>" in decoded
    assert "References: <abc@example.com>" in decoded
    assert "Hello" in decoded


def test_build_raw_message_no_threading_when_no_msg_id():
    raw_b64 = _build_raw_message(to="x@y", subject="S", body="B")
    decoded = base64.urlsafe_b64decode(raw_b64 + "==").decode("utf-8")
    assert "In-Reply-To" not in decoded
    assert "References" not in decoded


# ---------- credential construction ----------


def test_build_credentials_carries_scopes_and_refresh_token():
    creds = build_credentials("cid", "csecret", "rtok")
    assert creds.client_id == "cid"
    assert creds.client_secret == "csecret"
    assert creds.refresh_token == "rtok"
    assert creds.scopes == GMAIL_SCOPES


# ---------- mock-based service tests ----------


def _mock_service(list_result=None, get_result=None, modify_result=None,
                  drafts_create_result=None, drafts_get_result=None,
                  drafts_list_result=None, drafts_send_result=None):
    """Build a MagicMock that supports the chained calls we make."""
    service = MagicMock()
    users = service.users.return_value
    messages = users.messages.return_value
    drafts = users.drafts.return_value
    if list_result is not None:
        messages.list.return_value.execute.return_value = list_result
    if get_result is not None:
        messages.get.return_value.execute.return_value = get_result
    if modify_result is not None:
        messages.modify.return_value.execute.return_value = modify_result
    if drafts_create_result is not None:
        drafts.create.return_value.execute.return_value = drafts_create_result
    if drafts_get_result is not None:
        drafts.get.return_value.execute.return_value = drafts_get_result
    if drafts_list_result is not None:
        drafts.list.return_value.execute.return_value = drafts_list_result
    if drafts_send_result is not None:
        drafts.send.return_value.execute.return_value = drafts_send_result
    return service


def _msg(id_, from_, subject, labels=("INBOX", "UNREAD"), snippet=""):
    return {
        "id": id_,
        "labelIds": list(labels),
        "snippet": snippet,
        "payload": {"headers": [
            {"name": "From", "value": from_},
            {"name": "Subject", "value": subject},
        ]},
    }


def test_search_lists_and_gets_each_message():
    list_result = {"messages": [{"id": "m-1"}, {"id": "m-2"}]}
    service = _mock_service(list_result=list_result)
    # Chain get to return different results per id
    get_results = {
        "m-1": _msg("m-1", "alice@x.y", "first", snippet="hello"),
        "m-2": _msg("m-2", "bob@x.y", "second", snippet="world"),
    }
    service.users.return_value.messages.return_value.get.return_value.execute.side_effect = \
        lambda: None  # placeholder; we'll swap to a per-call impl
    # Use a side_effect on .get(...) to vary by id
    def get_factory(*, userId, id, **kwargs):
        m = MagicMock()
        m.execute.return_value = get_results[id]
        return m
    service.users.return_value.messages.return_value.get.side_effect = get_factory

    gmail = RealGmail(service=service)
    results = gmail.search("is:unread")
    assert [r["id"] for r in results] == ["m-1", "m-2"]
    assert results[0]["from"] == "alice@x.y"
    assert results[0]["subject"] == "first"
    assert results[0]["unread"] is True


def test_search_empty_listing_returns_empty():
    service = _mock_service(list_result={})
    gmail = RealGmail(service=service)
    assert gmail.search("is:unread") == []


def test_archive_calls_modify_removing_inbox_label():
    service = _mock_service(modify_result={})
    gmail = RealGmail(service=service)
    assert gmail.archive("m-1") is True
    modify = service.users.return_value.messages.return_value.modify
    args, kwargs = modify.call_args
    assert kwargs["userId"] == "me"
    assert kwargs["id"] == "m-1"
    assert kwargs["body"] == {"removeLabelIds": ["INBOX"]}


def test_get_by_id_translates_and_uses_metadata_format():
    service = _mock_service(get_result=_msg("m-1", "a@b", "s", snippet="body"))
    gmail = RealGmail(service=service)
    out = gmail.get_by_id("m-1")
    assert out["id"] == "m-1"
    assert out["body"] == "body"
    get = service.users.return_value.messages.return_value.get
    _, kwargs = get.call_args
    assert kwargs.get("format") == "metadata"


def test_create_draft_builds_threaded_reply():
    # Original message: "alice@example.com — Subject: Hi"
    original = {
        "id": "m-1",
        "threadId": "thread-1",
        "labelIds": ["INBOX"],
        "payload": {"headers": [
            {"name": "From", "value": "alice@example.com"},
            {"name": "Subject", "value": "Hi"},
            {"name": "Message-ID", "value": "<abc@example.com>"},
        ]},
    }
    drafts_create_result = {"id": "r-1", "message": {"id": "m-reply"}}
    service = MagicMock()
    users = service.users.return_value
    messages = users.messages.return_value
    drafts = users.drafts.return_value
    messages.get.return_value.execute.return_value = original
    drafts.create.return_value.execute.return_value = drafts_create_result

    gmail = RealGmail(service=service)
    out = gmail.create_draft("m-1", "Thanks!")
    assert out["id"] == "r-1"
    assert out["to"] == "alice@example.com"
    assert out["subject"] == "Re: Hi"
    assert out["inReplyTo"] == "m-1"
    assert out["body"] == "Thanks!"

    # Verify the raw payload sent to drafts.create
    _, kwargs = drafts.create.call_args
    raw_b64 = kwargs["body"]["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw_b64 + "==").decode("utf-8")
    assert "To: alice@example.com" in decoded
    assert "Re: Hi" in decoded
    assert "In-Reply-To: <abc@example.com>" in decoded
    assert "Thanks!" in decoded
    assert kwargs["body"]["message"]["threadId"] == "thread-1"


def test_create_draft_returns_none_when_original_missing():
    from googleapiclient.errors import HttpError
    service = MagicMock()
    err = HttpError(resp=MagicMock(status=404), content=b"not found")
    service.users.return_value.messages.return_value.get.return_value.execute.side_effect = err
    gmail = RealGmail(service=service)
    assert gmail.create_draft("m-nope", "body") is None


def test_get_draft_translates():
    body_b64 = base64.urlsafe_b64encode(b"Hello").decode("ascii")
    raw = {
        "id": "r-1",
        "message": {
            "threadId": "t",
            "payload": {
                "headers": [
                    {"name": "To", "value": "a@b"},
                    {"name": "Subject", "value": "Re: x"},
                ],
                "body": {"data": body_b64},
            },
        },
    }
    service = _mock_service(drafts_get_result=raw)
    gmail = RealGmail(service=service)
    out = gmail.get_draft("r-1")
    assert out["id"] == "r-1"
    assert out["body"] == "Hello"


def test_list_drafts_fetches_each():
    service = MagicMock()
    drafts_mock = service.users.return_value.drafts.return_value
    drafts_mock.list.return_value.execute.return_value = {"drafts": [{"id": "r-1"}, {"id": "r-2"}]}
    body_b64 = base64.urlsafe_b64encode(b"x").decode("ascii")
    full = {"id": "placeholder", "message": {"threadId": "t", "payload": {"headers": [], "body": {"data": body_b64}}}}
    def get_factory(*, userId, id, **kwargs):
        m = MagicMock()
        m.execute.return_value = {**full, "id": id}
        return m
    drafts_mock.get.side_effect = get_factory
    gmail = RealGmail(service=service)
    out = gmail.list_drafts()
    assert [d["id"] for d in out] == ["r-1", "r-2"]


def test_send_draft_marks_sent():
    body_b64 = base64.urlsafe_b64encode(b"x").decode("ascii")
    pre = {
        "id": "r-1",
        "message": {"threadId": "t", "payload": {"headers": [{"name": "To", "value": "a@b"}], "body": {"data": body_b64}}},
    }
    service = _mock_service(drafts_get_result=pre, drafts_send_result={"id": "m-sent"})
    gmail = RealGmail(service=service)
    out = gmail.send_draft("r-1")
    assert out is not None
    assert out["id"] == "r-1"
    assert out["sent"] is True


def test_send_draft_missing_returns_none():
    from googleapiclient.errors import HttpError
    service = MagicMock()
    err = HttpError(resp=MagicMock(status=404), content=b"not found")
    service.users.return_value.drafts.return_value.get.return_value.execute.side_effect = err
    gmail = RealGmail(service=service)
    assert gmail.send_draft("r-nope") is None


def test_archive_404_returns_false():
    from googleapiclient.errors import HttpError
    service = MagicMock()
    err = HttpError(resp=MagicMock(status=404), content=b"not found")
    service.users.return_value.messages.return_value.modify.return_value.execute.side_effect = err
    gmail = RealGmail(service=service)
    assert gmail.archive("m-nope") is False


def test_get_by_id_404_returns_none():
    from googleapiclient.errors import HttpError
    service = MagicMock()
    err = HttpError(resp=MagicMock(status=404), content=b"not found")
    service.users.return_value.messages.return_value.get.return_value.execute.side_effect = err
    gmail = RealGmail(service=service)
    assert gmail.get_by_id("m-nope") is None


def test_real_gmail_implements_gmail_provider_protocol():
    """Structural check — confirms RealGmail has the methods the rest of the
    executor expects. No assertion beyond 'this doesn't AttributeError'."""
    service = MagicMock()
    gmail = RealGmail(service=service)
    # Protocol surface
    assert callable(gmail.search)
    assert callable(gmail.get_by_id)
    assert callable(gmail.archive)
    assert callable(gmail.create_draft)
    assert callable(gmail.get_draft)
    assert callable(gmail.list_drafts)
    assert callable(gmail.send_draft)
