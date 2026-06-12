"""Event-driven Gmail/IMAP 2FA-code capture — credential-agnostic, secret-by-reference.

The v1 "Gmail + 2FA capture" capability. A login (e.g. the 24KR pipeline's Label Engine submit)
hits a 2FA challenge; the code lands in a mailbox 5–7 min later. Because the cage is ephemeral
(`docker run --rm` per turn), Aurum CANNOT block a turn waiting — so capture is EVENT-DRIVEN:

  • `check_once()` — the cage-friendly primitive: one mailbox read + extract; returns a fresh
    code or None. A host/SEN watcher (or cron) wakes Aurum to call it; a later wake retries.
  • `wait_for_code()` — a BOUNDED blocking poll for a standing (non-cage) context or tests;
    built on `check_once`, it never blocks forever (max_attempts × poll_interval, e.g. 7 min).

CREDENTIAL-AGNOSTIC + SECRET-BY-REFERENCE: the mailbox app-password (or token) is INJECTED at
construction (resolved from a `secret_ref` by OneCLI upstream), never embedded, never logged. The
durable path is IMAP + app-password (no OAuth 7-day-testing-token death — the pipeline's existing
OAuth token probed DEAD/invalid_grant, confirming that fragility). The IMAP connection is
INJECTABLE so the capture logic is fully testable without a live mailbox.
"""
from __future__ import annotations

import email
import logging
import re
import time
from dataclasses import dataclass
from email.message import Message
from typing import Any, Callable, List, Optional, Sequence

logger = logging.getLogger(__name__)

from ..durability.clock import execution_now  # noqa: F401 (documents: poll waits are EXECUTION time)

# Default keywords that typically precede a 2FA/OTP code in an email. Proximity to one of these
# disambiguates the real code from incidental digit runs.
_DEFAULT_KEYWORDS: Sequence[str] = (
    "code", "verification", "verify", "otp", "passcode", "security", "one-time", "2fa", "two-factor",
)


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    from_addr: str
    body: str
    uid: str = ""


class TwoFactorExtractor:
    """Extract an N-digit 2FA code from email text. Off the EL/PK fast path (this is capture, not
    the ledger hot path), so `re` is fine — and the pattern is anchored with no nested quantifiers,
    so it is linear-time / ReDoS-free regardless. Prefers a code adjacent to a keyword to avoid
    grabbing an incidental number; never returns part of a longer digit run."""

    def __init__(self, code_length: int = 6,
                 keywords: Sequence[str] = _DEFAULT_KEYWORDS, proximity: int = 40) -> None:
        self._n = int(code_length)
        self._keywords = tuple(k.lower() for k in keywords)
        self._proximity = int(proximity)
        # (?<!\d)\d{N}(?!\d): exactly N digits, not embedded in a longer number. Linear.
        self._re = re.compile(rf"(?<!\d)(\d{{{self._n}}})(?!\d)")

    def extract(self, text: str) -> Optional[str]:
        if not text:
            return None
        candidates = [(m.start(), m.group(1)) for m in self._re.finditer(text)]
        if not candidates:
            return None
        low = text.lower()
        for pos, code in candidates:
            window = low[max(0, pos - self._proximity):pos]
            if any(k in window for k in self._keywords):
                return code        # keyword-adjacent → the real code
        return candidates[0][1]    # fallback: first N-digit code


def _message_body(msg: Message) -> str:
    """Best-effort plain-text body (walk multipart; prefer text/plain, fall back to text/html)."""
    if msg.is_multipart():
        plain, html = "", ""
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and not plain:
                plain = _decode(part)
            elif ctype == "text/html" and not html:
                html = _decode(part)
        return plain or html
    return _decode(msg)


