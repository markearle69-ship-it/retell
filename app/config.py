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

    # Social auto-provisioning: content generation + scheduling for each
    # site's socials, mirroring the Retell voice-agent auto-provisioning above.
    #
    # Ayrshare (https://ayrshare.com) - one account manages every site's
    # social profiles via API. ayrshare_api_key is your Business Plan master
    # key; each site gets its own "profile" (see social_provisioning.py),
    # keyed by ayrshare_profile_key stored on the Tenant row.
    ayrshare_api_key: str = ""
    ayrshare_api_base: str = "https://app.ayrshare.com/api"

    # Anthropic (Claude) - generates on-brand, niche+location-varied caption
    # copy per post so 60+ sites in the same niche don't read as duplicates.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Bannerbear (https://bannerbear.com) - renders branded creative images
    # from a template (logo/colors/text layers) per post, so nobody's
    # hand-designing images 60 times. bannerbear_template_uid is the default
    # template used when a niche doesn't set its own.
    bannerbear_api_key: str = ""
    bannerbear_api_base: str = "https://api.bannerbear.com/v2"
    bannerbear_template_uid: str = ""

    # Scheduling defaults, used when a niche template leaves its own blank.
    # Platform names follow Ayrshare's convention, e.g.:
    # facebook, instagram, linkedin, twitter, gmb (Google Business Profile).
    #
    # GMB deliberately isn't in the default list: Google requires a verified
    # physical/service-area business tied to a real, contactable business
    # before it'll accept a Business Profile, and mass-created listings on
    # rank-and-rent sites get suspended quickly. Add "gmb" per niche (or here)
    # only for sites where you actually hold a verified listing.
    social_default_platforms: str = "facebook,instagram"
    # How many upcoming posts "Provision social" tops the queue up to, per run.
    social_posts_per_batch: int = 6
    # Spacing between a site's scheduled posts.
    social_post_interval_hours: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
