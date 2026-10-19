"""Pytest plugin: shared fixtures. Importing it never touches the database."""
import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_keys() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, pub


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL", "postgresql://northstar:northstar@localhost:55432/postgres"
    )


@contextmanager
def temp_database(pg_admin_url: str) -> Iterator[str]:
    """Create a throwaway database, yield its URL, drop it afterwards."""
    name = f"test_{uuid4().hex[:8]}"
    with psycopg.connect(pg_admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = pg_admin_url.rpartition("/")
    try:
        yield f"{base}/{name}"
    finally:
        with psycopg.connect(pg_admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def fresh_db(pg_admin_url: str):
    with temp_database(pg_admin_url) as url:
        yield url


@pytest.fixture
def other_fresh_db(pg_admin_url: str):
    """A second, independent fresh database (e.g. for determinism checks)."""
    with temp_database(pg_admin_url) as url:
        yield url
