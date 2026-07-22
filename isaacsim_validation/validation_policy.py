"""Acceptance policy for Isaac Sim's robot asset validator output."""

from __future__ import annotations

NONBLOCKING_WARNING_RULES = frozenset({"ThumbnailExists"})


def asset_validator_verdict(issues: list[dict]) -> dict:
    """Treat errors and non-cosmetic warnings as VLA/RL asset blockers."""
    blocking = [
        issue
        for issue in issues
        if issue.get("severity") == "ERROR"
        or (issue.get("severity") == "WARNING" and issue.get("rule") not in NONBLOCKING_WARNING_RULES)
    ]
    return {
        "passed": not blocking,
        "blocking_issue_count": len(blocking),
        "blocking_rules": sorted({str(issue.get("rule")) for issue in blocking}),
    }
