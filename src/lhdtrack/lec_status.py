"""LEC presentation and statistics policy; never a theorem-cache policy."""
from __future__ import annotations


def display_verdict(block: dict | None) -> str | None:
    block = block or {}
    verdict = block.get("verdict")
    if verdict == "proven" and block.get("bounded"):
        return f"bounded({block.get('bound', '?')})"
    raw = str(block.get("lhd_verdict") or "").lower()
    if verdict in {"tested", "tested-sampled", "timeout", "timeout-time", "timeout-memory"}:
        return "tested"
    if verdict in {None, "inconclusive", "unknown"} and raw.startswith("tested-sampled"):
        return "tested"  # legacy ledgers recorded successful sampling as inconclusive
    return verdict


def accepted_for_statistics(block: dict | None) -> bool:
    """Tested and proven measurements participate equally; refutations do not."""
    verdict = display_verdict(block) or ""
    return verdict in {"proven", "tested"} or verdict.startswith("bounded(")


def comparable_runs(left: dict, right: dict) -> bool:
    """Compare accepted LEC attempts, or two counterexample-finding attempts."""
    return (accepted_for_statistics(left) and accepted_for_statistics(right)) or (
        display_verdict(left) == display_verdict(right) == "refuted")


def language_comparable(row: dict) -> bool:
    """Apply today's statistics policy to legacy manifest-status rows too."""
    if row.get("pyrope_status") not in {None, "idiomatic"}:
        return False
    if row.get("lec") in {"refuted", "error", "unsupported"}:
        return False
    return bool(row.get("comparable")) or (
        row.get("pyrope_status") == "idiomatic"
        and accepted_for_statistics({"verdict": row.get("lec")})
    )
