"""Runtime configuration, read from environment variables (prefix ``DHT_``).

See .env.example for every setting. No secrets are needed to run the
prototype; the settings only control paths and demo behaviour.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DHT_", env_file=".env", extra="ignore")

    app_name: str = "Human Health Digital Twin"
    # v2 schema (optional vitals, uploaded twins); a fresh file avoids clashing with older databases.
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'twin_v2.db'}"
    # Seed a synthetic subject with 7 days of history when the DB is empty.
    seed_on_startup: bool = True
    history_days: int = 7
    history_interval_s: float = 300.0
    history_seed: int = 7
    demo_seed: int = 42
    # Live demo: one observation every tick; each tick advances twin time.
    tick_seconds: float = 1.0
    twin_seconds_per_tick: float = 15.0
    autostart_live: bool = True
    replay_file: str = str(REPO_ROOT / "data" / "demo_recording.jsonl")
    # Where the built frontend lives (served by FastAPI in Docker/production).
    frontend_dist: str = str(REPO_ROOT / "frontend" / "dist")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    log_level: str = "INFO"

    # --- PS-03 clinical monitoring ---
    hapi_base_url: str = "http://localhost:8080/fhir"
    clinical_profile_dir: str = str(REPO_ROOT / "data" / "profiles")
    bidmc_data_dir: str = str(REPO_ROOT / "data" / "bidmc")
    # Clinical scenario replay is started explicitly, never on boot.
    clinical_autostart: bool = False
    clinical_tick_seconds: float = 0.25
    clinical_sim_seconds_per_tick: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
