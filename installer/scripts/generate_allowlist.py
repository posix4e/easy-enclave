#!/usr/bin/env python3
import argparse
import base64
import json
import ssl
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from easyenclave.verify import extract_measurements


def fetch_attestation(url: str, insecure: bool) -> dict:
    req = Request(url)
    context = ssl._create_unverified_context() if insecure else None
    with urlopen(req, context=context) as response:
        return json.loads(response.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate agent attestation allowlist")
    parser.add_argument("--attestation-url", default="http://localhost:8000/attestation")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS verification for attestation URL")
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--output", default="agent-attestation-allowlist.json")
    args = parser.parse_args()

    attestation = fetch_attestation(args.attestation_url, args.insecure)
    measurements = attestation.get("measurements", {})
    report_data = attestation.get("report_data", "")
    quote = attestation.get("quote", "")
    if not measurements or not report_data:
        raise SystemExit("Attestation missing measurements or report_data")
    if not quote:
        raise SystemExit("Attestation missing quote")

    try:
        quote_bytes = base64.b64decode(quote)
        quote_measurements = extract_measurements(quote_bytes)
        quote_measurements.pop("report_data", None)
    except Exception as exc:
        raise SystemExit(f"Failed to extract quote measurements: {exc}")

    allowlist = {
        "version": "1.0",
        "release_tag": args.release_tag,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "measurements": measurements,
        "report_data": report_data,
        "quote_measurements": quote_measurements,
    }

    with open(args.output, "w") as f:
        json.dump(allowlist, f, indent=2)

    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