def _decode(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return str(part.get_payload() or "")
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""


class MailboxReader:
    """Reads recent messages over IMAP. The app-password is INJECTED (resolved from a secret_ref
    upstream) and used only to open the connection — never stored beyond what the connection needs,
    never logged. `connect` is injectable (default: a real imaplib SSL login) so capture logic is
    testable with a fake mailbox."""

    last_error: Optional[Exception] = None   # last fetch failure (None = clean); inspectable

    def __init__(self, *, user: str, app_password: str, host: str = "imap.gmail.com",
                 port: int = 993, mailbox: str = "INBOX",
                 connect: Optional[Callable[[], Any]] = None) -> None:
        self._user = user
        self._app_password = app_password   # injected secret; used only in _default_connect
        self._host = host
        self._port = port
        self._mailbox = mailbox
        self._connect = connect or self._default_connect
        self.last_error = None

    def _default_connect(self) -> Any:  # pragma: no cover - needs a live mailbox
        import imaplib
        conn = imaplib.IMAP4_SSL(self._host, self._port)
        conn.login(self._user, self._app_password)
        return conn

    def fetch_recent(self, *, from_addr: Optional[str] = None,
                     subject_contains: Optional[str] = None, unseen_only: bool = True,
                     limit: int = 10) -> List[EmailMessage]:
        """Newest-first recent messages matching the filters. Fail-safe: any IMAP error yields an
        empty list (a missed read just means 'retry next wake'), never a crash."""
        conn = None
        try:
            conn = self._connect()           # inside the try: a failed LOGIN is caught + surfaced,
            conn.select(self._mailbox)        # not propagated uncaught (it logs in here)
            criteria: List[str] = []
            if unseen_only:
                criteria.append("UNSEEN")
            if from_addr:
                criteria += ["FROM", from_addr]
            if subject_contains:
                criteria += ["SUBJECT", subject_contains]
            typ, data = conn.search(None, *(criteria or ["ALL"]))
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-limit:]
            out: List[EmailMessage] = []
            for uid in reversed(uids):           # newest first
                typ, md = conn.fetch(uid, "(RFC822)")
                if typ != "OK" or not md or not isinstance(md[0], tuple):
                    continue
                msg = email.message_from_bytes(md[0][1])
                out.append(EmailMessage(
                    subject=str(msg.get("Subject", "")),
                    from_addr=str(msg.get("From", "")),
                    body=_message_body(msg),
                    uid=uid.decode() if isinstance(uid, bytes) else str(uid)))
            self.last_error = None
            return out
        except Exception as e:
            # Fail-safe (return [] → retry) but NOT silent: a revoked app-password / failed LOGIN
            # raises here and previously looked identical to an empty inbox. Log it + expose
            # `last_error` so a persistent auth failure is visible, not a phantom 'no 2FA email'.
            self.last_error = e
            logger.error("MailboxReader.fetch_recent failed (%s: %s) — returning no mail. If this "
                         "persists it is likely IMAP auth (a revoked app-password), not an empty "
                         "inbox.", type(e).__name__, e)
            return []
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass


class TwoFactorWatcher:
    """Event-driven 2FA capture over a MailboxReader + TwoFactorExtractor."""

    def __init__(self, reader: MailboxReader, extractor: Optional[TwoFactorExtractor] = None, *,
                 poll_interval_s: float = 15.0, max_attempts: int = 28,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        # 28 × 15s ≈ 7 min — covers the 5–7 min 2FA window. EXECUTION-time waits (never Domain).
        self._reader = reader
        self._extractor = extractor or TwoFactorExtractor()
        self._poll_interval_s = float(poll_interval_s)
        self._max_attempts = int(max_attempts)
        self._sleeper = sleeper

    def check_once(self, *, from_addr: Optional[str] = None,
                   subject_contains: Optional[str] = None) -> Optional[str]:
        """The cage-friendly primitive: ONE mailbox read + extract. Returns a code or None — the
        turn ends either way; a later wake retries. Does not block."""
        for m in self._reader.fetch_recent(from_addr=from_addr, subject_contains=subject_contains):
            code = self._extractor.extract(f"{m.subject}\n{m.body}")
            if code:
                return code
        return None

    def wait_for_code(self, *, from_addr: Optional[str] = None,
                      subject_contains: Optional[str] = None) -> Optional[str]:
        """BOUNDED blocking poll (standing-context / test only — NOT for inside a cage turn).
        Returns the code as soon as it appears, or None after max_attempts. Never infinite."""
        for attempt in range(self._max_attempts):
            code = self.check_once(from_addr=from_addr, subject_contains=subject_contains)
            if code is not None:
                return code
            if attempt < self._max_attempts - 1:
                self._sleeper(self._poll_interval_s)
        return None
