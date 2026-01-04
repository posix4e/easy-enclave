#!/usr/bin/env python3
"""
Run workloads on TDX host with attestation.

This module runs docker-compose workloads directly on the TDX host
and generates TDX quotes using the host's direct access to QGS.
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def get_tdx_info() -> dict:
    """Get TDX capability information from the host."""
    info = {
        "tdx_available": False,
        "configfs_tsm": False,
        "dev_tdx_guest": False,
        "qgs_socket": False,
    }

    # Check /dev/tdx_guest
    if Path("/dev/tdx_guest").exists():
        info["dev_tdx_guest"] = True
        info["tdx_available"] = True

    # Check configfs-tsm
    tsm_path = Path("/sys/kernel/config/tsm/report")
    if tsm_path.exists():
        info["configfs_tsm"] = True
        info["tdx_available"] = True

    # Check QGS socket
    qgs_socket = Path("/var/run/tdx-qgs/qgs.socket")
    if qgs_socket.exists():
        info["qgs_socket"] = True

    return info


def run_docker_compose(compose_file: str) -> None:
    """Run docker-compose up for the workload."""
    compose_path = Path(compose_file)
    if not compose_path.exists():
        raise FileNotFoundError(f"Compose file not found: {compose_file}")

    print(f"Starting workload from {compose_file}...")

    # Stop any existing containers from this compose file
    subprocess.run(
        ["docker-compose", "-f", str(compose_path), "down", "--remove-orphans"],
        capture_output=True,
        cwd=compose_path.parent
    )

    # Start the workload
    result = subprocess.run(
        ["docker-compose", "-f", str(compose_path), "up", "-d"],
        capture_output=True,
        text=True,
        cwd=compose_path.parent
    )

    if result.returncode != 0:
        print(f"docker-compose stderr: {result.stderr}")
        raise RuntimeError(f"Failed to start workload: {result.stderr}")

    print("Workload started")


def wait_for_service(port: int = 8080, timeout: int = 60) -> None:
    """Wait for the service to be ready on the specified port."""
    print(f"Waiting for service on port {port}...")
    start = time.time()

    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            if result == 0:
                print(f"Service ready on port {port}")
                return
        except Exception:
            pass
        time.sleep(1)

    raise TimeoutError(f"Service not ready on port {port} within {timeout}s")


def get_quote() -> str:
    """Generate TDX quote from the host and return base64-encoded."""
    from quote import get_tdx_quote

    # Use commit SHA as report data if available
    commit_sha = os.environ.get('GITHUB_SHA', 'unknown')[:32]
    report_data = commit_sha.encode().ljust(64, b'\x00')[:64]

    quote_bytes = get_tdx_quote(report_data)
    return base64.b64encode(quote_bytes).decode('utf-8')


def run_workload(compose_file: str) -> dict:
    """
    Run the full workload flow:
    1. Check TDX availability
    2. Start docker-compose workload
    3. Wait for service
    4. Generate quote

    Returns dict with quote and endpoint info.
    """
    # Check TDX
    tdx_info = get_tdx_info()
    print(f"TDX Info: {json.dumps(tdx_info)}")

    if not tdx_info["tdx_available"]:
        raise RuntimeError("TDX not available on this host")

    # Start workload
    run_docker_compose(compose_file)

    # Wait for service
    wait_for_service(port=8080, timeout=60)

    # Generate quote
    print("Generating TDX quote...")
    quote = get_quote()
    print(f"Quote generated ({len(quote)} chars base64)")

    return {
        "success": True,
        "quote": quote,
        "endpoint": "http://127.0.0.1:8080",
        "tdx_info": tdx_info,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python workload.py <docker-compose.yml>")
        sys.exit(1)

    try:
        result = run_workload(sys.argv[1])
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
