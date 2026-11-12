"""Scenario replay runner: drives every patient's data stream (PS-03 §3.2, §3.7).

Each discharge profile names a scenario. The runner builds the matching source
— a scripted virtual wearable, or a BIDMC recording — steps it on a fixed tick
and submits each reading as a transaction Bundle to the HTTP FHIR ingestion
endpoint through an in-process ASGI transport, then HAPI storage and scoring.

Simulated time is accelerated so a five-day deterioration unfolds in minutes of
presentation time. Ground-truth onset and first-alert time are recorded per
patient, which is what makes detection delay measurable.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from backend.app.config import Settings
from backend.app.services.clinical import ClinicalService
from clinical.fhir import build_reading_bundle
from clinical.monitor import AlertKind
from clinical.profiles import DischargeProfile
from digital_twin.types import Observation
from simulator.bidmc import BidmcReplay, load_records
from simulator.generator import SubjectProfile
from simulator.wearable import SCRIPTS, EventKind, VirtualWearable, WearableScript

log = logging.getLogger("ps03.replay")

MAX_CONSECUTIVE_ERRORS = 5
BIDMC_SAMPLES_PER_TICK = 4


def subject_profile_for(profile: DischargeProfile) -> SubjectProfile:
    """Resting physiology implied by the discharge profile.

    Beta-blockade lowers resting pulse; chronic hypercapnic respiratory failure
    means a lower SpO2 and a slightly higher resting breathing rate. These are
    the patient's *own* normal, which is what the rolling baseline learns.
    """
    return SubjectProfile(
        age_years=float(profile.age_years),
        hr_rest=56.0 if profile.beta_blocked else 64.0,
        rr_rest=15.0 if profile.hypercapnic_respiratory_failure else 13.5,
        spo2=91.0 if profile.hypercapnic_respiratory_failure else 97.5,
        temp=36.6,
    )


def script_for(profile: DischargeProfile) -> WearableScript:
    script = SCRIPTS.get(profile.scenario or "stable_recovery")
    if script is None:
        raise KeyError(f"unknown scenario {profile.scenario!r}; choose one of {sorted(SCRIPTS)}")
    if profile.beta_blocked and script.key == "slow_deterioration":
        # A blunted pulse must still reach the edge of the population band, or
        # the beta-blocker correction would have nothing to demonstrate.
        return replace(
            script,
            events=[
                replace(e, magnitude=e.magnitude * 1.5) if e.kind is EventKind.DRIFT else e
                for e in script.events
            ],
        )
    return script


@dataclass
class PatientStream:
    patient_id: str
    scenario: str
    source: str
    wearable: VirtualWearable | None = None
    replay: BidmcReplay | None = None
    onset_s: float | None = None
    elapsed_s: float = 0.0
    emitted: int = 0
    dropouts: int = 0
    finished: bool = False
    errors: int = 0
    last_error: str | None = None
    first_alert_elapsed_s: float | None = None
    first_alert_at: datetime | None = None
    first_alert_tier: str | None = None
    active_events: list[str] = field(default_factory=list)

    @property
    def detection_delay_s(self) -> float | None:
        if self.onset_s is None or self.first_alert_elapsed_s is None:
            return None
        return self.first_alert_elapsed_s - self.onset_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "scenario": self.scenario,
            "source": self.source,
            "elapsed_s": round(self.elapsed_s, 1),
            "elapsed_hours": round(self.elapsed_s / 3600.0, 2),
            "emitted": self.emitted,
            "dropouts": self.dropouts,
            "finished": self.finished,
            "last_error": self.last_error,
            "active_events": self.active_events,
            "deterioration_onset_s": self.onset_s,
            "first_alert_elapsed_s": self.first_alert_elapsed_s,
            "first_alert_at": self.first_alert_at.isoformat() if self.first_alert_at else None,
            "first_alert_tier": self.first_alert_tier,
            "detection_delay_s": self.detection_delay_s,
            "detection_delay_hours": round(self.detection_delay_s / 3600.0, 2) if self.detection_delay_s is not None else None,
            "bidmc": self.replay.progress if self.replay else None,
        }


class ReplayRunner:
    """One asyncio task stepping every active patient stream."""

    def __init__(
        self,
        clinical: ClinicalService,
        settings: Settings,
        ingestion_client: httpx.AsyncClient,
        on_status=None,
    ) -> None:
        self.clinical = clinical
        self.settings = settings
        self.ingestion_client = ingestion_client
        self.on_status = on_status
        self.streams: dict[str, PatientStream] = {}
        self.running = False
        self.ticks = 0
        self.started_at: datetime | None = None
        self._task: asyncio.Task | None = None
        self._bidmc_dir = Path(settings.bidmc_data_dir)

    # ------------------------------------------------------------- control
    async def start(self, patient_ids: list[str] | None = None) -> dict[str, Any]:
        await self.stop()
        wanted = patient_ids or list(self.clinical.patients)
        if not wanted:
            raise ValueError("no discharge profiles loaded; run scripts/generate_profiles.py")
        unknown = [p for p in wanted if p not in self.clinical.patients]
        if unknown:
            raise KeyError(f"unknown patients: {', '.join(unknown)}")

        start_clock = datetime(2026, 11, 5, 8, 0, 0)
        for index, patient_id in enumerate(wanted):
            profile = self.clinical.patient(patient_id).profile
            self.streams[patient_id] = self._build_stream(profile, index, start_clock)

        self.running = True
        self.ticks = 0
        self.started_at = datetime.now()
        self._task = asyncio.create_task(self._loop())
        self._notify()
        return self.status()

    def _build_stream(
        self, profile: DischargeProfile, index: int, start_clock: datetime
    ) -> PatientStream:
        scenario = profile.scenario or "stable_recovery"
        if scenario == "bidmc_replay":
            records = load_records(self._bidmc_dir)
            # Each replay patient gets a different recording, deterministically.
            chosen = [records[index % len(records)]]
            replay = BidmcReplay(
                chosen,
                patient_id=profile.patient_id,
                speed=1.0,
                start=start_clock,
                loop=True,
            )
            return PatientStream(
                patient_id=profile.patient_id,
                scenario=scenario,
                source="bidmc",
                replay=replay,
            )

        script = script_for(profile)
        wearable = VirtualWearable(
            subject_profile_for(profile),
            script,
            seed=self.settings.demo_seed + index,
            start=start_clock,
            rate_s=self.settings.clinical_sim_seconds_per_tick,
        )
        return PatientStream(
            patient_id=profile.patient_id,
            scenario=script.key,
            source="simulator",
            wearable=wearable,
            onset_s=script.deterioration_onset_s,
        )

    async def stop(self) -> dict[str, Any]:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        was_running = self.running
        self.running = False
        if was_running:
            self._notify()
        return self.status()

    async def reset(self) -> dict[str, Any]:
        """Stop, clear every monitor and alert history, and rebuild the streams."""
        await self.stop()
        self.clinical.reset()
        self.streams.clear()
        self.ticks = 0
        self.started_at = None
        self._notify()
        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ticks": self.ticks,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "tick_seconds": self.settings.clinical_tick_seconds,
            "sim_seconds_per_tick": self.settings.clinical_sim_seconds_per_tick,
            "time_acceleration": round(
                self.settings.clinical_sim_seconds_per_tick / self.settings.clinical_tick_seconds, 1
            ),
            "scenarios": [
                {"key": s.key, "label": s.label, "description": s.description}
                for s in SCRIPTS.values()
            ]
            + [
                {
                    "key": "bidmc_replay",
                    "label": "BIDMC replay",
                    "description": "Recorded ICU heart rate, breathing rate and SpO₂ from PhysioNet.",
                }
            ],
            "streams": [s.to_dict() for s in self.streams.values()],
        }

    # ---------------------------------------------------------------- loop
    async def _loop(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            active = [s for s in self.streams.values() if not s.finished]
            if not active:
                self.running = False
                self._notify()
                return
            await asyncio.gather(*(self._tick(s) for s in active))
            self.ticks += 1
            if self.ticks % 20 == 0:
                self._notify()
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(self.settings.clinical_tick_seconds - elapsed, 0.0))

    async def _tick(self, stream: PatientStream) -> None:
        try:
            if stream.replay is not None:
                await self._tick_replay(stream)
            else:
                await self._tick_wearable(stream)
            stream.errors = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stream.errors += 1
            stream.last_error = str(exc)
            log.exception("replay tick failed for %s (%d/%d)", stream.patient_id, stream.errors, MAX_CONSECUTIVE_ERRORS)
            if stream.errors >= MAX_CONSECUTIVE_ERRORS:
                stream.finished = True
                self._notify()

    async def _tick_wearable(self, stream: PatientStream) -> None:
        wearable = stream.wearable
        assert wearable is not None
        step_s = self.settings.clinical_sim_seconds_per_tick
        stream.active_events = [
            e.kind.value for e in wearable.script.active_events(stream.elapsed_s)
        ]
        obs = wearable.next()
        stream.elapsed_s = wearable.elapsed_s
        if obs is None:
            stream.dropouts += 1
        else:
            await self._ingest(stream, obs)
        if wearable.finished:
            stream.finished = True
            self._notify()

    async def _tick_replay(self, stream: PatientStream) -> None:
        replay = stream.replay
        assert replay is not None
        for _ in range(BIDMC_SAMPLES_PER_TICK):
            item = replay.next()
            if item is None:
                stream.finished = True
                self._notify()
                return
            obs, _delay = item
            await self._ingest(stream, obs)
        stream.elapsed_s = replay.elapsed_s

    async def _ingest(self, stream: PatientStream, obs: Observation) -> None:
        response = await self.ingestion_client.post(
            "/api/fhir/Bundle", json=build_reading_bundle(stream.patient_id, obs)
        )
        response.raise_for_status()
        stream.emitted += 1
        results = response.json()["results"]
        if not results:
            return
        result = results[-1]
        elapsed_s = stream.elapsed_s
        if stream.wearable is not None:
            elapsed_s -= self.settings.clinical_sim_seconds_per_tick
        if (
            stream.first_alert_elapsed_s is None
            and result["alert_kind"] == AlertKind.CLINICAL.value
            and result["tier"] != "NORMAL"
            and (stream.onset_s is None or elapsed_s >= stream.onset_s)
        ):
            stream.first_alert_elapsed_s = elapsed_s
            stream.first_alert_at = obs.timestamp
            stream.first_alert_tier = result["tier"]
            log.info(
                "first clinical alert for %s at %.1f h (detection delay %s)",
                stream.patient_id,
                elapsed_s / 3600.0,
                f"{stream.detection_delay_s / 3600.0:.2f} h" if stream.detection_delay_s is not None else "n/a",
            )
            self._notify()

    def _notify(self) -> None:
        if self.on_status:
            try:
                self.on_status(self.status())
            except Exception:
                log.exception("replay status listener failed")