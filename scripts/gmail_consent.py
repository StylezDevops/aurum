"""One-time, HOST-side OAuth consent for Gmail 2FA capture (Phase F).

Run this ONCE on your own machine (NOT in the cage). It opens your browser, you approve the
`gmail.readonly` scope for the mailbox, and it writes `secrets/gmail_token.json` — a long-lived
refresh token. After that, Aurum reads the inbox non-interactively via that token (by-reference);
it never re-consents and never runs a browser.

    python scripts/gmail_consent.py
    python scripts/gmail_consent.py --client secrets/gmail_client.json --token secrets/gmail_token.json

Prereqs:
- `pip install -r install-requirements.txt` (needs google-auth-oauthlib).
- The OAuth consent screen must include the `gmail.readonly` scope and be PUBLISHED ("In
  production") — a Testing-mode app's refresh token expires in ~7 days (that is what killed the
  pipeline's old token). You'll click through an "unverified app" screen as the owner; that's fine.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> int:
    ap = argparse.ArgumentParser(description="One-time Gmail OAuth consent (host-side).")
    ap.add_argument("--client", default="secrets/gmail_client.json",
                    help="downloaded OAuth Desktop client JSON")
    ap.add_argument("--token", default="secrets/gmail_token.json",
                    help="where to write the refresh token (gitignored)")
    ap.add_argument("--port", type=int, default=0, help="loopback port (0 = auto)")
    args = ap.parse_args()

    client = Path(args.client)
    token = Path(args.token)
    if not client.exists():
        print(f"ERROR: client JSON not found at {client}. Download a Desktop OAuth client from "
              "console.google.com (APIs & Services -> Credentials) and save it there.", file=sys.stderr)
        return 2
    try:
        data = json.loads(client.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {client} is not valid JSON: {e}", file=sys.stderr)
        return 2
    if not ({"installed", "web"} & set(data)):
        print("ERROR: that JSON is not an OAuth client (expected an 'installed' Desktop app).",
              file=sys.stderr)
        return 2

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: missing dep. Run: pip install -r install-requirements.txt", file=sys.stderr)
        return 2

    print(f"Opening your browser to approve {SCOPES[0]} ...")
    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
    # access_type=offline + prompt=consent guarantee a refresh_token comes back.
    creds = flow.run_local_server(port=args.port, prompt="consent", access_type="offline")
    if not getattr(creds, "refresh_token", None):
        print("ERROR: no refresh_token returned. Re-run; ensure the consent screen is published "
              "and you approved offline access.", file=sys.stderr)
        return 1

    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    print(f"OK - wrote {token} (refresh token saved; gitignored). scopes={list(creds.scopes or [])}")
    print("Done. Aurum can now read the inbox non-interactively by-reference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
