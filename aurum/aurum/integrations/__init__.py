"""Integrations — governed clients for external services the agent drives THROUGH the spine.

Every call is cleared by GovernanceKernel.govern() first; the irreversible ones are gated to the
full authority band. Secrets are by-reference (resolved at call time), never embedded.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from .pipeline import PipelineClient, resolve_secret

__all__ = ["PipelineClient", "resolve_secret"]
