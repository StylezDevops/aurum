"""Phase F — GmailApiReader (OAuth/Gmail-API path): same interface as the IMAP reader, drops into
the watcher; tested against a FAKE Gmail service (no live creds, no google libs needed)."""
from __future__ import annotations

import base64
from typing import Dict, List

from aurum.sensors import GmailApiReader, TwoFactorWatcher, parse_gmail_message


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _gmail_msg(mid: str, subject: str, frm: str, body: str, html: bool = False) -> Dict:
    part_mime = "text/html" if html else "text/plain"
    return {"id": mid, "payload": {
        "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": frm}],
        "mimeType": "multipart/alternative",
        "parts": [{"mimeType": part_mime, "body": {"data": _b64url(body)}}]}}


class _Resp:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _Messages:
    def __init__(self, listing: Dict, by_id: Dict[str, Dict], fail: bool = False):
        self._listing = listing
        self._by_id = by_id
        self._fail = fail
        self.last_query = None

    def list(self, *, userId, q, maxResults):
        self.last_query = q
        if self._fail:
            raise RuntimeError("gmail api down")
        return _Resp(self._listing)

    def get(self, *, userId, id, format):
        return _Resp(self._by_id[id])


class _FakeGmail:
    def __init__(self, msgs: List[Dict], fail: bool = False):
        listing = {"messages": [{"id": m["id"]} for m in msgs]}   # Gmail returns newest-first
        self._messages = _Messages(listing, {m["id"]: m for m in msgs}, fail=fail)

    def users(self):
        return self

    def messages(self):
        return self._messages


def test_parse_gmail_message_extracts_headers_and_body():
    msg = _gmail_msg("1", "Your verification code", "Label Engine <no-reply@le.com>",
                     "Your code is 482913")
    em = parse_gmail_message(msg)
    assert em.subject == "Your verification code"
    assert "le.com" in em.from_addr
    assert "482913" in em.body and em.uid == "1"


def test_reader_fetches_and_builds_query():
    fake = _FakeGmail([_gmail_msg("2", "Code", "le", "code 314159"),
                       _gmail_msg("1", "old", "x", "nope")])
    reader = GmailApiReader(service=fake)
    msgs = reader.fetch_recent(from_addr="no-reply@le.com", subject_contains="code")
    assert [m.uid for m in msgs] == ["2", "1"]                 # newest-first preserved
    assert "is:unread" in fake._messages.last_query
    assert "from:no-reply@le.com" in fake._messages.last_query
    assert 'subject:"code"' in fake._messages.last_query


def test_reader_failsafe_on_api_error():
    reader = GmailApiReader(service=_FakeGmail([], fail=True))
    assert reader.fetch_recent() == []                          # never crashes a turn


def test_reader_plugs_into_watcher_check_once():
    fake = _FakeGmail([_gmail_msg("9", "Your security code", "le", "code: 246810")])
    code = TwoFactorWatcher(GmailApiReader(service=fake)).check_once()
    assert code == "246810"                                     # extractor + OAuth reader compose
