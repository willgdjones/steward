"""1Password CLI credential resolver. Executor-side only — never in LLM process."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from steward.rules import CredentialScopeDecl


def is_op_reference(value: str) -> bool:
    return value.startswith("op://")


def resolve_op_reference(ref: str) -> str:
    """Resolve via `op read`. Never log the return value."""
    if not is_op_reference(ref):
        raise ValueError(f"not an op:// reference: {ref}")
    try:
        result = subprocess.run(
            ["op", "read", ref],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        if any(s in stderr for s in ("not signed in", "session expired", "locked")):
            raise RuntimeError(f"vault locked or not signed in — cannot resolve {ref}") from e
        raise RuntimeError(f"failed to resolve {ref}: {stderr.strip()}") from e
    except FileNotFoundError as e:
        raise RuntimeError("op CLI not found on PATH") from e


def is_vault_unlocked() -> bool:
    try:
        subprocess.run(
            ["op", "whoami"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


class CredentialResolver(Protocol):
    def resolve(self, ref: str) -> str: ...
    def is_unlocked(self) -> bool: ...


@dataclass
class OpResolver:
    def resolve(self, ref: str) -> str:
        return resolve_op_reference(ref)

    def is_unlocked(self) -> bool:
        return is_vault_unlocked()


@dataclass
class ScopeCheck:
    allowed: bool
    reason: str | None = None


def check_credential_scopes(
    action: str,
    scopes: list[CredentialScopeDecl],
    resolver: CredentialResolver,
) -> ScopeCheck:
    scope = next((s for s in scopes if s.action == action), None)
    if scope is None:
        return ScopeCheck(allowed=True)
    if not resolver.is_unlocked():
        return ScopeCheck(allowed=False, reason="vault is locked — cannot resolve credentials")
    for ref in scope.refs:
        try:
            resolver.resolve(ref)
        except Exception as e:
            return ScopeCheck(allowed=False, reason=str(e))
    return ScopeCheck(allowed=True)
