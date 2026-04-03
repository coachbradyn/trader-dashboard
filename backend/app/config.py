from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    admin_secret: str = ""  # MUST be set via ADMIN_SECRET env var
    dashboard_api_key: str = ""  # API key for frontend auth — set via DASHBOARD_API_KEY env var
    allowed_origins: str = "http://localhost:3000"

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://data.alpaca.markets"

    # AI service
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    ai_routing_mode: str = "dual"  # "dual", "claude_only", "gemini_only"

    # Financial data
    fmp_api_key: str = ""  # Financial Modeling Prep API key

    # Price polling
    price_poll_interval_market: int = 15  # seconds during market hours
    price_poll_interval_closed: int = 60  # seconds outside market hours

    class Config:
        env_file = ".env"

    @property
    def async_database_url(self) -> str:
        """Convert standard postgres URL to async-compatible URL."""
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
