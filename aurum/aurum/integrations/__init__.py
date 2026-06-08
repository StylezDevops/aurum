"""Integrations — governed clients for external services the agent drives THROUGH the spine.

Every call is cleared by GovernanceKernel.govern() first; the irreversible ones are gated to the
full authority band. Secrets are by-reference (resolved at call time), never embedded.
"""
# Author: StylezDevops <94297031+StylezDevops@users.noreply.github.com>
from .pipeline import PipelineClient, resolve_secret

__all__ = ["PipelineClient", "resolve_secret"]
