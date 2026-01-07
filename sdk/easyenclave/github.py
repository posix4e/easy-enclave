"""
GitHub API integration for fetching attestations.
"""

import json
from typing import Any, Optional, cast

import requests

from .exceptions import AttestationNotFoundError


def get_latest_attestation(repo: str, token: Optional[str] = None) -> dict[str, Any]:
    """
    Fetch the newest attestation from a GitHub repository's releases.

    Args:
        repo: Repository in "owner/repo" format
        token: Optional GitHub token for private repos

    Returns:
        Attestation dictionary with quote, endpoint, measurements

    Raises:
        AttestationNotFoundError: If no attestation found
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{repo}/releases"
    response = requests.get(url, headers=headers, params={"per_page": 30})

    if response.status_code == 404:
        raise AttestationNotFoundError(f"No releases found for {repo}")

    response.raise_for_status()
    releases = response.json()
    if isinstance(releases, dict):
        releases = [releases]
    if not isinstance(releases, list):
        raise AttestationNotFoundError(f"Unexpected releases payload for {repo}")

    for release in releases:
        if not isinstance(release, dict):
            continue
        # Look for attestation.json asset
        for asset in release.get("assets", []):
            if asset["name"] == "attestation.json":
                asset_url = asset["url"]
                asset_response = requests.get(
                    asset_url,
                    headers={**headers, "Accept": "application/octet-stream"}
                )
                asset_response.raise_for_status()
                return cast(dict[str, Any], asset_response.json())

        # Try to parse from release body
        body = release.get("body", "")
        try:
            start = body.find("```json\n{")
            if start != -1:
                start += 8  # Skip ```json\n
                end = body.find("\n```", start)
                if end != -1:
                    return cast(dict[str, Any], json.loads(body[start:end]))
        except json.JSONDecodeError:
            pass

    raise AttestationNotFoundError(f"no attestation data found for {repo}")


def list_attestations(repo: str, token: Optional[str] = None, limit: int = 10) -> list:
    """
    List recent attestations from a repository.

    Args:
        repo: Repository in "owner/repo" format
        token: Optional GitHub token
        limit: Maximum number of attestations to return

    Returns:
        List of attestation dictionaries
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{repo}/releases"
    response = requests.get(url, headers=headers, params={"per_page": limit})
    response.raise_for_status()

    attestations = []
    for release in response.json():
        try:
            # Try to get attestation from each release
            for asset in release.get("assets", []):
                if asset["name"] == "attestation.json":
                    asset_response = requests.get(
                        asset["url"],
                        headers={**headers, "Accept": "application/octet-stream"}
                    )
                    if asset_response.ok:
                        attestations.append(asset_response.json())
                    break
        except Exception:
            continue

    return attestations
