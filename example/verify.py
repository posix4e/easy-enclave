#!/usr/bin/env python3
"""
Example: Verify and connect to an Easy Enclave service.

Usage:
    python verify.py owner/repo
    python verify.py posix4e/easy-enclave
"""

import sys
import requests


def main():
    if len(sys.argv) < 2:
        print("Usage: python verify.py <owner/repo>")
        print("Example: python verify.py posix4e/easy-enclave")
        sys.exit(1)

    repo = sys.argv[1]
    print(f"Connecting to: {repo}")

    # Import SDK
    try:
        from easyenclave import connect
    except ImportError:
        print("Installing easyenclave SDK...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", "../sdk"], check=True)
        from easyenclave import connect

    # Connect and verify
    try:
        endpoint = connect(repo)
        print(f"\n✓ Verification successful!")
        print(f"  Endpoint: {endpoint.endpoint}")
        print(f"  Measurements: {endpoint.measurements}")
        print(f"  Release: {endpoint.release}")

        # Test the endpoint
        print(f"\nTesting endpoint...")
        response = requests.get(endpoint.endpoint, timeout=5)
        print(f"  Status: {response.status_code}")
        print(f"  Response: {response.text[:200]}")

    except Exception as e:
        print(f"\n✗ Verification failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
