"""Live smoke for Gmail 2FA capture — HOST-side, manual. Reads the consented inbox ONCE and tries
to extract a 2FA code, so you can confirm the whole path works against the real mailbox.

Run AFTER `python scripts/gmail_consent.py` has written secrets/gmail_token.json:
    python scripts/gmail_smoke.py
    python scripts/gmail_smoke.py --from no-reply@labelengine.com --subject code
    python scripts/gmail_smoke.py --all          # include already-read mail (not just unread)

This prints the extracted code (your own, short-lived). It only READS (gmail.readonly).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the aurum package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aurum"))


def main() -> int:
    ap = argparse.ArgumentParser(description="One-shot live Gmail 2FA capture smoke.")
    ap.add_argument("--token", default="secrets/gmail_token.json")
    ap.add_argument("--from", dest="from_addr", default=None, help="filter by sender")
    ap.add_argument("--subject", default=None, help="filter by subject substring")
    ap.add_argument("--all", action="store_true", help="include already-read mail too")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    if not Path(args.token).exists():
        print(f"ERROR: {args.token} not found — run scripts/gmail_consent.py first.", file=sys.stderr)
        return 2
    try:
        from aurum.sensors import GmailApiReader, TwoFactorWatcher
    except Exception as e:  # noqa: BLE001
        print(f"ERROR importing aurum.sensors: {e}", file=sys.stderr)
        return 2

    reader = GmailApiReader(token_path=args.token)
    msgs = reader.fetch_recent(from_addr=args.from_addr, subject_contains=args.subject,
                               unseen_only=not args.all, limit=args.limit)
    print(f"matched {len(msgs)} message(s):")
    for m in msgs:
        print(f"  - {m.subject!r}  from {m.from_addr!r}")
    code = TwoFactorWatcher(reader).check_once(from_addr=args.from_addr,
                                               subject_contains=args.subject)
    print("EXTRACTED CODE:", code if code else "(none found — send yourself a code email and retry)")
    return 0 if code else 1


if __name__ == "__main__":
    raise SystemExit(main())
