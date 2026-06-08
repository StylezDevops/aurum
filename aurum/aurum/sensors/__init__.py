"""Sensors — concrete inbound watchers (the SEN organ's eventual population).

This package holds individual sensors (the first: Gmail/IMAP 2FA-code capture). The general
Sensorium organ (SEN — a pluggable watcher REGISTRY over folder/inbox/repo/webhook/RSS) is NOT
yet built (build_state SEN stays False); these are the concrete capabilities it will register.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from .gmail_2fa import EmailMessage, MailboxReader, TwoFactorExtractor, TwoFactorWatcher
from .gmail_oauth import GmailApiReader, parse_gmail_message

__all__ = ["EmailMessage", "MailboxReader", "TwoFactorExtractor", "TwoFactorWatcher",
           "GmailApiReader", "parse_gmail_message"]
