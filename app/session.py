from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

SESSION_COOKIE_NAME = "admin_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days

PORTFOLIO_SESSION_COOKIE_NAME = "portfolio_session"
PORTFOLIO_SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days - tenants log in occasionally


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


def _portfolio_serializer() -> URLSafeTimedSerializer:
    # Reuses the same app secret as the admin session, but a different salt -
    # itsdangerous namespaces by salt, so a token signed for one purpose
    # never verifies for the other even with the same underlying key.
    return URLSafeTimedSerializer(settings.admin_api_key, salt="portfolio-session")


def create_portfolio_session_cookie_value(portfolio_id: int) -> str:
    return _portfolio_serializer().dumps({"portfolio_id": portfolio_id})


def verify_portfolio_session_cookie(value: str) -> Optional[int]:
    try:
        data = _portfolio_serializer().loads(value, max_age=PORTFOLIO_SESSION_MAX_AGE)
        return data.get("portfolio_id")
    except (BadSignature, SignatureExpired):
        return None
