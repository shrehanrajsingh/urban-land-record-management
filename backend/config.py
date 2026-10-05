from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg2://cadastral:cadastral@localhost:5433/cadastral_fusion"
    )
    data_dir: str = "./data"
    ai_service_role: str = "AI_SERVICE"
    auto_match_threshold: float = 0.90
    audit_sample_rate: float = 0.10
    coverage_threshold: float = 0.85
    overlap_threshold: float = 0.10
    area_ratio_tolerance: float = 0.15
    default_crs: str = "EPSG:32643"  # UTM 43N (demo)
    web_origin: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
