#!/usr/bin/env python3
"""
Generate TDX quote from the host using available methods.

Tries multiple approaches in order:
1. configfs-tsm (modern kernel interface)
2. /dev/tdx_guest (direct device)
3. QGS socket client
"""

import base64
import json
import os
import struct
import subprocess
import sys
from pathlib import Path


def get_tdx_report(report_data: bytes = b'\x00' * 64) -> bytes:
    """
    Get TDX report using /dev/tdx_guest.

    Args:
        report_data: 64 bytes of user data

    Returns:
        Raw TD report bytes
    """
    import fcntl

    # TDX_CMD_GET_REPORT ioctl
    TDX_CMD_GET_REPORT = 0xc0104401

    # Request structure: report_data (64) + tdreport (1024)
    buf = bytearray(report_data.ljust(64, b'\x00')[:64] + b'\x00' * 1024)

    with open('/dev/tdx_guest', 'rb+', buffering=0) as f:
        fcntl.ioctl(f, TDX_CMD_GET_REPORT, buf)

    return bytes(buf[64:])  # Return TD report


def get_quote_via_configfs(report_data: bytes = b'\x00' * 64) -> bytes:
    """
    Get TDX quote using configfs-tsm interface (kernel 6.7+).

    Args:
        report_data: 64 bytes of user data

    Returns:
        Raw TDX quote bytes
    """
    tsm_path = Path("/sys/kernel/config/tsm/report")

    # Create a new report request
    report_dirs = list(tsm_path.glob("report*"))
    if report_dirs:
        report_dir = report_dirs[0]
    else:
        # May need to create via mkdir
        report_dir = tsm_path / "report0"
        report_dir.mkdir(exist_ok=True)

    # Write report data (in hex)
    inblob_path = report_dir / "inblob"
    inblob_path.write_bytes(report_data.ljust(64, b'\x00')[:64])

    # Read the quote
    outblob_path = report_dir / "outblob"
    quote = outblob_path.read_bytes()

    return quote


def get_quote_via_qgs(report_data: bytes = b'\x00' * 64) -> bytes:
    """
    Get TDX quote via QGS (Quote Generation Service).

    Args:
        report_data: 64 bytes of user data

    Returns:
        Raw TDX quote bytes
    """
    import socket

    # QGS socket path
    QGS_SOCKET = "/var/run/aesmd/aesm.socket"

    # First get TD report
    td_report = get_tdx_report(report_data)

    # Connect to QGS and request quote
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(QGS_SOCKET)

    # Build QGS request (simplified - actual protocol may vary)
    # This is a placeholder - actual QGS protocol is more complex
    request = struct.pack("<I", len(td_report)) + td_report
    sock.sendall(request)

    # Read response
    response = b''
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk

    sock.close()
    return response


def get_quote_via_dcap_tool(report_data: bytes = b'\x00' * 64) -> bytes:
    """
    Get TDX quote using Intel's quote generation tools.

    Args:
        report_data: 64 bytes of user data

    Returns:
        Raw TDX quote bytes
    """
    # Try various Intel tools
    tools = [
        ['tdx_quote_generate', '--report-data', report_data.hex()],
        ['quote_generate', '-r', report_data.hex()],
    ]

    for cmd in tools:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=True
            )
            return result.stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

    raise RuntimeError("No DCAP quote generation tool found")


def get_tdx_quote(report_data: bytes = b'\x00' * 64) -> bytes:
    """
    Generate a TDX quote using available methods.

    Tries methods in order of preference.

    Args:
        report_data: 64 bytes of user data to include in the quote

    Returns:
        Raw TDX quote bytes
    """
    errors = []

    # Method 1: configfs-tsm (modern)
    try:
        return get_quote_via_configfs(report_data)
    except Exception as e:
        errors.append(f"configfs-tsm: {e}")

    # Method 2: Direct /dev/tdx_guest + QGS
    try:
        return get_quote_via_qgs(report_data)
    except Exception as e:
        errors.append(f"QGS: {e}")

    # Method 3: DCAP tools
    try:
        return get_quote_via_dcap_tool(report_data)
    except Exception as e:
        errors.append(f"DCAP tools: {e}")

    # Method 4: Just get TD report (for testing)
    try:
        print("Warning: Could not get full quote, returning TD report only")
        return get_tdx_report(report_data)
    except Exception as e:
        errors.append(f"TD report: {e}")

    raise RuntimeError(f"Could not generate TDX quote. Errors: {'; '.join(errors)}")


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
    measurements = {}

    # Try to extract RTMRs from known offsets
    # These offsets are for TD Report within a quote
    # Quote header is typically 48 bytes, then TD Report follows

    try:
        # Skip quote header (48 bytes) if this looks like a full quote
        if len(quote_or_report) > 1024:
            offset = 48  # Quote header size
        else:
            offset = 0

        # RTMR offsets within TD Report
        rtmr_base = offset + 0x2A0
        rtmr_size = 48

        if len(quote_or_report) >= rtmr_base + (4 * rtmr_size):
            measurements["rtmr0"] = quote_or_report[rtmr_base:rtmr_base+rtmr_size].hex()
            measurements["rtmr1"] = quote_or_report[rtmr_base+rtmr_size:rtmr_base+2*rtmr_size].hex()
            measurements["rtmr2"] = quote_or_report[rtmr_base+2*rtmr_size:rtmr_base+3*rtmr_size].hex()
            measurements["rtmr3"] = quote_or_report[rtmr_base+3*rtmr_size:rtmr_base+4*rtmr_size].hex()
    except Exception as e:
        print(f"Warning: Could not parse measurements: {e}")

    return measurements


def main():
    """Generate quote and output for GitHub Actions."""

    # Generate some unique report data (could include commit SHA, timestamp, etc.)
    commit_sha = os.environ.get('GITHUB_SHA', 'unknown')[:32]
    report_data = commit_sha.encode().ljust(64, b'\x00')[:64]

    try:
        # Generate quote
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

    except Exception as e:
        print(f"Error generating quote: {e}", file=sys.stderr)
        print("Falling back to placeholder quote for testing", file=sys.stderr)

        # Output placeholder for testing
        github_output = os.environ.get('GITHUB_OUTPUT', '')
        if github_output:
            with open(github_output, 'a') as f:
                f.write("quote=placeholder-no-tdx-available\n")
                f.write("measurements={}\n")

        # Don't fail the action - let it continue with placeholder
        # sys.exit(1)


if __name__ == '__main__':
    main()
