from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Retell
    retell_api_key: str = ""

    # Storage
    database_url: str = "sqlite:///./retell_leads.db"

    # Admin API access (protects /tenants and /leads)
    admin_api_key: str = "change-me"

    # Email notifications (SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # SMS notifications (Twilio - reuses the account you already have)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
