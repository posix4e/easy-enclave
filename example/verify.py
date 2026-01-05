#!/usr/bin/env python3
"""
Example: Verify and connect to an Easy Enclave service.

Usage:
    python verify.py owner/repo
    python verify.py posix4e/easy-enclave
"""

import os
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

        # Test the endpoint (skip in CI - VM is on private network)
        if os.environ.get("CI"):
            print("\nSkipping endpoint test (CI environment, private network)")
        else:
            print("\nTesting endpoint...")
            try:
                response = requests.get(endpoint.endpoint, timeout=5)
                print(f"  Status: {response.status_code}")
                print(f"  Response: {response.text[:200]}")
            except requests.exceptions.RequestException as e:
                print(f"  Warning: Could not reach endpoint: {e}")
                print("  (This is expected if VM is on a private network)")

    except Exception as e:
        print(f"\n✗ Verification failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
