"""Linear-time redaction / taint-scanning for the EL & PK fast paths (AURUM_ERR_068).

DEFECT this fixes: PK.redact on the EL/PK fast-path was left to regex, and Python's native
`re` has O(2^N) catastrophic backtracking on crafted patterns. A ~50-char adversarial string
could lock a CPU core for minutes, back the EL queue into permanent LedgerBackpressure, and
brick the agent. Volume was protected (053/037); algorithmic complexity was not.

FIX: an O(N)-GUARANTEED engine. RE2 (linear-time by construction) when the `re2` package is
installed; otherwise a hand-rolled, backtracking-free linear SCANNER. Native Python `re` is
FORBIDDEN on these paths — it is never imported here, so a ReDoS pattern cannot be introduced
by accident. Redaction time scales linearly with input length regardless of input content.

Scope is deliberately the cheap, signature-matchable secret/PII shapes (emails, bearer tokens,
key=value secrets, long opaque high-entropy runs). Semantic redaction is explicitly NOT done on
the hot path (it cannot be done at the latency SLO) — see the holdout "cheap-or-external"
discipline in the runtime-integrity reference §4.3.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

from typing import Any

# We NEVER import native `re` here — that is the whole point (no backtracking engine on the
# fast path). RE2 is linear by construction; the fallback scanner is hand-rolled and linear.
try:  # pragma: no cover - depends on optional dep being installed
    import re2 as _re2  # google-re2 / pyre2 — linear-time guaranteed
    _HAVE_RE2 = True
except Exception:  # ImportError or build failure
    _re2 = None
    _HAVE_RE2 = False

REDACTED = "[REDACTED]"

# Native `re` is forbidden on these paths; this module asserts that by construction.
USES_NATIVE_RE = False

# Case-insensitive secret KEYWORDS that introduce a value to redact (key=value / key: value).
_SECRET_KEYWORDS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "api-key",
    "authorization", "auth", "bearer", "client_secret", "access_key", "private_key",
)

_VALUE_TERMINATORS = set(" \t\r\n\"',;)}]")
_EMAIL_LOCAL_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-")
_EMAIL_DOMAIN_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_OPAQUE_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/=_-")
_OPAQUE_MIN = 32  # a contiguous run of >=32 opaque chars is treated as a possible secret
# ID/HASH shape: pure lowercase hex + dashes (uuid4().hex, canonical uuid, md5/sha digests). These
# are IDENTIFIERS and integrity hashes — the join keys replay/lineage depend on (decision_id, …) —
# never credentials (which use mixed case / base64 / prefixes). EXEMPT from opaque-run redaction so
# the value-scan can catch bare tokens WITHOUT redacting uuids (the regression that breaks joins).
_ID_CHARS = frozenset("0123456789abcdef-")


def _is_id_shaped(run: str) -> bool:
    """True iff `run` is pure lowercase-hex + dashes (a uuid/digest shape), so it is exempt from
    opaque-run redaction. A real credential of this exact shape is vanishingly rare and is still
    covered by key-name redaction + the secret-by-reference rule."""
    return bool(run) and all(c in _ID_CHARS for c in run)


class LinearRedactor:
    """O(N) redactor. `engine` is "re2" when the linear regex engine is available, else
    "linear-scan" (a backtracking-free character scanner). Either way the worst-case cost is
    linear in input length — a ReDoS payload cannot lock the CPU."""

    def __init__(self) -> None:
        self.engine = "re2" if _HAVE_RE2 else "linear-scan"

    # -- public API --------------------------------------------------------

    def redact(self, text: str) -> str:
        """Redact secrets/PII in a single string in linear time."""
        if not isinstance(text, str) or not text:
            return text
        if _HAVE_RE2:
            return self._redact_re2(text)
        return self._redact_scan(text)

    def redact_payload(self, payload: Any) -> Any:
        """Recursively redact a JSON-shaped payload (dict/list/str). Keys whose NAME is a
        secret keyword have their value redacted wholesale; all strings are scanned."""
        if isinstance(payload, dict):
            out = {}
            for k, v in payload.items():
                if isinstance(k, str) and k.lower() in _SECRET_KEYWORDS:
                    out[k] = REDACTED
                else:
                    out[k] = self.redact_payload(v)
            return out
        if isinstance(payload, list):
            return [self.redact_payload(v) for v in payload]
        if isinstance(payload, str):
            return self.redact(payload)
        return payload

    # -- RE2 path (linear by construction) --------------------------------

    def _redact_re2(self, text: str) -> str:  # pragma: no cover - exercised only with re2
        # RE2 has no backtracking, so even these patterns are linear-time.
        email = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
        kv = r"(?i)(password|passwd|secret|token|api[_\-]?key|authorization|bearer|client_secret|access_key|private_key)\s*[=:]\s*\S+"
        opaque = r"[A-Za-z0-9+/=_\-]{32,}"
        out = _re2.sub(email, REDACTED, text)
        out = _re2.sub(kv, REDACTED, out)
        # id/hash-shaped opaque runs (uuids, digests) are exempt — preserve replay/lineage join keys
        out = _re2.sub(opaque, lambda m: m.group(0) if _is_id_shaped(m.group(0)) else REDACTED, out)
        return out

    # -- linear scanner fallback (no regex, no backtracking) --------------

    def _redact_scan(self, text: str) -> str:
        n = len(text)
        out: list[str] = []
        i = 0
        low = text.lower()
        while i < n:
            # 1) secret keyword -> redact the following value token.
            kw = self._match_keyword(low, i)
            if kw is not None:
                j = i + kw
                # skip optional separator (= or :) and surrounding spaces
                while j < n and text[j] in " \t":
                    j += 1
                if j < n and text[j] in "=:":
                    j += 1
                    while j < n and text[j] in " \t":
                        j += 1
                    out.append(text[i:j])
                    # consume the value token up to a terminator (linear)
                    while j < n and text[j] not in _VALUE_TERMINATORS:
                        j += 1
                    out.append(REDACTED)
                    i = j
                    continue
                out.append(text[i:j])
                i = j
                continue
            ch = text[i]
            # 2) email: a local run, '@', a domain run with a dot.
            if ch in _EMAIL_LOCAL_OK:
                k = i
                while k < n and text[k] in _EMAIL_LOCAL_OK:
                    k += 1
                if k < n and text[k] == "@":
                    d = k + 1
                    while d < n and text[d] in _EMAIL_DOMAIN_OK:
                        d += 1
                    if "." in text[k + 1:d] and d - (k + 1) >= 3:
                        out.append(REDACTED)
                        i = d
                        continue
                # 3) long opaque run (possible token) — only if it qualifies.
                if ch in _OPAQUE_OK:
                    m = i
                    while m < n and text[m] in _OPAQUE_OK:
                        m += 1
                    if m - i >= _OPAQUE_MIN:
                        run = text[i:m]
                        out.append(run if _is_id_shaped(run) else REDACTED)  # exempt uuids/hashes
                        i = m
                        continue
                    out.append(text[i:k if k > i else i + 1])
                    i = k if k > i else i + 1
                    continue
                out.append(text[i:k])
                i = k
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _match_keyword(low: str, i: int) -> int | None:
        """If a secret keyword starts at position i (on a word boundary), return its length;
        else None. O(number of keywords), independent of input length -> overall linear."""
        if i > 0 and (low[i - 1].isalnum() or low[i - 1] == "_"):
            return None
        for kw in _SECRET_KEYWORDS:
            if low.startswith(kw, i):
                end = i + len(kw)
                if end >= len(low) or not (low[end].isalnum() or low[end] == "_"):
                    return len(kw)
        return None


# Module-level shared instance (stateless; safe to share).
DEFAULT_REDACTOR = LinearRedactor()


def redact(payload: Any) -> Any:
    """Single redaction chokepoint matching PK.redact's signature, backed by the linear
    engine. Organs that persist state call this; they never roll their own (and never `re`)."""
    return DEFAULT_REDACTOR.redact_payload(payload)
