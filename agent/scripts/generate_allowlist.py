#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen


def fetch_attestation(url: str) -> dict:
    req = Request(url)
    with urlopen(req) as response:
        return json.loads(response.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate agent attestation allowlist")
    parser.add_argument("--attestation-url", default="http://localhost:8000/attestation")
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--output", default="agent-attestation-allowlist.json")
    args = parser.parse_args()

    attestation = fetch_attestation(args.attestation_url)
    measurements = attestation.get("measurements", {})
    report_data = attestation.get("report_data", "")
    if not measurements or not report_data:
        raise SystemExit("Attestation missing measurements or report_data")

    allowlist = {
        "version": "1.0",
        "release_tag": args.release_tag,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "measurements": measurements,
        "report_data": report_data,
    }

    with open(args.output, "w") as f:
        json.dump(allowlist, f, indent=2)

    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
