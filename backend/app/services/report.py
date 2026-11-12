"""Performance report service (PS-03 §3.7).

Replays every discharge profile's scenario offline, through both the proposed
monitor and the naive fixed-threshold comparator, over identical seeded
readings. The result is cached: the inputs are deterministic, so recomputing
on every dashboard poll would waste time without changing the answer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.config import Settings
from backend.app.services.clinical import ClinicalService
from backend.app.services.replay import script_for, subject_profile_for
from clinical.comparator import build_report, evaluate_readings
from clinical.profiles import DischargeProfile
from digital_twin.types import Observation
from simulator.bidmc import BidmcReplay, load_records
from simulator.wearable import VirtualWearable

log = logging.getLogger("ps03.report")

REPORT_START = datetime(2026, 11, 5, 8, 0, 0)


def readings_for(
    profile: DischargeProfile, index: int, settings: Settings
) -> tuple[list[Observation], float | None, str, float]:
    """Generate the full deterministic reading series for one profile."""
    if profile.scenario == "bidmc_replay":
        records = load_records(Path(settings.bidmc_data_dir))
        replay = BidmcReplay(
            [records[index % len(records)]],
            patient_id=profile.patient_id,
            speed=1.0,
            start=REPORT_START,
            loop=False,
        )
        return list(replay.observations()), None, "bidmc", 1.0

    script = script_for(profile)
    wearable = VirtualWearable(
        subject_profile_for(profile),
        script,
        seed=settings.demo_seed + index,
        start=REPORT_START,
        rate_s=settings.clinical_sim_seconds_per_tick,
    )
    readings: list[Observation] = []
    while not wearable.finished:
        obs = wearable.next()
        if obs is not None:
            readings.append(obs)
    return readings, script.deterioration_onset_s, "simulator", settings.clinical_sim_seconds_per_tick


class ReportService:
    def __init__(self, clinical: ClinicalService, settings: Settings) -> None:
        self.clinical = clinical
        self.settings = settings
        self._cache: dict[str, Any] | None = None

    def invalidate(self) -> None:
        self._cache = None

    def generate(self, refresh: bool = False) -> dict[str, Any]:
        if self._cache is not None and not refresh:
            return self._cache
        evaluations = []
        errors: list[dict[str, str]] = []
        for index, state in enumerate(self.clinical.patients.values()):
            profile = state.profile
            try:
                readings, onset_s, source, interval_s = readings_for(profile, index, self.settings)
            except (FileNotFoundError, ValueError, KeyError) as exc:
                log.warning("report skipped %s: %s", profile.patient_id, exc)
                errors.append({"patient_id": profile.patient_id, "detail": str(exc)})
                continue
            if not readings:
                continue
            evaluations.append(
                evaluate_readings(
                    profile, readings, onset_s=onset_s, source=source, interval_s=interval_s
                )
            )
        report = build_report(evaluations)
        report["errors"] = errors
        report["generated_at"] = datetime.now().isoformat(timespec="seconds")
        self._cache = report
        return report