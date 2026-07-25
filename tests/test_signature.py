import hashlib
import hmac
import time

from app.signature import verify_retell_signature

API_KEY = "test-api-key"


def _sign(body: bytes, timestamp_ms: int, api_key: str = API_KEY) -> str:
    digest = hmac.new(
        api_key.encode("utf-8"),
        body + str(timestamp_ms).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v={timestamp_ms},d={digest}"


def test_valid_signature_passes():
    body = b'{"event":"call_analyzed"}'
    header = _sign(body, int(time.time() * 1000))
    assert verify_retell_signature(body, header, API_KEY) is True


def test_wrong_key_fails():
    body = b'{"event":"call_analyzed"}'
    header = _sign(body, int(time.time() * 1000), api_key="wrong-key")
    assert verify_retell_signature(body, header, API_KEY) is False


def test_tampered_body_fails():
    body = b'{"event":"call_analyzed"}'
    header = _sign(body, int(time.time() * 1000))
    assert verify_retell_signature(b'{"event":"call_ended"}', header, API_KEY) is False


def test_stale_timestamp_fails():
    body = b'{"event":"call_analyzed"}'
    ten_minutes_ago = int(time.time() * 1000) - (10 * 60 * 1000)
    header = _sign(body, ten_minutes_ago)
    assert verify_retell_signature(body, header, API_KEY) is False


def test_malformed_header_fails():
    body = b'{"event":"call_analyzed"}'
    assert verify_retell_signature(body, "not-a-real-header", API_KEY) is False
    assert verify_retell_signature(body, "", API_KEY) is False
