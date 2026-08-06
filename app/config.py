from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Retell
    retell_api_key: str = ""

    # This service's own public URL, e.g. https://retell-production-b640.up.railway.app
    # (no trailing slash). Used to set webhook_url explicitly on every agent this
    # service creates, and for what the admin panel displays - both derived from
    # one authoritative value instead of guessing the scheme from the incoming
    # request, which is unreliable behind Railway's reverse proxy.
    public_base_url: str = ""

    # Storage
    database_url: str = "sqlite:///./retell_leads.db"

    # Admin API access (protects /tenants and /leads)
    admin_api_key: str = "change-me"

    # Self-hosted visitor analytics (see app/analytics.py). Keyed separately
    # from admin_api_key: this secret keys the daily-rotating pseudonymous
    # visitor hash, a different purpose from admin auth, so rotating one
    # never silently breaks the other.
    analytics_salt: str = "change-me-analytics-salt"

    # Email notifications. Preferred: Postmark's API (postmark_api_token set) -
    # only needs smtp_from for the sender address, everything else is ignored.
    # Falls back to generic SMTP if postmark_api_token is blank, for any other
    # provider.
    postmark_api_token: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # SMS notifications (Twilio - reuses the account you already have)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Auto-provisioning: the one shared SIP trunk every site's number gets
    # attached to, and the termination credentials configured on it.
    twilio_trunk_sid: str = ""
    twilio_sip_username: str = ""
    twilio_sip_password: str = ""
    retell_allowed_inbound_countries: str = "US,GB"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
