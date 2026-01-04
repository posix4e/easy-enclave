"""
Easy Enclave SDK - TDX attestation via GitHub.

Usage:
    from easyenclave import connect

    client = connect("owner/repo")
"""

from .connect import connect
from .exceptions import (
    EasyEnclaveError,
    DCAPError,
    MeasurementError,
    AttestationNotFoundError,
    VerificationError,
)

__version__ = "0.1.0"
__all__ = [
    "connect",
    "EasyEnclaveError",
    "DCAPError",
    "MeasurementError",
    "AttestationNotFoundError",
    "VerificationError",
]
