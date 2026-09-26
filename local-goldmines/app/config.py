from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Single-user login. Empty by default so login fails CLOSED if unset.
    app_password: str = ""

    database_url: str = "sqlite:///./goldmines.db"

    # Nominatim (OpenStreetMap geocoder) usage policy requires a contact in the User-Agent.
    contact_email: str = ""

    census_api_key: str = ""

    dataforseo_login: str = ""
    dataforseo_password: str = ""
    anthropic_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
