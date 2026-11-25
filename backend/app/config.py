import os
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    LLM_API_KEYS: str = Field(
        default="fake_key_1,fake_key_2",
        description="Comma-separated API keys for LLM provider"
    )
    DATABASE_URL: str = Field(
        default="sqlite:///./data/app.db",
        description="SQLite database URL"
    )
    ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173",
        description="Comma-separated CORS allowed origins"
    )
    ENABLE_FAILURE_SIMULATION: bool = Field(
        default=True,
        description="Enable X-Simulate-Failure header hook"
    )
    LLM_MODEL: str = Field(
        default="gemini/gemini-2.0-flash",
        description="LiteLLM model identifier"
    )
    DEFAULT_SYSTEM_PROMPT: str = Field(
        default="You are a helpful and concise AI assistant.",
        description="Default system prompt if none is specified for session"
    )
    LLM_TIMEOUT: float = Field(
        default=30.0,
        description="LLM request timeout in seconds"
    )
    CACHE_TYPE: str = Field(
        default="local",
        description="LiteLLM cache type (local or disk)"
    )
    PORT: int = Field(
        default=8000,
        description="Server port"
    )

    @property
    def key_pool(self) -> List[str]:
        keys = [k.strip() for k in self.LLM_API_KEYS.split(",") if k.strip()]
        if not keys:
            return ["key_placeholder_1", "key_placeholder_2"]
        return keys

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }


settings = Settings()
