from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

from .verify import DCAPError, verify_quote

RATLS_QUOTE_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.1")


@dataclass
class RatlsVerifyResult:
    verified: bool
    reason: str
    report_data: Optional[str] = None
    measurements: Optional[dict] = None


def public_key_digest(public_key: ec.EllipticCurvePublicKey) -> bytes:
    data = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(data).digest()


def report_data_for_pubkey(public_key: ec.EllipticCurvePublicKey) -> bytes:
    return public_key_digest(public_key) + b"\x00" * 32


def build_ratls_cert(
    quote: bytes,
    private_key: ec.EllipticCurvePrivateKey,
    common_name: str = "easyenclave-ratls",
    ttl_seconds: int = 3600,
) -> bytes:
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(seconds=ttl_seconds))
        .add_extension(x509.UnrecognizedExtension(RATLS_QUOTE_OID, quote), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def extract_quote_from_cert(cert: x509.Certificate) -> bytes:
    try:
        ext = cert.extensions.get_extension_for_oid(RATLS_QUOTE_OID)
    except x509.ExtensionNotFound:
        return b""
    value = getattr(ext.value, "value", b"")
    return value or b""


def match_quote_measurements(allowlist: dict, measurements: dict) -> tuple[bool, str]:
    expected = allowlist.get("quote_measurements") or {}
    if not expected:
        return False, "allowlist_missing_quote_measurements"
    for key, value in expected.items():
        if key == "report_data":
            continue
        if measurements.get(key) != value:
            return False, f"measurement_mismatch:{key}"
    return True, "ok"


def verify_ratls_cert(
    cert_der: bytes,
    allowlist: Optional[dict],
    pccs_url: Optional[str] = None,
    skip_pccs: bool = False,
    require_allowlist: bool = True,
) -> RatlsVerifyResult:
    if not cert_der:
        return RatlsVerifyResult(False, "missing_peer_cert")

    cert = x509.load_der_x509_certificate(cert_der)
    quote = extract_quote_from_cert(cert)
    if not quote:
        return RatlsVerifyResult(False, "missing_quote_extension")

    pubkey = cert.public_key()
    expected_report = report_data_for_pubkey(pubkey).hex()

    try:
        result = verify_quote(base64.b64encode(quote).decode(), pccs_url=pccs_url, skip_pccs=skip_pccs)
    except DCAPError as exc:
        return RatlsVerifyResult(False, f"dcap_error:{exc}")

    report_data = result.get("measurements", {}).get("report_data")
    if not report_data:
        return RatlsVerifyResult(False, "missing_report_data")
    if report_data.lower() != expected_report.lower():
        return RatlsVerifyResult(False, "report_data_mismatch", report_data=report_data, measurements=result.get("measurements"))

    if not result.get("verified"):
        return RatlsVerifyResult(False, "dcap_verification_failed", report_data=report_data, measurements=result.get("measurements"))

    if allowlist is None:
        if require_allowlist:
            return RatlsVerifyResult(False, "missing_allowlist", report_data=report_data, measurements=result.get("measurements"))
        return RatlsVerifyResult(True, "ok", report_data=report_data, measurements=result.get("measurements"))

    measurements = result.get("measurements", {}) or {}
    ok, reason = match_quote_measurements(allowlist, measurements)
    if not ok:
        return RatlsVerifyResult(False, reason, report_data=report_data, measurements=measurements)

    return RatlsVerifyResult(True, "ok", report_data=report_data, measurements=measurements)
