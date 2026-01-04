#!/usr/bin/env python3
"""
Generate TDX quote using modern kernel interface (configfs-tsm).

Requires:
- Linux kernel 6.7+ with CONFIG_TSM_REPORTS enabled
- QGS (Quote Generation Service) backend for Intel-signed quotes
- TDX hardware with proper DCAP provisioning

This module ONLY uses the configfs-tsm interface to ensure all quotes
are properly signed by Intel's Quoting Enclave via DCAP infrastructure.
"""

import base64
import json
import os
from pathlib import Path

# Minimum quote size for a valid Intel-signed TDX quote
# Header (48) + TD Report (584) + Signature data (~variable, but at least 100)
MIN_SIGNED_QUOTE_SIZE = 1020

# TDX TEE type identifier in quote header
TDX_TEE_TYPE = 0x81


def validate_intel_signature(quote: bytes) -> None:
    """
    Validate that a quote has Intel signature structure.

    A properly signed TDX quote must have:
    - Minimum size (header + report + signature)
    - Correct TEE type (0x81 for TDX)
    - Valid quote version

    Args:
        quote: Raw quote bytes

    Raises:
        ValueError: If quote lacks Intel signature structure
    """
    if len(quote) < MIN_SIGNED_QUOTE_SIZE:
        raise ValueError(
            f"Quote too small ({len(quote)} bytes) to contain Intel signature. "
            f"Minimum size is {MIN_SIGNED_QUOTE_SIZE} bytes. "
            "This likely means QGS backend is not configured for configfs-tsm."
        )

    # Parse quote header
    version = int.from_bytes(quote[0:2], 'little')
    att_key_type = int.from_bytes(quote[2:4], 'little')
    tee_type = int.from_bytes(quote[4:8], 'little')

    if tee_type != TDX_TEE_TYPE:
        raise ValueError(
            f"Invalid TEE type {tee_type:#x}, expected {TDX_TEE_TYPE:#x} (TDX). "
            "This is not a valid TDX quote."
        )

    if version < 4:
        raise ValueError(
            f"Quote version {version} is too old. TDX quotes require version 4+."
        )

    # Check for signature data after the header + report
    # Quote header is 48 bytes, TD report body is ~584 bytes
    # Signature section should follow
    header_and_report_size = 48 + 584
    if len(quote) <= header_and_report_size:
        raise ValueError(
            "Quote missing signature section. "
            "Ensure QGS is properly configured with DCAP."
        )


def get_tdx_quote(report_data: bytes = b'\x00' * 64) -> bytes:
    """
    Generate an Intel-signed TDX quote using configfs-tsm.

    This function ONLY uses the modern configfs-tsm interface (kernel 6.7+)
    which requires a QGS backend to produce Intel-signed quotes.

    The configfs-tsm interface:
    1. Writes report_data to /sys/kernel/config/tsm/report/<id>/inblob
    2. Reads the signed quote from /sys/kernel/config/tsm/report/<id>/outblob
    3. The kernel routes this through the configured attestation backend (QGS)

    Args:
        report_data: 64 bytes of user data to include in the quote

    Returns:
        Raw TDX quote bytes with Intel signature

    Raises:
        RuntimeError: If configfs-tsm is not available
        ValueError: If the returned quote lacks Intel signature
    """
    tsm_path = Path("/sys/kernel/config/tsm/report")

    if not tsm_path.exists():
        raise RuntimeError(
            "configfs-tsm not available at /sys/kernel/config/tsm/report. "
            "Requirements:\n"
            "  - Linux kernel 6.7+ with CONFIG_TSM_REPORTS=y\n"
            "  - TSM configfs mounted: mount -t configfs none /sys/kernel/config\n"
            "  - QGS (Quote Generation Service) running for Intel signatures"
        )

    # Create a unique report request directory
    import time
    report_id = f"report_{os.getpid()}_{int(time.time() * 1000)}"
    report_dir = tsm_path / report_id

    try:
        report_dir.mkdir(exist_ok=False)
    except FileExistsError:
        # Use existing directory if creation fails
        report_dirs = list(tsm_path.glob("report*"))
        if not report_dirs:
            raise RuntimeError("Cannot create or find TSM report directory")
        report_dir = report_dirs[0]

    try:
        # Write report data
        inblob_path = report_dir / "inblob"
        inblob_path.write_bytes(report_data.ljust(64, b'\x00')[:64])

        # Read the quote (this triggers QGS to generate Intel-signed quote)
        outblob_path = report_dir / "outblob"
        quote = outblob_path.read_bytes()

        # Validate the quote has proper Intel signature structure
        validate_intel_signature(quote)

        return quote

    finally:
        # Clean up the report directory
        try:
            report_dir.rmdir()
        except OSError:
            pass  # Directory may not be empty or already removed


def parse_measurements(quote_or_report: bytes) -> dict:
    """
    Extract measurements from TDX quote or TD report.

    TD Report structure (simplified):
    - Offset 0x00: REPORTMACSTRUCT (256 bytes)
    - Offset 0x100: TEE_TCB_INFO (239 bytes)
    - Offset 0x1EF: Reserved
    - Offset 0x200: REPORTDATA (64 bytes)
    - Offset 0x240: MROWNER (48 bytes)
    - Offset 0x270: MROWNERCONFIG (48 bytes)
    - Offset 0x2A0: RTMR0 (48 bytes)
    - Offset 0x2D0: RTMR1 (48 bytes)
    - Offset 0x300: RTMR2 (48 bytes)
    - Offset 0x330: RTMR3 (48 bytes)

    Returns:
        Dictionary with measurement values
    """
    # Skip quote header (48 bytes) if this looks like a full quote
    if len(quote_or_report) > 1024:
        offset = 48  # Quote header size
    else:
        offset = 0

    # RTMR offsets within TD Report
    rtmr_base = offset + 0x2A0
    rtmr_size = 48

    if len(quote_or_report) < rtmr_base + (4 * rtmr_size):
        raise ValueError(f"Quote too small to contain RTMRs: {len(quote_or_report)} bytes")

    return {
        "rtmr0": quote_or_report[rtmr_base:rtmr_base+rtmr_size].hex(),
        "rtmr1": quote_or_report[rtmr_base+rtmr_size:rtmr_base+2*rtmr_size].hex(),
        "rtmr2": quote_or_report[rtmr_base+2*rtmr_size:rtmr_base+3*rtmr_size].hex(),
        "rtmr3": quote_or_report[rtmr_base+3*rtmr_size:rtmr_base+4*rtmr_size].hex(),
    }


def main():
    """Generate quote and output for GitHub Actions."""

    # Generate some unique report data (could include commit SHA, timestamp, etc.)
    commit_sha = os.environ.get('GITHUB_SHA', 'unknown')[:32]
    report_data = commit_sha.encode().ljust(64, b'\x00')[:64]

    # Generate quote - fail if not possible
    quote_bytes = get_tdx_quote(report_data)
    quote_b64 = base64.b64encode(quote_bytes).decode('utf-8')

    # Extract measurements
    measurements = parse_measurements(quote_bytes)
    measurements_json = json.dumps(measurements)

    # Output for GitHub Actions
    github_output = os.environ.get('GITHUB_OUTPUT', '')
    if github_output:
        with open(github_output, 'a') as f:
            f.write(f"quote={quote_b64}\n")
            f.write(f"measurements={measurements_json}\n")

    print(f"Generated TDX quote ({len(quote_bytes)} bytes)")
    print(f"Measurements: {measurements_json}")


if __name__ == '__main__':
    main()
