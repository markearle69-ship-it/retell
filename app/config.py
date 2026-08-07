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

    # Rank tracking (scripts/check_rankings.py). Uses SerpApi's Google Search
    # API rather than driving a real/incognito browser - see the script's
    # docstring for why. https://serpapi.com
    serpapi_key: str = ""
    # Where to email a site when its ranking check fails outright or its
    # position gets worse than rank_check_alert_threshold (or drops off the
    # results entirely). This is you, the site operator - not the tenant.
    rank_check_alert_email: str = ""
    rank_check_alert_threshold: int = 10
    rank_check_num_results: int = 100

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
