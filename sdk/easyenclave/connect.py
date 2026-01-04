"""
Main connect() API for Easy Enclave SDK.
"""

from dataclasses import dataclass
from typing import Optional

from .github import get_latest_attestation
from .verify import verify_quote
from .exceptions import (
    EasyEnclaveError,
    AttestationNotFoundError,
    DCAPError,
    MeasurementError,
    VerificationError,
)


@dataclass
class VerifiedEndpoint:
    """Result of successful verification."""

    endpoint: str
    """The verified service endpoint URL."""

    measurements: dict
    """TDX measurements (RTMRs) from the quote."""

    quote: str
    """Base64-encoded TDX quote."""

    repo: str
    """GitHub repository that was verified."""

    release: str
    """Release tag/version."""

    def __str__(self):
        return f"VerifiedEndpoint({self.endpoint})"


def connect(
    repo: str,
    token: Optional[str] = None,
    expected_measurements: Optional[dict] = None,
    skip_verification: bool = False,
) -> VerifiedEndpoint:
    """
    Connect to a TDX-attested service via its GitHub repository.

    This function:
    1. Fetches the latest attestation from the repo's releases
    2. Verifies the TDX quote against Intel PCCS
    3. Returns the verified endpoint information

    Args:
        repo: GitHub repository in "owner/repo" format
        token: Optional GitHub token for private repos
        expected_measurements: Optional dict of expected RTMR values to verify
        skip_verification: Skip DCAP verification (for testing only)

    Returns:
        VerifiedEndpoint with the service endpoint and attestation data

    Raises:
        AttestationNotFoundError: No attestation found for repo
        DCAPError: TDX quote verification failed
        MeasurementError: Measurements don't match expected values
        VerificationError: Other verification errors

    Example:
        >>> from easyenclave import connect
        >>> endpoint = connect("acme/my-service")
        >>> print(endpoint.endpoint)
        https://my-service.acme.com:8443
    """
    # Normalize repo format
    if repo.startswith("github.com/"):
        repo = repo[len("github.com/"):]
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):]

    # Fetch attestation from GitHub
    try:
        attestation = get_latest_attestation(repo, token)
    except AttestationNotFoundError:
        raise
    except Exception as e:
        raise VerificationError(f"Failed to fetch attestation: {e}")

    # Extract attestation data
    quote = attestation.get("quote", "")
    endpoint = attestation.get("endpoint")
    measurements = attestation.get("measurements", {})

    if not endpoint:
        raise VerificationError("Attestation missing endpoint URL")

    # Verify TDX quote
    if not skip_verification and quote:
        try:
            result = verify_quote(quote, expected_measurements)
            if not result.get("verified"):
                raise DCAPError("Quote verification returned false")
        except DCAPError:
            raise
        except Exception as e:
            raise DCAPError(f"Quote verification failed: {e}")

    # Check measurements if expected values provided
    if expected_measurements:
        for key, expected in expected_measurements.items():
            actual = measurements.get(key)
            if actual != expected:
                raise MeasurementError(
                    f"Measurement {key} mismatch: expected {expected}, got {actual}",
                    expected=expected_measurements,
                    actual=measurements,
                )

    return VerifiedEndpoint(
        endpoint=endpoint,
        measurements=measurements,
        quote=quote,
        repo=repo,
        release=attestation.get("timestamp", "unknown"),
    )
