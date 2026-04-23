"""One-off OAuth bootstrap for the real Gmail provider.

Usage:
    python -m steward.gmail.oauth bootstrap --client-id ID --client-secret SECRET

Opens your default browser, runs the OAuth consent flow on a localhost
redirect, prints the refresh token on completion. You then store that
refresh token in 1Password under `op://vault/gmail/refresh_token` (or
whatever ref your principles.md declares).

This script is the only place a plaintext refresh token appears. The
executor never reads it from disk — it resolves the op:// ref at startup
and uses it in memory only.
"""
from __future__ import annotations

import argparse
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from steward.gmail.real import GMAIL_SCOPES


def bootstrap(client_id: str, client_secret: str) -> str:
    """Run the OAuth consent flow and return the refresh token."""
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=GMAIL_SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise RuntimeError(
            "OAuth returned no refresh token. Make sure access_type=offline and "
            "prompt=consent — and that this is the first time you've authorized "
            "this client."
        )
    return creds.refresh_token


def main() -> None:
    parser = argparse.ArgumentParser(prog="steward.gmail.oauth")
    sub = parser.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("bootstrap", help="Run OAuth consent; print refresh token")
    bp.add_argument("--client-id", required=True, help="OAuth client_id (sensitive — you will want this in 1Password)")
    bp.add_argument("--client-secret", required=True, help="OAuth client_secret (sensitive — same)")

    args = parser.parse_args()

    if args.cmd == "bootstrap":
        try:
            refresh_token = bootstrap(args.client_id, args.client_secret)
        except Exception as e:
            print(f"OAuth failed: {e}", file=sys.stderr)
            sys.exit(1)
        # The refresh token MUST be stored in 1Password, not on disk. Print
        # once so you can paste it in and nothing lingers in shell history
        # if you invoke with `read -s` / similar.
        print("---")
        print("Refresh token (store this immediately in 1Password):")
        print(refresh_token)
        print("---")
        print("Suggested 1Password path: op://vault/gmail/refresh_token")
        print("After storing, declare in principles.md:")
        print("  credential_scopes:")
        print("    - action: archive")
        print("      refs:")
        print("        - op://vault/gmail/client_id")
        print("        - op://vault/gmail/client_secret")
        print("        - op://vault/gmail/refresh_token")


if __name__ == "__main__":
    main()
