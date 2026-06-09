"""Integrations — the public framework primitive for governed external access.

`GovernedHttpClient` drives ANY external HTTP API through the governance kernel (safe reads pass;
irreversible/outward calls gated to the full band) with secrets by-reference and mount-registered
tool classification. Concrete, domain-specific clients (a label's release pipeline, a CRM, …) are
INSTANCE code: subclass this in the private layer, not here.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from .governed_http import GovernedHttpClient, resolve_secret

__all__ = ["GovernedHttpClient", "resolve_secret"]
