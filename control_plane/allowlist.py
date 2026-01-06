from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen


@dataclass
class AllowlistEntry:
    repo: str
    release_tag: str
    allowlist: dict
    fetched_at: float


class AllowlistCache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._items: dict[tuple[str, str], AllowlistEntry] = {}

    def get(self, repo: str, release_tag: str) -> Optional[dict]:
        entry = self._items.get((repo, release_tag))
        if not entry:
            return None
        if time.time() - entry.fetched_at > self._ttl:
            self._items.pop((repo, release_tag), None)
            return None
        return entry.allowlist

    def put(self, repo: str, release_tag: str, allowlist: dict) -> None:
        self._items[(repo, release_tag)] = AllowlistEntry(
            repo=repo,
            release_tag=release_tag,
            allowlist=allowlist,
            fetched_at=time.time(),
        )


def fetch_allowlist(repo: str, release_tag: str, asset_name: str, token: Optional[str]) -> dict:
    release_url = f"https://api.github.com/repos/{repo}/releases/tags/{release_tag}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "easy-enclave-control-plane",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(release_url, headers=headers)
    with urlopen(req) as response:
        release = json.loads(response.read().decode())

    assets = release.get("assets", [])
    asset_url = None
    for asset in assets:
        if asset.get("name") == asset_name:
            asset_url = asset.get("browser_download_url")
            break

    if not asset_url:
        raise RuntimeError(f"Allowlist asset not found: {asset_name}")

    req = Request(asset_url, headers=headers)
    with urlopen(req) as response:
        return json.loads(response.read().decode())
