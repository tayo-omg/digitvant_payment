from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./fintech.db"
    bank_api_base_url: str = ""          # empty = use mock name enquiry (dev only)
    bank_api_key: str = ""
    internal_api_key: str = "change-me"  # for service-to-service calls from the core bank app
    jwt_public_key: str = ""
    jwt_algorithm: str = "RS256"
    jwt_audience: str | None = None
    webhook_secret: str = "change-me"
    verification_ttl_seconds: int = 300
    max_upload_bytes: int = 5_000_000
    subscription_amount_tolerance: float = 0.15  # USD-billed subs drift with FX.


settings = Settings()
