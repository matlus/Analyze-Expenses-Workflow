"""Semantic exception branches for context-compaction trials."""

from __future__ import annotations


class ContextCompactionBusinessError(ValueError):
    """A trial input can be corrected by its caller."""


class ContextCompactionTechnicalError(RuntimeError):
    """A trial failure needs system-owner attention."""
