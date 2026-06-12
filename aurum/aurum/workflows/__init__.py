"""Governed workflows — real useful workloads driven end-to-end through the governance spine.

A workflow NEVER touches the world directly: every step is cleared by GovernanceKernel.govern()
first (write-then-act), and a denied/gated step is observed-and-contained, not executed —
gated execution is shadow mode by construction.
"""
from .dataverse_contact import GovernedWorkflow, ShadowDataverse, StepResult, WorkflowResult

__all__ = ["GovernedWorkflow", "ShadowDataverse", "StepResult", "WorkflowResult"]
