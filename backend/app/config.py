from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_model: str = "qwen/qwen3-32b"

    hindsight_api_key: str = ""
    hindsight_base_url: str = ""
    hindsight_bank_id: str = "acme-logistics-finops"

    data_dir: Path = ROOT / "data"
    database_path: Path = ROOT / "data" / "spendsight.db"

    # Anomaly detection
    baseline_days: int = 14
    z_threshold: float = 3.0
    min_delta_usd: float = 150.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
