from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "change-me-in-production-use-long-random-string"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    database_url: str = "sqlite:////data/internet_manager.db"

    admin_username: str = "admin"
    admin_password: str = "admin"

    mikrotik_host: str = ""
    mikrotik_user: str = ""
    mikrotik_password: str = ""
    mikrotik_port: int = 8728
    mikrotik_use_ssl: bool = False
    mikrotik_plaintext_login: bool = True

    adguard_url: str = ""  # e.g. http://192.168.1.10:3000
    adguard_user: str = ""
    adguard_password: str = ""
    adguard_social_filter_name: str = "Social media block"

    # Social slow mode (kbit/s) – chat often works, video struggles
    social_slow_limit_kbps: int = 256
    timezone: str = "Europe/Bratislava"
    mikrotik_webfig_url: str = ""  # optional e.g. http://192.168.1.1 – for graph links


@lru_cache
def get_settings() -> Settings:
    return Settings()
