#!/usr/bin/env python3
"""
Generate TDX quote from the host using QGS.

For CRAWL phase: generates quote from the TDX host itself.
For WALK phase: will generate quote from workload TD VM.
"""

import base64
import json
import os
import subprocess
import sys


def get_tdx_quote(report_data: bytes = b'\x00' * 64) -> bytes:
    """
    Generate a TDX quote using the local QGS.

    Args:
        report_data: 64 bytes of user data to include in the quote

    Returns:
        Raw TDX quote bytes
    """
    # TODO: Implement actual QGS quote generation
    # For now, try to read from /dev/tdx_guest or use qgs client

    try:
        # Try using the tdx_attest library if available
        import tdx_attest
        quote = tdx_attest.get_quote(report_data)
        return quote
    except ImportError:
        pass

    # Fallback: try qgs command line tool
    try:
        result = subprocess.run(
            ['qgs-client', '--report-data', report_data.hex()],
            capture_output=True,
            check=True
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Last resort: try /dev/tdx_guest directly
    try:
        with open('/dev/tdx_guest', 'rb') as f:
            # This is simplified - actual implementation needs proper ioctl
            quote = f.read()
            return quote
    except (FileNotFoundError, PermissionError):
        pass

    raise RuntimeError("Could not generate TDX quote - no quote mechanism available")


def get_measurements() -> dict:
    """
    Extract measurements (RTMRs) from the TDX quote or TD report.

    Returns:
        Dictionary with RTMR values
    """
    # TODO: Parse actual measurements from quote
    # For CRAWL, return placeholder that will be filled in during testing

    try:
        with open('/sys/kernel/tdx/rtmr0', 'r') as f:
            rtmr0 = f.read().strip()
    except FileNotFoundError:
        rtmr0 = "unavailable"

    return {
        "rtmr0": rtmr0,
        "rtmr1": "unavailable",
        "rtmr2": "unavailable",
        "rtmr3": "unavailable"
    }


def main():
    """Generate quote and output for GitHub Actions."""

    try:
        # Generate quote
        quote_bytes = get_tdx_quote()
        quote_b64 = base64.b64encode(quote_bytes).decode('utf-8')

        # Get measurements
        measurements = get_measurements()
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
        sys.exit(1)


if __name__ == '__main__':
    main()
