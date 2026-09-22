"""Password hashing for Portfolio logins (per-portfolio passwords stored in
the DB, unlike ADMIN_API_KEY which lives only in an env var). Uses stdlib
hashlib.scrypt - no extra dependency, and scrypt is purpose-built for this
(memory-hard, tunable cost) unlike a plain sha256 hash."""

import hashlib
import hmac
import os

_N = 2**14  # CPU/memory cost - RFC 7914's suggested value for interactive logins
_R = 8
_P = 1
_DKLEN = 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False

    actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return hmac.compare_digest(actual, expected)
