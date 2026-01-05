"""Tests for GitHub attestation fetching."""
import json
from unittest.mock import MagicMock, patch

import pytest
from easyenclave.exceptions import AttestationNotFoundError
from easyenclave.github import get_latest_attestation, list_attestations


class TestGetLatestAttestation:
    """Tests for get_latest_attestation function."""

    def test_get_attestation_from_asset(self):
        """Test fetching attestation from release asset."""
        mock_release = {
            "tag_name": "deploy-20260105-133352",
            "assets": [{
                "name": "attestation.json",
                "url": "https://api.github.com/repos/owner/repo/releases/assets/123"
            }]
        }
        mock_attestation = {
            "version": "1.0",
            "quote": "BAACAIE...",
            "endpoint": "http://192.168.122.172:8080",
            "timestamp": "2026-01-05T13:33:52Z"
        }

        with patch('requests.get') as mock_get:
            # First call: get release
            release_response = MagicMock()
            release_response.status_code = 200
            release_response.json.return_value = mock_release

            # Second call: get asset
            asset_response = MagicMock()
            asset_response.status_code = 200
            asset_response.json.return_value = mock_attestation

            mock_get.side_effect = [release_response, asset_response]

            result = get_latest_attestation("owner/repo")

            assert result["version"] == "1.0"
            assert result["endpoint"] == "http://192.168.122.172:8080"
            assert "quote" in result

    def test_get_attestation_from_body(self):
        """Test fetching attestation from release body when no asset."""
        mock_attestation = {
            "version": "1.0",
            "quote": "BAACAIE...",
            "endpoint": "http://192.168.122.100:8080"
        }
        mock_release = {
            "tag_name": "deploy-20260105",
            "assets": [],
            "body": f"## Attestation\n```json\n{json.dumps(mock_attestation)}\n```"
        }

        with patch('requests.get') as mock_get:
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = mock_release
            mock_get.return_value = response

            result = get_latest_attestation("owner/repo")

            assert result["version"] == "1.0"
            assert result["endpoint"] == "http://192.168.122.100:8080"

    def test_get_attestation_not_found(self):
        """Test error when no releases exist."""
        with patch('requests.get') as mock_get:
            response = MagicMock()
            response.status_code = 404
            mock_get.return_value = response

            with pytest.raises(AttestationNotFoundError, match="No releases found"):
                get_latest_attestation("owner/repo")

    def test_get_attestation_no_attestation_data(self):
        """Test error when release has no attestation."""
        mock_release = {
            "tag_name": "v1.0.0",
            "assets": [],
            "body": "Just a regular release"
        }

        with patch('requests.get') as mock_get:
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = mock_release
            mock_get.return_value = response

            with pytest.raises(AttestationNotFoundError, match="no attestation data"):
                get_latest_attestation("owner/repo")

    def test_get_attestation_with_token(self):
        """Test that auth token is passed in headers."""
        mock_release = {
            "tag_name": "deploy-test",
            "assets": [{
                "name": "attestation.json",
                "url": "https://api.github.com/assets/123"
            }]
        }

        with patch('requests.get') as mock_get:
            release_response = MagicMock()
            release_response.status_code = 200
            release_response.json.return_value = mock_release

            asset_response = MagicMock()
            asset_response.json.return_value = {"quote": "test"}

            mock_get.side_effect = [release_response, asset_response]

            get_latest_attestation("owner/repo", token="ghp_test123")

            # Verify token was passed
            call_args = mock_get.call_args_list[0]
            assert "Authorization" in call_args.kwargs.get("headers", {}) or \
                   "Authorization" in call_args[1].get("headers", {})


class TestListAttestations:
    """Tests for list_attestations function."""

    def test_list_attestations_success(self):
        """Test listing multiple attestations."""
        mock_releases = [
            {
                "tag_name": "deploy-20260105",
                "assets": [{
                    "name": "attestation.json",
                    "url": "https://api.github.com/assets/1"
                }]
            },
            {
                "tag_name": "deploy-20260104",
                "assets": [{
                    "name": "attestation.json",
                    "url": "https://api.github.com/assets/2"
                }]
            }
        ]

        with patch('requests.get') as mock_get:
            # First call: list releases
            list_response = MagicMock()
            list_response.status_code = 200
            list_response.json.return_value = mock_releases

            # Asset calls
            asset_response1 = MagicMock()
            asset_response1.ok = True
            asset_response1.json.return_value = {"quote": "quote1"}

            asset_response2 = MagicMock()
            asset_response2.ok = True
            asset_response2.json.return_value = {"quote": "quote2"}

            mock_get.side_effect = [list_response, asset_response1, asset_response2]

            result = list_attestations("owner/repo", limit=2)

            assert len(result) == 2
            assert result[0]["quote"] == "quote1"
            assert result[1]["quote"] == "quote2"

    def test_list_attestations_empty(self):
        """Test listing when no attestations exist."""
        with patch('requests.get') as mock_get:
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = []
            mock_get.return_value = response

            result = list_attestations("owner/repo")

            assert result == []

    def test_list_attestations_skips_errors(self):
        """Test that errors for individual releases are skipped."""
        mock_releases = [
            {
                "tag_name": "deploy-1",
                "assets": [{
                    "name": "attestation.json",
                    "url": "https://api.github.com/assets/1"
                }]
            },
            {
                "tag_name": "deploy-2",
                "assets": []  # No attestation asset
            }
        ]

        with patch('requests.get') as mock_get:
            list_response = MagicMock()
            list_response.status_code = 200
            list_response.json.return_value = mock_releases

            asset_response = MagicMock()
            asset_response.ok = True
            asset_response.json.return_value = {"quote": "quote1"}

            mock_get.side_effect = [list_response, asset_response]

            result = list_attestations("owner/repo")

            # Only one attestation should be returned
            assert len(result) == 1
