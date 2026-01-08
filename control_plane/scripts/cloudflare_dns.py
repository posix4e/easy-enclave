#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any, Optional

API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(RuntimeError):
    pass


def api_request(method: str, path: str, token: str, params: dict | None = None, data: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body: Optional[bytes] = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
    try:
        payload = json.loads(raw.decode())
    except Exception as exc:  # pragma: no cover
        raise CloudflareError("invalid_api_response") from exc
    if not payload.get("success", False):
        errors = payload.get("errors") or []
        message = "; ".join(err.get("message", "api_error") for err in errors) or "api_error"
        raise CloudflareError(message)
    return payload


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "easy-enclave"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read().decode().strip()


def detect_public_ipv4() -> str:
    sources = [
        ("https://api.ipify.org", lambda text: text.strip()),
        ("https://ifconfig.me/ip", lambda text: text.strip()),
        (
            "https://1.1.1.1/cdn-cgi/trace",
            lambda text: next(
                (line.split("=", 1)[1].strip() for line in text.splitlines() if line.startswith("ip=")),
                "",
            ),
        ),
    ]
    for url, parser in sources:
        try:
            candidate = parser(fetch_text(url))
            ip = ipaddress.ip_address(candidate)
            if isinstance(ip, ipaddress.IPv4Address):
                return candidate
        except Exception:
            continue
    raise CloudflareError("auto_ip_failed")


def get_zone_id(token: str, zone_name: str) -> str:
    payload = api_request(
        "GET",
        "/zones",
        token,
        params={"name": zone_name, "status": "active", "per_page": 50},
    )
    zones = payload.get("result") or []
    if not zones:
        raise CloudflareError("zone_not_found")
    return zones[0].get("id", "")


def get_zone_name(token: str, zone_id: str) -> str:
    payload = api_request("GET", f"/zones/{zone_id}", token)
    result = payload.get("result") or {}
    name = result.get("name", "")
    if not name:
        raise CloudflareError("zone_not_found")
    return name


def normalize_record_name(host: str, zone: str) -> str:
    host = host.strip()
    zone = zone.strip().strip(".")
    if host in ("@", zone):
        return zone
    if host.endswith(f".{zone}"):
        return host
    return f"{host}.{zone}"


def fetch_records(token: str, zone_id: str, record_type: str, name: str) -> list[dict[str, Any]]:
    payload = api_request(
        "GET",
        f"/zones/{zone_id}/dns_records",
        token,
        params={"type": record_type, "name": name, "per_page": 100},
    )
    return payload.get("result") or []


def ensure_record(
    token: str,
    zone_id: str,
    record_type: str,
    name: str,
    content: str,
    ttl: int,
    proxied: bool,
    dry_run: bool,
) -> None:
    records = fetch_records(token, zone_id, record_type, name)
    if records:
        record = records[0]
        if len(records) > 1:
            print(f"warning: multiple {record_type} records for {name}, updating first only", file=sys.stderr)
        current = {
            "content": record.get("content"),
            "ttl": record.get("ttl"),
            "proxied": record.get("proxied"),
        }
        desired = {"content": content, "ttl": ttl, "proxied": proxied}
        if current == desired:
            print(f"unchanged {record_type} {name} -> {content}")
            return
        if dry_run:
            print(f"update {record_type} {name} -> {content} ttl={ttl} proxied={proxied}")
            return
        api_request(
            "PUT",
            f"/zones/{zone_id}/dns_records/{record.get('id')}",
            token,
            data={"type": record_type, "name": name, "content": content, "ttl": ttl, "proxied": proxied},
        )
        print(f"updated {record_type} {name} -> {content}")
        return
    if dry_run:
        print(f"create {record_type} {name} -> {content} ttl={ttl} proxied={proxied}")
        return
    api_request(
        "POST",
        f"/zones/{zone_id}/dns_records",
        token,
        data={"type": record_type, "name": name, "content": content, "ttl": ttl, "proxied": proxied},
    )
    print(f"created {record_type} {name} -> {content}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Cloudflare DNS for Easy Enclave.")
    parser.add_argument("--api-token", dest="api_token", help="Cloudflare API token")
    parser.add_argument("--zone-id", dest="zone_id", help="Cloudflare zone id")
    parser.add_argument("--zone", dest="zone_name", help="Cloudflare zone name (e.g. easyenclave.com)")
    parser.add_argument("--ip", dest="ipv4", help="IPv4 address for A records")
    parser.add_argument("--ipv6", dest="ipv6", help="IPv6 address for AAAA records")
    parser.add_argument("--auto-ip", action="store_true", help="Auto-detect public IPv4")
    parser.add_argument("--ttl", default="1", help="TTL (1 = auto)")
    parser.add_argument("--control-host", default="control", help="Host for control plane")
    parser.add_argument("--app-wildcard", default="*.app", help="Wildcard host for apps")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without applying")
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument("--proxied", dest="proxied", action="store_true", help="Enable Cloudflare proxy")
    proxy_group.add_argument("--dns-only", dest="proxied", action="store_false", help="Disable Cloudflare proxy")
    parser.set_defaults(proxied=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = args.api_token or os.getenv("CLOUDFLARE_API_TOKEN")
    zone_id = args.zone_id or os.getenv("CLOUDFLARE_ZONE_ID")
    zone_name = args.zone_name or os.getenv("CLOUDFLARE_ZONE")
    if not token:
        raise CloudflareError("missing_api_token")
    if not zone_id and not zone_name:
        raise CloudflareError("missing_zone")
    if args.auto_ip and not args.ipv4:
        args.ipv4 = detect_public_ipv4()
        print(f"detected_ipv4={args.ipv4}")

    if not args.ipv4 and not args.ipv6:
        raise CloudflareError("missing_ip")

    ttl = int(args.ttl)
    proxied = bool(args.proxied)
    if proxied and ttl != 1:
        ttl = 1

    if not zone_id:
        zone_id = get_zone_id(token, zone_name)
    if not zone_name:
        zone_name = get_zone_name(token, zone_id)

    control_name = normalize_record_name(args.control_host, zone_name)
    app_name = normalize_record_name(args.app_wildcard, zone_name)

    if args.ipv4:
        ensure_record(token, zone_id, "A", control_name, args.ipv4, ttl, proxied, args.dry_run)
        ensure_record(token, zone_id, "A", app_name, args.ipv4, ttl, proxied, args.dry_run)
    if args.ipv6:
        ensure_record(token, zone_id, "AAAA", control_name, args.ipv6, ttl, proxied, args.dry_run)
        ensure_record(token, zone_id, "AAAA", app_name, args.ipv6, ttl, proxied, args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CloudflareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
