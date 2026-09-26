import httpx

from ..config import settings

TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def user_agent() -> str:
    contact = settings.contact_email or "unset"
    return f"LocalGoldmines/1.0 (contact: {contact})"


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": user_agent()})
