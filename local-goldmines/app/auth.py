import hmac

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

SESSION_COOKIE_NAME = "goldmines_session"
SESSION_MAX_AGE = 30 * 24 * 3600


class NotAuthenticated(Exception):
    """Raised by require_login; an app-level handler redirects to /login."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_password, salt="goldmines-session")


def password_ok(password: str) -> bool:
    # Fails closed when APP_PASSWORD is unset.
    return bool(settings.app_password) and hmac.compare_digest(password, settings.app_password)


def create_session_cookie_value() -> str:
    return _serializer().dumps({"ok": True})


def require_login(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not settings.app_password:
        raise NotAuthenticated()
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise NotAuthenticated()
