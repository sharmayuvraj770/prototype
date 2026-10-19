"""JWT signing key bootstrap for the mock IdP."""
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def ensure_keys(keys_dir: Path) -> bool:
    """Write RSA-2048 PEMs if missing. Returns True if created."""
    keys_dir = Path(keys_dir)
    priv_path, pub_path = keys_dir / "jwt_private.pem", keys_dir / "jwt_public.pem"
    if priv_path.exists() and pub_path.exists():
        return False
    keys_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(priv_path, 0o600)  # no-op on Windows
    pub_path.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return True
