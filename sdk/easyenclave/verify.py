"""
TDX quote verification via Intel PCCS (DCAP).
"""

import base64
from typing import Optional
import requests

from .exceptions import DCAPError, VerificationError


# Intel PCCS endpoints
INTEL_PCCS_URL = "https://api.trustedservices.intel.com/sgx/certification/v4"


def verify_quote(quote_b64: str, expected_measurements: Optional[dict] = None) -> dict:
    """
    Verify a TDX quote against Intel PCCS.

    Args:
        quote_b64: Base64-encoded TDX quote
        expected_measurements: Optional dict of expected RTMR values

    Returns:
        Dictionary with verification result and extracted data

    Raises:
        DCAPError: If quote verification fails
    """
    try:
        quote_bytes = base64.b64decode(quote_b64)
    except Exception as e:
        raise DCAPError(f"Invalid quote encoding: {e}")

    if len(quote_bytes) < 48:
        raise DCAPError("Quote too short to be valid")

    # TODO: Implement full DCAP verification
    # For CRAWL phase, we do basic structural validation
    # Full verification requires:
    # 1. Parse quote structure
    # 2. Extract FMSPC from quote
    # 3. Fetch TCB info and QE identity from Intel PCCS
    # 4. Verify quote signature
    # 5. Check TCB status

    result = {
        "verified": False,
        "quote_size": len(quote_bytes),
        "measurements": {},
        "tcb_status": "unknown",
    }

    # Parse basic quote header (simplified)
    # TDX quote structure: header (48 bytes) + body + signature
    try:
        # Version (2 bytes) + Attestation Key Type (2 bytes) + TEE Type (4 bytes)
        version = int.from_bytes(quote_bytes[0:2], 'little')
        tee_type = int.from_bytes(quote_bytes[4:8], 'little')

        if tee_type != 0x81:  # TDX TEE type
            raise DCAPError(f"Not a TDX quote (TEE type: {tee_type:#x})")

        result["version"] = version
        result["tee_type"] = "TDX"

        # For now, mark as verified if structure is valid
        # TODO: Add actual cryptographic verification
        result["verified"] = True
        result["tcb_status"] = "pending_full_verification"

    except Exception as e:
        raise DCAPError(f"Failed to parse quote: {e}", quote_bytes)

    return result


def verify_with_pccs(quote_bytes: bytes) -> dict:
    """
    Verify quote using Intel PCCS API.

    This performs full remote attestation verification.

    Args:
        quote_bytes: Raw TDX quote bytes

    Returns:
        Verification result from PCCS
    """
    # TODO: Implement full PCCS verification
    # This requires:
    # 1. POST quote to PCCS verification endpoint
    # 2. Parse response for TCB status
    # 3. Handle various TCB levels (UpToDate, OutOfDate, etc.)

    raise NotImplementedError("Full PCCS verification not yet implemented")


def extract_measurements(quote_bytes: bytes) -> dict:
    """
    Extract RTMR measurements from a TDX quote.

    Args:
        quote_bytes: Raw TDX quote bytes

    Returns:
        Dictionary with RTMR values
    """
    # TODO: Parse actual RTMR values from quote body
    # RTMRs are in the TD Report structure within the quote

    return {
        "rtmr0": "pending",
        "rtmr1": "pending",
        "rtmr2": "pending",
        "rtmr3": "pending",
    }
