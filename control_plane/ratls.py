from __future__ import annotations

import os
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from easyenclave.ratls import build_ratls_cert, report_data_for_pubkey


@dataclass
class RatlsMaterial:
    cert_path: Path
    key_path: Path


def get_tdx_quote(report_data: bytes) -> bytes:
    tsm_path = Path("/sys/kernel/config/tsm/report")
    if not tsm_path.exists():
        raise RuntimeError(f"configfs-tsm not available at {tsm_path}")
    report_dir = tempfile.mkdtemp(dir=tsm_path)
    inblob = Path(report_dir) / "inblob"
    outblob = Path(report_dir) / "outblob"
    with open(inblob, "wb") as f:
        f.write(report_data.ljust(64, b"\x00")[:64])
    with open(outblob, "rb") as f:
        data = f.read()
    if len(data) == 0:
        raise RuntimeError("empty quote from configfs-tsm")
    return data


def ensure_ratls_material(common_name: str, ttl_seconds: int) -> RatlsMaterial:
    ratls_dir = Path("/var/lib/easy-enclave/ratls")
    ratls_dir.mkdir(parents=True, exist_ok=True)
    cert_path = ratls_dir / "ratls.crt"
    key_path = ratls_dir / "ratls.key"

    key = ec.generate_private_key(ec.SECP256R1())
    report_data = report_data_for_pubkey(key.public_key())
    quote = get_tdx_quote(report_data)
    cert_pem = build_ratls_cert(quote, key, common_name=common_name, ttl_seconds=ttl_seconds)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    os.chmod(cert_path, 0o600)
    os.chmod(key_path, 0o600)

    return RatlsMaterial(cert_path=cert_path, key_path=key_path)


def build_server_ssl_context(material: RatlsMaterial, require_client_cert: bool) -> ssl.SSLContext:
    context = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_OPTIONAL if require_client_cert else ssl.CERT_NONE
    context.load_cert_chain(certfile=str(material.cert_path), keyfile=str(material.key_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def extract_peer_cert(request) -> bytes:
    transport = request.transport
    if not transport:
        return b""
    ssl_obj = transport.get_extra_info("ssl_object")
    if not ssl_obj:
        return b""
    return ssl_obj.getpeercert(binary_form=True) or b""
