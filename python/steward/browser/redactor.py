"""Defense-in-depth redactor for browser sub-agent artefacts.

Why this exists: the executor resolves `op://` credentials at dispatch time and
hands them to the browser sub-agent only long enough to fill a login form. They
*should* never round-trip back into extracted text. But sites occasionally echo
a username/email into a DOM label, or a misconfigured error page dumps the form
values. This module is the last guard before an outcome is journaled or returned
to the HTTP client: exact-match scrub of known-resolved credential strings.

Known limitations (accepted for this slice, see issue 015):
- Case-sensitive: we do not lowercase. Passwords are case-sensitive and
  lowercasing prose would create false positives.
- Exact substring match only. If a page reflects a truncated fragment
  (e.g. "SuperSecret" instead of "SuperSecret99"), we miss it.
- Very short credential strings are skipped to avoid matching common
  words. A 3-char password will not be scrubbed; callers should warn.
- Screenshots, network logs, and saved download files are *not* handled here
  — they don't exist yet (no real browser integration). Must be wired up when
  real browser artefacts appear.
"""
from __future__ import annotations

from typing import Any

MIN_CRED_LEN = 4
REDACTED = "[REDACTED]"

# Fields in a browser outcome dict that may carry reflected credential values.
# Keep explicit; don't walk arbitrary depth — we want every site-reachable field
# to be obvious in this file.
_STRING_FIELDS = ("pageTitle", "textContent", "error", "url", "actual_url", "actual_title")


def redact_string(value: str, resolved_creds: list[str]) -> str:
    """Replace any occurrence of a resolved-credential string with [REDACTED]."""
    out = value
    for cred in resolved_creds:
        if len(cred) < MIN_CRED_LEN:
            continue
        out = out.replace(cred, REDACTED)
    return out


def redact_browser_outcome(outcome: dict[str, Any], resolved_creds: list[str]) -> dict[str, Any]:
    """Return a new outcome dict with any reflected credential values scrubbed.

    Walks only known string fields; structured `extracted` dict values are
    scrubbed one level deep (keys are kept verbatim).
    """
    result: dict[str, Any] = dict(outcome)
    for field in _STRING_FIELDS:
        v = result.get(field)
        if isinstance(v, str):
            result[field] = redact_string(v, resolved_creds)

    extracted = result.get("extracted")
    if isinstance(extracted, dict):
        scrubbed: dict[str, Any] = {}
        for k, v in extracted.items():
            if isinstance(v, str):
                scrubbed[k] = redact_string(v, resolved_creds)
            else:
                scrubbed[k] = v
        result["extracted"] = scrubbed

    return result
