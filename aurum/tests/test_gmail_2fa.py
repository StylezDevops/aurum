"""Phase F (Gmail 2FA capture) — event-driven, credential-agnostic 2FA-code capture.

Tests the extractor, the IMAP reader (against a FAKE mailbox — no live creds), and the
event-driven watcher (check_once + a BOUNDED wait_for_code that never blocks for real).
"""
from __future__ import annotations

from typing import List

from aurum.sensors import EmailMessage, MailboxReader, TwoFactorExtractor, TwoFactorWatcher


# ── extractor ────────────────────────────────────────────────────────────────────────────────

def test_extractor_prefers_keyword_adjacent_code():
    ex = TwoFactorExtractor()
    assert ex.extract("Your Label Engine verification code is 482913. Expires soon.") == "482913"
    # An incidental 6-digit number (order id) must lose to the keyword-adjacent code.
    assert ex.extract("Order 100200 confirmed. Your security code: 314159") == "314159"
    assert ex.extract("nothing numeric here") is None
    # A longer digit run is not a 6-digit code (no false capture from inside it).
    assert ex.extract("ref 1234567 (seven digits)") is None


# ── IMAP reader against a fake mailbox ─────────────────────────────────────────────────────────

class _FakeIMAP:
    """Minimal imaplib-shaped stub: select/search/fetch/logout over a list of raw RFC822 bytes."""

    def __init__(self, messages: List[bytes], fail_on_select: bool = False):
        self._messages = messages
        self._fail = fail_on_select
        self.logged_out = False

    def select(self, mailbox):
        if self._fail:
            raise OSError("imap select failed")
        return ("OK", [str(len(self._messages)).encode()])

    def search(self, charset, *criteria):
        uids = " ".join(str(i + 1) for i in range(len(self._messages))).encode()
        return ("OK", [uids])

    def fetch(self, uid, spec):
        idx = int(uid.decode() if isinstance(uid, bytes) else uid) - 1
        if 0 <= idx < len(self._messages):
            return ("OK", [(f"{uid} (RFC822)".encode(), self._messages[idx]), b")"])
        return ("NO", [None])

    def logout(self):
        self.logged_out = True
        return ("OK", [b"BYE"])


def _raw(subject: str, body: str, frm: str = "Label Engine <no-reply@labelengine.com>") -> bytes:
    return (f"From: {frm}\r\nSubject: {subject}\r\n\r\n{body}").encode("utf-8")


def test_reader_parses_recent_newest_first():
    fake = _FakeIMAP([_raw("old", "first 111111"), _raw("Your code", "code is 482913")])
    reader = MailboxReader(user="x@gmail.com", app_password="unused-injected", connect=lambda: fake)
    msgs = reader.fetch_recent()
    assert len(msgs) == 2
    assert msgs[0].subject == "Your code"            # newest first
    assert "482913" in msgs[0].body
    assert "labelengine.com" in msgs[0].from_addr
    assert fake.logged_out is True                    # connection always closed


def test_reader_failsafe_on_imap_error_returns_empty():
    reader = MailboxReader(user="x", app_password="unused",
                           connect=lambda: _FakeIMAP([], fail_on_select=True))
    assert reader.fetch_recent() == []                # missed read → retry next wake, never crash


def test_reader_injected_connect_never_uses_app_password():
    # Credential-agnostic: an injected connection bypasses login entirely, so the app-password is
    # never touched in tests (and in prod it is used ONLY inside _default_connect → login).
    used = {"login": False}

    class _NoLoginIMAP(_FakeIMAP):
        def login(self, u, p):
            used["login"] = True

    reader = MailboxReader(user="x", app_password="SECRET-NEVER-USED",
                           connect=lambda: _NoLoginIMAP([_raw("Code", "code 246810")]))
    assert reader.fetch_recent()[0].body.endswith("246810")
    assert used["login"] is False


# ── event-driven watcher ───────────────────────────────────────────────────────────────────────

class _StubReader:
    """Returns a scripted sequence of message-lists, one per fetch_recent() call."""

    def __init__(self, sequence: List[List[EmailMessage]]):
        self._seq = list(sequence)
        self.calls = 0

    def fetch_recent(self, **_kw):
        out = self._seq[self.calls] if self.calls < len(self._seq) else []
        self.calls += 1
        return out


def _code_msg(code: str) -> EmailMessage:
    return EmailMessage(subject="Your verification code", from_addr="le", body=f"code is {code}")


def test_check_once_returns_code_or_none():
    w = TwoFactorWatcher(_StubReader([[_code_msg("482913")]]))
    assert w.check_once() == "482913"
    w2 = TwoFactorWatcher(_StubReader([[EmailMessage("hi", "x", "no code here")]]))
    assert w2.check_once() is None


def test_wait_for_code_polls_until_arrival_no_real_sleep():
    slept: List[float] = []
    reader = _StubReader([[], [], [_code_msg("314159")]])     # arrives on the 3rd poll
    w = TwoFactorWatcher(reader, poll_interval_s=15.0, max_attempts=5,
                         sleeper=lambda s: slept.append(s))
    assert w.wait_for_code() == "314159"
    assert reader.calls == 3
    assert slept == [15.0, 15.0]                              # bounded, and NO real sleeping


def test_wait_for_code_is_bounded_and_returns_none_on_timeout():
    slept: List[float] = []
    reader = _StubReader([[], [], [], [], []])                # never arrives
    w = TwoFactorWatcher(reader, poll_interval_s=15.0, max_attempts=4,
                         sleeper=lambda s: slept.append(s))
    assert w.wait_for_code() is None                          # never blocks forever
    assert reader.calls == 4 and len(slept) == 3              # max_attempts polls, attempts-1 sleeps
