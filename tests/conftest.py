"""Shared fixtures. Every test uses its own temporary SQLite database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.factory import create_app
from digital_twin.baseline import compute_baseline
from digital_twin.types import Baseline, Observation
from simulator.generator import SubjectProfile, generate_history

T0 = datetime(2026, 3, 2, 9, 0, 0)


@pytest.fixture(scope="session")
def profile() -> SubjectProfile:
    return SubjectProfile()


@pytest.fixture(scope="session")
def history(profile: SubjectProfile) -> list[Observation]:
    return generate_history(profile, end=T0, days=3, interval_s=300.0, seed=3, include_planned_anomaly=False)


@pytest.fixture(scope="session")
def baseline(history: list[Observation], profile: SubjectProfile) -> Baseline:
    return compute_baseline(history, profile.age_years, profile.utc_offset_hours)


def make_obs(t: datetime, **overrides) -> Observation:
    values = dict(
        timestamp=t,
        heart_rate=66.0,
        spo2=97.7,
        temperature=36.8,
        respiratory_rate=14.0,
        steps=0,
        activity_intensity=0.03,
        is_asleep=False,
    )
    values.update(overrides)
    return Observation(**values)


def series(start: datetime, n: int, step_s: float = 15.0, **overrides) -> list[Observation]:
    return [make_obs(start + timedelta(seconds=step_s * i), **overrides) for i in range(n)]


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        seed_on_startup=True,
        history_days=3,
        autostart_live=False,
        tick_seconds=0.05,
        frontend_dist=str(tmp_path / "no-frontend"),
        replay_file=str(tmp_path / "missing.jsonl"),
        clinical_profile_dir=str(tmp_path / "profiles"),
        bidmc_data_dir=str(tmp_path / "bidmc"),
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
