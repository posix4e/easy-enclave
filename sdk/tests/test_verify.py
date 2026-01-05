"""Tests for TDX quote verification."""
import base64

import pytest
from easyenclave.exceptions import DCAPError
from easyenclave.verify import (
    extract_certificates,
    extract_measurements,
    extract_fmspc_from_cert,
    parse_quote,
    parse_quote_header,
    parse_td_report,
    verify_quote,
)

# Sample TDX quote from actual deployment (truncated for tests)
# This is a real quote structure with valid header and TD report
SAMPLE_QUOTE_B64 = (
    "BAACAIEAAAAAAAAAk5pyM/ecTKmUCg2zlX8GB42gRy/8yoWpJiGVOzCSrFsAAAAA"
    "CwEEAAAAAAAAAAAAAAAAAHvwYygOlPsFH13XsfxZzpqsQruWHfjUS3Ccmw/4entN"
    "9khle6bRGJWJ/qsdWjyanQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAADNAGYANAAAAAAAO9+PQ"
    "eTjuAEX90DyGiOnIPlXQT+kgMpsLyqlw1R+QYqxR/43z1xiePgHTi1tEqgeQAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAABGhtFbsta1wtIA0WJIyAS7N5QM1wktTTnsT/lHfZPWZLYu00n5l16T"
    "WWVbfqiNunzSSYXhBzMY+Pp85Yd+kLZ9p+3zwfRV3RUSEBCMInUH818bv+AHj5CJ"
    "Su4n3vhgHGcFBjz6CY44k6t1T0uL1g6o+1Uen6Q0nbQs174t89ePLltENwaFE8Bx"
    "Tz1xo37ImEcAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAADMEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)


class TestQuoteHeader:
    """Tests for quote header parsing."""

    def test_parse_valid_header(self):
        """Test parsing a valid TDX quote header."""
        quote_bytes = base64.b64decode(SAMPLE_QUOTE_B64)
        header = parse_quote_header(quote_bytes[:48])

        assert header.version == 4
        assert header.tee_type == 0x81  # TDX
        assert header.att_key_type == 2  # ECDSA-256

    def test_header_too_short(self):
        """Test that short headers raise error."""
        with pytest.raises(DCAPError, match="too short"):
            parse_quote_header(b'\x00' * 10)


class TestTDReport:
    """Tests for TD Report parsing."""

    def test_parse_td_report(self):
        """Test parsing TD Report from quote."""
        quote_bytes = base64.b64decode(SAMPLE_QUOTE_B64)
        td_report = parse_td_report(quote_bytes[48:48+584])

        assert len(td_report.rtmr0) == 48
        assert len(td_report.rtmr1) == 48
        assert len(td_report.rtmr2) == 48
        assert len(td_report.rtmr3) == 48
        assert len(td_report.report_data) == 64

    def test_td_report_too_short(self):
        """Test that short TD report raises error."""
        with pytest.raises(DCAPError, match="too short"):
            parse_td_report(b'\x00' * 100)


class TestQuoteParsing:
    """Tests for full quote parsing."""

    def test_parse_quote_structure(self):
        """Test parsing complete quote structure."""
        quote_bytes = base64.b64decode(SAMPLE_QUOTE_B64)

        # Our sample quote is truncated (no signature data), so parse_quote
        # will raise DCAPError. We test header/report parsing separately.
        # This test verifies the error handling for truncated quotes.
        if len(quote_bytes) < 636:
            with pytest.raises(DCAPError, match="too short"):
                parse_quote(quote_bytes)
        else:
            # Sample is truncated - signature data incomplete
            with pytest.raises(DCAPError, match="Signature data too short"):
                parse_quote(quote_bytes)

    def test_quote_too_short(self):
        """Test that short quotes raise error."""
        with pytest.raises(DCAPError, match="too short"):
            parse_quote(b'\x00' * 100)

    def test_invalid_tee_type(self):
        """Test that non-TDX quotes are rejected."""
        # Create a quote with wrong TEE type
        quote_bytes = bytearray(base64.b64decode(SAMPLE_QUOTE_B64))
        if len(quote_bytes) >= 8:
            # Set TEE type to 0x00 (SGX) instead of 0x81 (TDX)
            quote_bytes[4:8] = b'\x00\x00\x00\x00'
            with pytest.raises(DCAPError, match="Not a TDX quote"):
                parse_quote(bytes(quote_bytes))


class TestMeasurements:
    """Tests for measurement extraction."""

    def test_extract_measurements(self):
        """Test extracting RTMR measurements from quote."""
        quote_bytes = base64.b64decode(SAMPLE_QUOTE_B64)

        if len(quote_bytes) >= 632:
            measurements = extract_measurements(quote_bytes)

            assert "rtmr0" in measurements
            assert "rtmr1" in measurements
            assert "rtmr2" in measurements
            assert "rtmr3" in measurements

            # Each RTMR should be 48 bytes = 96 hex chars
            assert len(measurements["rtmr0"]) == 96
            assert len(measurements["rtmr1"]) == 96

    def test_extract_measurements_short_quote(self):
        """Test measurement extraction with short quote returns error markers."""
        measurements = extract_measurements(b'\x00' * 100)
        assert measurements["rtmr0"] == "error"


class TestCertificates:
    """Tests for certificate extraction."""

    def test_extract_certificates_empty(self):
        """Test extracting certs from data without PEM."""
        certs = extract_certificates(b'no certificates here')
        assert certs == []

    def test_extract_certificates_valid_pem(self):
        """Test extracting valid PEM certificate."""
        from easyenclave.verify import INTEL_ROOT_CA_PEM

        certs = extract_certificates(INTEL_ROOT_CA_PEM)
        assert len(certs) == 1
        assert "Intel SGX Root CA" in certs[0].subject.rfc4514_string()


class TestVerifyQuote:
    """Tests for full quote verification."""

    def test_verify_quote_invalid_encoding(self):
        """Test that invalid base64 raises error."""
        with pytest.raises(DCAPError, match="Invalid quote encoding"):
            verify_quote("not-valid-base64!!!")

    def test_verify_quote_too_short(self):
        """Test that short quotes raise error."""
        short_quote = base64.b64encode(b'\x00' * 100).decode()
        with pytest.raises(DCAPError, match="too short"):
            verify_quote(short_quote)


class TestFMSPCExtraction:
    """Tests for FMSPC extraction from PCK certificate."""

    def test_extract_fmspc_strict_oid(self):
        """Extract FMSPC only when the correct OID is present."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID, ObjectIdentifier
        from datetime import datetime, timedelta, timezone

        sgx_extensions_oid = ObjectIdentifier("1.2.840.113741.1.13.1")
        fmspc_oid_bytes = bytes.fromhex("060a2a864886f84d010d0104")
        fmspc_bytes = bytes.fromhex("00906ED50000")
        ext_value = fmspc_oid_bytes + b"\x04\x06" + fmspc_bytes

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.UnrecognizedExtension(sgx_extensions_oid, ext_value), critical=False)
            .sign(key, hashes.SHA256())
        )

        assert extract_fmspc_from_cert(cert) == "00906ED50000"

    def test_extract_fmspc_missing_oid(self):
        """Return None when the FMSPC OID is not present."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID, ObjectIdentifier
        from datetime import datetime, timedelta, timezone

        sgx_extensions_oid = ObjectIdentifier("1.2.840.113741.1.13.1")
        wrong_oid_bytes = bytes.fromhex("060a2a864886f84d010d0105")
        fmspc_bytes = bytes.fromhex("00906ED50000")
        ext_value = wrong_oid_bytes + b"\x04\x06" + fmspc_bytes

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.UnrecognizedExtension(sgx_extensions_oid, ext_value), critical=False)
            .sign(key, hashes.SHA256())
        )

        assert extract_fmspc_from_cert(cert) is None
