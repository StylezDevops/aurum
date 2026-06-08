"""GmailApiReader — read recent mail via the Gmail API (OAuth), same interface as the IMAP reader.

Drops straight into TwoFactorWatcher (transport-agnostic): `fetch_recent() -> List[EmailMessage]`,
identical shape to aurum.sensors.gmail_2fa.MailboxReader, so the extractor + watcher are unchanged.

Secret-by-reference: the refresh token lives in `secrets/gmail_token.json` (gitignored, written
once by scripts/gmail_consent.py) or the vault, loaded LAZILY. The google libraries are imported
lazily too, so `import aurum.sensors` works without them and the test suite needs none (tests
inject a fake Gmail service). Fail-safe: any API error yields an empty list (retry next wake),
never a crash.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import base64
from typing import Any, List, Optional

from .gmail_2fa import EmailMessage

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _b64url(data: str) -> str:
    if not data:
        return ""
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", "replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    """Best-effort plain-text body from a Gmail API payload (prefer text/plain; walk nested parts)."""
    if payload.get("mimeType") == "text/plain":
        return _b64url((payload.get("body") or {}).get("data", ""))
    plain, html = "", ""
    for part in payload.get("parts") or []:
        mime = part.get("mimeType", "")
        if mime == "text/plain" and not plain:
            plain = _b64url((part.get("body") or {}).get("data", ""))
        elif mime == "text/html" and not html:
            html = _b64url((part.get("body") or {}).get("data", ""))
        elif part.get("parts") and not plain:
            plain = _extract_body(part)
    return plain or html or _b64url((payload.get("body") or {}).get("data", ""))


def parse_gmail_message(msg: dict) -> EmailMessage:
    payload = msg.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}
    return EmailMessage(subject=headers.get("subject", ""), from_addr=headers.get("from", ""),
                        body=_extract_body(payload), uid=str(msg.get("id", "")))


class GmailApiReader:
    """Gmail-API mailbox reader. Inject `service` (a built Gmail API client) for tests; in prod
    leave it None and it is built lazily from `token_path` (auto-refreshing via the stored
    refresh token). Same `fetch_recent` contract as MailboxReader."""

    def __init__(self, *, token_path: str = "secrets/gmail_token.json",
                 service: Any = None, user_id: str = "me") -> None:
        self._token_path = token_path
        self._service = service
        self._user_id = user_id

    def _get_service(self) -> Any:
        if self._service is None:
            from google.oauth2.credentials import Credentials      # lazy
            from googleapiclient.discovery import build            # lazy
            creds = Credentials.from_authorized_user_file(self._token_path, SCOPES)
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def fetch_recent(self, *, from_addr: Optional[str] = None,
                     subject_contains: Optional[str] = None, unseen_only: bool = True,
                     limit: int = 10) -> List[EmailMessage]:
        try:
            svc = self._get_service()
            terms: List[str] = []
            if unseen_only:
                terms.append("is:unread")
            if from_addr:
                terms.append(f"from:{from_addr}")
            if subject_contains:
                terms.append(f'subject:"{subject_contains}"')
            resp = svc.users().messages().list(
                userId=self._user_id, q=" ".join(terms), maxResults=limit).execute()
            out: List[EmailMessage] = []
            for ref in resp.get("messages", []):
                full = svc.users().messages().get(
                    userId=self._user_id, id=ref["id"], format="full").execute()
                out.append(parse_gmail_message(full))
            return out                              # Gmail returns newest-first
        except Exception:
            return []                               # fail-safe: missed read, retry next wake
