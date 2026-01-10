"""Live GitHub attestation tests."""
from __future__ import annotations

import os

import pytest
from easyenclave.exceptions import AttestationNotFoundError
from easyenclave.github import get_latest_attestation, list_attestations


def _live_repo() -> tuple[str, str | None]:
    if os.getenv("LIVE_GITHUB_TESTS") != "1":
        pytest.skip("Set LIVE_GITHUB_TESTS=1 to run live GitHub tests.")
    repo = os.getenv("EASYENCLAVE_TEST_REPO") or os.getenv("GITHUB_REPOSITORY")
    if not repo:
        pytest.skip("Set EASYENCLAVE_TEST_REPO or GITHUB_REPOSITORY to run live GitHub tests.")
    token = os.getenv("EASYENCLAVE_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        pytest.skip("Set EASYENCLAVE_GITHUB_TOKEN or GITHUB_TOKEN to run live GitHub tests.")
    return repo, token


def test_get_latest_attestation_live() -> None:
    repo, token = _live_repo()
    try:
        attestation = get_latest_attestation(repo, token=token)
    except AttestationNotFoundError as exc:
        pytest.skip(str(exc))
    assert "quote" in attestation
    assert "endpoint" in attestation


def test_list_attestations_live() -> None:
    repo, token = _live_repo()
    attestations = list_attestations(repo, token=token, limit=5)
    if not attestations:
        pytest.skip("No attestations available in repo.")
    assert all("quote" in attestation for attestation in attestations)
