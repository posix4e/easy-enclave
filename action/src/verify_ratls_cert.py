#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sdk"))

from easyenclave.ratls import verify_ratls_cert  # noqa: E402


def fetch_peer_cert(url: str, timeout: float) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "wss"}:
        raise ValueError("URL must use https or wss")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    if not host:
        raise ValueError("URL missing hostname")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls_sock:
            return tls_sock.getpeercert(binary_form=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify RA-TLS certificate with DCAP")
    parser.add_argument("--url", required=True, help="Agent URL (https://host:port)")
    parser.add_argument("--allowlist", required=True, help="Path to allowlist JSON")
    parser.add_argument("--skip-pccs", action="store_true", help="Skip PCCS verification")
    parser.add_argument("--timeout", type=float, default=10.0, help="TLS connect timeout")
    parser.add_argument("--output-cert", required=True, help="Path to write PEM cert")
    parser.add_argument("--output-pin", help="Path to write curl --pinnedpubkey value")
    args = parser.parse_args()

    allowlist_json = json.loads(Path(args.allowlist).read_text(encoding="utf-8"))

    cert_der = fetch_peer_cert(args.url, args.timeout)
    result = verify_ratls_cert(
        cert_der,
        allowlist_json,
        skip_pccs=args.skip_pccs,
        require_allowlist=True,
    )
    if not result.verified:
        print(f"RA-TLS verification failed: {result.reason}", file=sys.stderr)
        return 2

    cert = x509.load_der_x509_certificate(cert_der)
    pem = cert.public_bytes(serialization.Encoding.PEM)
    Path(args.output_cert).write_bytes(pem)
    if args.output_pin:
        spki = cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        pin = "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode()
        Path(args.output_pin).write_text(pin, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
