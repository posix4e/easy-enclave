from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from easyenclave.verify import DCAPError, verify_quote


@dataclass
class AttestationResult:
    verified: bool
    reason: str
    sealed: bool
    report_data: Optional[str] = None


def verify_attestation(
    attestation: dict,
    allowlist: dict,
    require_sealed: bool,
    pccs_url: Optional[str],
) -> AttestationResult:
    quote = attestation.get("quote")
    measurements = attestation.get("measurements", {})
    if not quote or not measurements:
        return AttestationResult(False, "missing_quote_or_measurements", False)

    expected = allowlist.get("measurements", {})
    if not expected:
        return AttestationResult(False, "allowlist_missing_measurements", False)

    for key, value in expected.items():
        if measurements.get(key) != value:
            return AttestationResult(False, f"measurement_mismatch:{key}", bool(measurements.get("sealed")))

    sealed_value = bool(measurements.get("sealed"))
    if require_sealed and not sealed_value:
        return AttestationResult(False, "sealed_required", sealed_value)

    try:
        result = verify_quote(quote, pccs_url=pccs_url, skip_pccs=False)
    except DCAPError as exc:
        return AttestationResult(False, f"dcap_error:{exc}", sealed_value)

    report_data = result.get("measurements", {}).get("report_data")
    expected_report = allowlist.get("report_data")
    if expected_report and report_data and report_data.lower() != expected_report.lower():
        return AttestationResult(False, "report_data_mismatch", sealed_value, report_data=report_data)

    if not result.get("verified"):
        return AttestationResult(False, "dcap_verification_failed", sealed_value, report_data=report_data)

    return AttestationResult(True, "ok", sealed_value, report_data=report_data)
