"""Sensors — concrete inbound watchers (the SEN organ's eventual population).

This package holds individual sensors (the first: Gmail/IMAP 2FA-code capture). The general
Sensorium organ (SEN — a pluggable watcher REGISTRY over folder/inbox/repo/webhook/RSS) is NOT
yet built (build_state SEN stays False); these are the concrete capabilities it will register.
"""
# Author: StylezDevops <94297031+StylezDevops@users.noreply.github.com>
from .gmail_2fa import EmailMessage, MailboxReader, TwoFactorExtractor, TwoFactorWatcher

__all__ = ["EmailMessage", "MailboxReader", "TwoFactorExtractor", "TwoFactorWatcher"]
