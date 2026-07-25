from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

SESSION_COOKIE_NAME = "admin_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.admin_api_key, salt="admin-panel-session")


def create_session_cookie_value() -> str:
    return _serializer().dumps({"ok": True})


def verify_session_cookie(value: str) -> bool:
    try:
        _serializer().loads(value, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
