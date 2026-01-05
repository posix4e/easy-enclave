#!/usr/bin/env python3
"""
Example: Verify and connect to an Easy Enclave service.

Usage:
    python verify.py owner/repo
    python verify.py posix4e/easy-enclave
"""

import sys

import requests
from easyenclave import connect


def main():
    if len(sys.argv) < 2:
        print("Usage: python verify.py <owner/repo>")
        print("Example: python verify.py posix4e/easy-enclave")
        sys.exit(1)

    repo = sys.argv[1]
    print(f"Connecting to: {repo}")

    endpoint = connect(repo)
    print(f"\n✓ Verification successful!")
    print(f"  Endpoint: {endpoint.endpoint}")
    print(f"  Measurements: {endpoint.measurements}")
    print(f"  Release: {endpoint.release}")

    # Test the endpoint
    print("\nTesting endpoint...")
    response = requests.get(endpoint.endpoint, timeout=10)
    print(f"  Status: {response.status_code}")
    print(f"  Response: {response.text[:200]}")

if __name__ == "__main__":
    main()
