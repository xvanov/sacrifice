"""UX auditor service.

Consumes direction artifacts through ``build_ux_auditor_payload`` and
runs browser-backed UX audits inside a sandboxed environment.  Each
finding cites Playwright locator actions, response timings, or axe-core
rule ids so downstream evidence-emission work can reference specific,
objective measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.workers.ux_auditor_sandbox import BrowserSandboxResult


@dataclass
class UxAuditFinding:
    """A single UX finding with a citation grounded in browser data."""

    citation_type: str  # "playwright_locator", "response_timing", "axe_rule"
    summary: str
    detail: dict = field(default_factory=dict)


@dataclass
class UxAuditReport:
    """Complete UX audit report for a direction."""

    direction_id: str
    target_url: str
    findings: list[UxAuditFinding] = field(default_factory=list)
    sandbox_exit_code: int = -1
    sandbox_timed_out: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.sandbox_exit_code == 0 and not self.sandbox_timed_out and self.error is None


# Canonical citation types accepted by the downstream evidence contract.
CITATION_PLAYWRIGHT_LOCATOR = "playwright_locator"
CITATION_RESPONSE_TIMING = "response_timing"
CITATION_AXE_RULE = "axe_rule"

_CANONICAL_CITATION_TYPES = {
    CITATION_PLAYWRIGHT_LOCATOR,
    CITATION_RESPONSE_TIMING,
    CITATION_AXE_RULE,
}


def _findings_from_sandbox_result(
    result: "BrowserSandboxResult",
) -> list[UxAuditFinding]:
    """Convert raw sandbox findings into typed ``UxAuditFinding`` objects.

    Only findings with canonical citation types are included; unrecognised
    types are silently dropped so downstream consumers see a clean contract.
    """
    out: list[UxAuditFinding] = []
    for raw in result.findings:
        ctype = raw.get("citation_type", "")
        if ctype not in _CANONICAL_CITATION_TYPES:
            continue

        if ctype == CITATION_PLAYWRIGHT_LOCATOR:
            summary = f"Locator: {raw.get('locator', 'unknown')}"
        elif ctype == CITATION_RESPONSE_TIMING:
            summary = (
                f"Response timing: {raw.get('load_time_ms', '?')}ms "
                f"(status {raw.get('status_code', '?')})"
            )
        elif ctype == CITATION_AXE_RULE:
            summary = (
                f"Axe rule {raw.get('rule_id', '?')}: "
                f"{raw.get('description', 'unknown')[:120]}"
            )
        else:
            summary = "Unknown finding"

        out.append(UxAuditFinding(citation_type=ctype, summary=summary, detail=raw))

    return out


async def run_ux_audit(
    direction_id: str,
    target_url: str,
    *,
    sandbox_image: str | None = None,
    timeout: int = 120,
) -> UxAuditReport:
    """Run a browser-backed UX audit for *direction_id* against *target_url*.

    The audit launches a browser sandbox, navigates to ``target_url``,
    and collects Playwright locator references, response timings, and
    axe-core accessibility rule findings.

    Returns an ``UxAuditReport`` suitable for downstream evidence-emission
    work.
    """
    from app.workers.ux_auditor_sandbox import BrowserSandbox, BrowserSandboxError

    report = UxAuditReport(direction_id=direction_id, target_url=target_url)

    try:
        sandbox = BrowserSandbox(image=sandbox_image, timeout=timeout)
        result = sandbox.run_audit(target_url)
    except BrowserSandboxError as exc:
        report.error = str(exc)
        return report

    report.sandbox_exit_code = result.exit_code
    report.sandbox_timed_out = result.timed_out
    report.findings = _findings_from_sandbox_result(result)

    if not result.success:
        report.error = (
            f"Sandbox exited {result.exit_code}"
            + (" (timed out)" if result.timed_out else "")
        )

    return report