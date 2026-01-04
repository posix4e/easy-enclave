"""
Easy Enclave exceptions.
"""


class EasyEnclaveError(Exception):
    """Base exception for Easy Enclave SDK."""
    pass


class AttestationNotFoundError(EasyEnclaveError):
    """No attestation found for the repository."""
    pass


class DCAPError(EasyEnclaveError):
    """TDX quote verification failed via Intel PCCS."""

    def __init__(self, message: str, quote: bytes = None):
        super().__init__(message)
        self.quote = quote


class MeasurementError(EasyEnclaveError):
    """Measurements do not match expected values."""

    def __init__(self, message: str, expected: dict = None, actual: dict = None):
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class VerificationError(EasyEnclaveError):
    """General verification error."""
    pass
