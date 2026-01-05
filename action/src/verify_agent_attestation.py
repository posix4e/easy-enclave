#!/usr/bin/env python3
import argparse
import json
import sys
from urllib.request import Request, urlopen

from easyenclave.verify import verify_quote


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def fetch_attestation(url: str) -> dict:
    req = Request(url)
    with urlopen(req) as response:
        return json.loads(response.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify agent attestation")
    parser.add_argument("--allowlist", required=True, help="Path to allowlist JSON")
    parser.add_argument("--attestation-url", required=True, help="Agent attestation URL")
    parser.add_argument("--skip-pccs", action="store_true", help="Skip PCCS verification")
    args = parser.parse_args()

    allowlist = load_json(args.allowlist)
    expected = allowlist.get("measurements", {})
    if "vm_image_id" not in expected:
        print("Allowlist missing vm_image_id", file=sys.stderr)
        return 2

    attestation = fetch_attestation(args.attestation_url)
    quote = attestation.get("quote")
    if not quote:
        print("Attestation missing quote", file=sys.stderr)
        return 2

    measured = attestation.get("measurements", {})
    for key, value in expected.items():
        if measured.get(key) != value:
            print(f"Measurement mismatch: {key}", file=sys.stderr)
            print(f"expected={value}", file=sys.stderr)
            print(f"actual={measured.get(key)}", file=sys.stderr)
            return 3

    result = verify_quote(quote, skip_pccs=args.skip_pccs)
    report_data = result["measurements"].get("report_data")
    if not report_data:
        print("Quote missing report_data", file=sys.stderr)
        return 4

    expected_report = allowlist.get("report_data")
    if expected_report and report_data.lower() != expected_report.lower():
        print("report_data mismatch", file=sys.stderr)
        print(f"expected={expected_report}", file=sys.stderr)
        print(f"actual={report_data}", file=sys.stderr)
        return 5

    print("Agent attestation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
