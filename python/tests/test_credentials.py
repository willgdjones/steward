from dataclasses import dataclass
from typing import Callable

from steward.credentials import check_credential_scopes, is_op_reference
from steward.rules import CredentialScopeDecl


@dataclass
class FakeResolver:
    _resolve: Callable[[str], str]
    _unlocked: bool

    def resolve(self, ref: str) -> str:
        return self._resolve(ref)

    def is_unlocked(self) -> bool:
        return self._unlocked


def test_is_op_reference_recognises_op_prefix():
    assert is_op_reference("op://vault/item/field") is True


def test_is_op_reference_rejects_non_op():
    assert is_op_reference("https://example.com") is False
    assert is_op_reference("plaintext-token") is False


def _scopes():
    return [CredentialScopeDecl(action="send_draft", refs=["op://vault/gmail/refresh_token"])]


def test_allows_action_with_no_scope():
    r = FakeResolver(_resolve=lambda ref: f"resolved-{ref}", _unlocked=True)
    result = check_credential_scopes("archive", _scopes(), r)
    assert result.allowed is True


def test_allows_when_unlocked_and_resolves():
    r = FakeResolver(_resolve=lambda ref: f"resolved-{ref}", _unlocked=True)
    result = check_credential_scopes("send_draft", _scopes(), r)
    assert result.allowed is True


def test_refuses_when_locked():
    def raise_locked(_):
        raise RuntimeError("vault locked")

    r = FakeResolver(_resolve=raise_locked, _unlocked=False)
    result = check_credential_scopes("send_draft", _scopes(), r)
    assert result.allowed is False
    assert "locked" in result.reason


def test_refuses_when_ref_fails_to_resolve():
    def fail(ref):
        raise RuntimeError(f"cannot resolve {ref}")

    r = FakeResolver(_resolve=fail, _unlocked=True)
    result = check_credential_scopes("send_draft", _scopes(), r)
    assert result.allowed is False
    assert "cannot resolve" in result.reason
