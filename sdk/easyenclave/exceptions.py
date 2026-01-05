"""
Easy Enclave exceptions.
"""

from typing import Optional


class EasyEnclaveError(Exception):
    """Base exception for Easy Enclave SDK."""
    pass


class AttestationNotFoundError(EasyEnclaveError):
    """No attestation found for the repository."""
    pass


class DCAPError(EasyEnclaveError):
    """TDX quote verification failed via Intel PCCS."""

    def __init__(self, message: str, quote: Optional[bytes] = None):
        super().__init__(message)
        self.quote = quote


class MeasurementError(EasyEnclaveError):
    """Measurements do not match expected values."""

    def __init__(self, message: str, expected: Optional[dict] = None, actual: Optional[dict] = None):
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class VerificationError(EasyEnclaveError):
    """General verification error."""
    pass
