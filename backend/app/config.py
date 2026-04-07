"""
CareSync backend configuration.

Reads settings from environment variables with sensible defaults for local
development. In production these would come from a secrets manager.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Database
    mysql_host: str = os.getenv("MYSQL_HOST", "localhost")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "dev")
    mysql_database: str = os.getenv("MYSQL_DATABASE", "caresync")
    mysql_pool_size: int = int(os.getenv("MYSQL_POOL_SIZE", "10"))

    # LLM providers
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    # ASR
    asr_backend: str = os.getenv("ASR_BACKEND", "mock")  # "mock" | "openai_whisper" | "faster_whisper"
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # App
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    max_concurrent_jobs: int = int(os.getenv("MAX_CONCURRENT_JOBS", "4"))


settings = Settings()
