"""Verify the `x-retell-signature` header Retell attaches to webhook requests.

Per https://docs.retellai.com/features/secure-webhook, the header looks like
`v=<timestamp_ms>,d=<hex_hmac>`. The digest is HMAC-SHA256 over
`raw_body + timestamp` (string concatenation, raw body bytes must NOT be
re-serialized from parsed JSON), keyed with your Retell API key.
"""

import hashlib
import hmac
import re
import time

_SIGNATURE_RE = re.compile(r"v=(\d+),d=(.+)")
_MAX_SKEW_MS = 5 * 60 * 1000


def verify_retell_signature(raw_body: bytes, signature_header: str, api_key: str) -> bool:
    if not signature_header or not api_key:
        return False

    match = _SIGNATURE_RE.match(signature_header)
    if not match:
        return False

    timestamp_str, digest = match.group(1), match.group(2)

    try:
        timestamp_ms = int(timestamp_str)
    except ValueError:
        return False

    if abs(int(time.time() * 1000) - timestamp_ms) > _MAX_SKEW_MS:
        return False

    expected = hmac.new(
        api_key.encode("utf-8"),
        raw_body + timestamp_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, digest)
