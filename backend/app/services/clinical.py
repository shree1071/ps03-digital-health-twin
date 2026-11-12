"""Clinical monitoring service: FHIR ingestion -> HAPI -> NEWS2 -> alerts.

Owns the discharge profiles, one :class:`PersonalizedMonitor` per patient, and
the alert history the clinician dashboard reads. Every accepted reading is
stored in HAPI FHIR as a LOINC-coded Vital Signs Observation before it is
scored, so the clinical record and the score always agree.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.services.hub import Hub
from clinical.artifacts import ArtifactConfig, ArtifactDetector
from clinical.fhir import (
    ACTIVITY_EXTENSION,
    MANDATORY_METRICS,
    SOURCE_EXTENSION,
    VITAL_CODINGS,
    build_reading_bundle,
    effective_from_observation,
    metric_from_observation,
    patient_from_observation,
    value_from_observation,
)
from clinical.hapi_client import HapiClient, HapiError
from clinical.monitor import AlertKind, MonitorResult, PersonalizedMonitor
from clinical.profiles import DischargeProfile
from digital_twin.types import Observation

log = logging.getLogger("ps03.clinical")

PROFILE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "profiles"
MAX_ALERTS = 500
MAX_BUFFER = 2000
# One shared channel carrying every patient's row, for the ward overview.
WARD_CHANNEL = "ward"


@dataclass
class PatientState:
    profile: DischargeProfile
    monitor: PersonalizedMonitor
    latest: MonitorResult | None = None
    alerts: deque[MonitorResult] = field(default_factory=lambda: deque(maxlen=MAX_ALERTS))
    readings: deque[Observation] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER))
    stored_observations: int = 0
    suppressed_count: int = 0
    tier_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    artifact_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_processed: datetime | None = None


class ClinicalService:
    def __init__(self, hub: Hub, hapi_base_url: str, profile_dir: Path = PROFILE_DIR) -> None:
        self.hub = hub
        self.hapi = HapiClient(hapi_base_url)
        self.profile_dir = profile_dir
        self.patients: dict[str, PatientState] = {}
        self._pending: dict[str, dict[datetime, dict[str, float | None]]] = defaultdict(dict)

    # ------------------------------------------------------------- profiles
    def load_profiles(self) -> int:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        paths = sorted(self.profile_dir.glob("*.json"))
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("could not read profile %s: %s", path, exc)
                continue
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                self.register_profile(DischargeProfile.from_dict(entry))
        return len(self.patients)

    async def ensure_patients(self) -> int:
        """Create the FHIR Patient resources; HAPI resolves Observation.subject."""
        for state in self.patients.values():
            await self.hapi.ensure_patient(state.profile)
        return len(self.patients)

    def register_profile(self, profile: DischargeProfile) -> PatientState:
        state = self.patients.get(profile.patient_id)
        if state is None:
            state = PatientState(profile=profile, monitor=_monitor_for(profile))
            self.patients[profile.patient_id] = state
        else:
            state.profile = profile
            state.monitor.profile = profile
        return state

    def patient(self, patient_id: str) -> PatientState:
        if patient_id not in self.patients:
            raise KeyError(f"unknown patient {patient_id!r}")
        return self.patients[patient_id]

    def reset(self) -> None:
        """Drop every monitor, baseline and alert history; keep the profiles."""
        for patient_id, state in list(self.patients.items()):
            self.patients[patient_id] = PatientState(
                profile=state.profile, monitor=_monitor_for(state.profile)
            )
        self._pending.clear()
        for patient_id, state in self.patients.items():
            self.hub.publish(patient_id, {"type": "clinical", "data": self.patient_state_payload(patient_id)})
            self.hub.publish(WARD_CHANNEL, {"type": "ward", "data": _patient_row(state)})

    # ------------------------------------------------------------ ingestion
    async def ingest_fhir_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Accept one FHIR Observation: validate, store in HAPI, then score."""
        patient_id = patient_from_observation(observation)
        metric = metric_from_observation(observation)
        effective = effective_from_observation(observation)
        if not patient_id or not metric or effective is None:
            raise ValueError("Observation needs subject, a supported LOINC code and effectiveDateTime")
        if patient_id not in self.patients:
            raise KeyError(f"unknown patient {patient_id!r}")

        stored = await self.hapi.store_observation(observation)
        state = self.patients[patient_id]
        state.stored_observations += 1

        value = value_from_observation(observation)
        bucket = self._pending[patient_id].setdefault(effective, {})
        bucket[metric] = value

        result = None
        pending = self._pending[patient_id]
        for earlier in sorted(t for t in pending if t < effective):
            earlier_bucket = pending.pop(earlier)
            self._score_timestamp(state, earlier, earlier_bucket)
        if all(m in bucket for m in MANDATORY_METRICS):
            result = self._score_timestamp(state, effective, bucket)
            pending.pop(effective, None)
        if state.last_processed is not None and effective <= state.last_processed:
            pending.pop(effective, None)
        return {
            "stored_id": stored.get("id"),
            "patient_id": patient_id,
            "metric": metric,
            "loinc": VITAL_CODINGS[metric].loinc,
            "scored": result is not None,
            "result": result.to_dict() if result else None,
        }

    async def ingest_fhir_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Accept a FHIR Bundle of Observations for one or more timestamps."""
        entries = bundle.get("entry", [])
        observations = [e["resource"] for e in entries if "resource" in e]
        if not observations:
            raise ValueError("Bundle contains no resources")

        grouped: dict[str, dict[datetime, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for observation in observations:
            patient_id = patient_from_observation(observation)
            effective = effective_from_observation(observation)
            metric = metric_from_observation(observation)
            if not patient_id or effective is None or not metric:
                raise ValueError("every Observation needs subject, LOINC code and effectiveDateTime")
            if patient_id not in self.patients:
                raise KeyError(f"unknown patient {patient_id!r}")
            grouped[patient_id][effective].append(observation)

        results: list[dict[str, Any]] = []
        stored_total = 0
        for patient_id, timestamps in grouped.items():
            state = self.patients[patient_id]
            flat = [o for group in timestamps.values() for o in group]
            await self.hapi.store_bundle(flat)
            stored_total += len(flat)
            state.stored_observations += len(flat)
            for effective in sorted(timestamps):
                bucket = self._pending[patient_id].pop(effective, {})
                source = "fhir"
                activity_intensity = 0.0
                for observation in timestamps[effective]:
                    metric = metric_from_observation(observation)
                    if metric:
                        bucket[metric] = value_from_observation(observation)
                    for extension in observation.get("extension", []):
                        if extension.get("url") == SOURCE_EXTENSION:
                            source = extension["valueString"]
                        elif extension.get("url") == ACTIVITY_EXTENSION:
                            activity_intensity = float(extension["valueDecimal"])
                obs = None
                if bucket.get("heart_rate") is not None:
                    obs = Observation(
                        timestamp=effective,
                        heart_rate=bucket["heart_rate"],
                        spo2=bucket.get("spo2"),
                        respiratory_rate=bucket.get("respiratory_rate"),
                        temperature=bucket.get("temperature"),
                        systolic_bp=bucket.get("systolic_bp"),
                        diastolic_bp=bucket.get("diastolic_bp"),
                        source=source,
                        activity_intensity=activity_intensity,
                    )
                result = self._score_timestamp(state, effective, bucket, obs=obs)
                if result:
                    results.append(result.to_dict())
        return {"stored": stored_total, "scored": len(results), "results": results}

    async def ingest_reading(self, patient_id: str, obs: Observation) -> MonitorResult | None:
        """Internal path used by the simulator and BIDMC replay.

        Stores every present vital as its own LOINC-coded Observation, then
        scores the assembled reading once.
        """
        state = self.patient(patient_id)
        observations = [entry["resource"] for entry in build_reading_bundle(patient_id, obs)["entry"]]
        if observations:
            await self.hapi.store_bundle(observations)
            state.stored_observations += len(observations)
        return self._score_timestamp(
            state, obs.timestamp, {m: getattr(obs, m, None) for m in VITAL_CODINGS}, obs=obs
        )

    # -------------------------------------------------------------- scoring
    def _score_timestamp(
        self,
        state: PatientState,
        effective: datetime,
        bucket: dict[str, float | None],
        obs: Observation | None = None,
    ) -> MonitorResult | None:
        if state.last_processed is not None and effective <= state.last_processed:
            return None
        if obs is None:
            hr = bucket.get("heart_rate")
            if hr is None:
                # Heart rate is the one required vital; without it there is
                # nothing to score, but the reading is still stored in HAPI.
                return None
            obs = Observation(
                timestamp=effective,
                heart_rate=float(hr),
                spo2=bucket.get("spo2"),
                temperature=bucket.get("temperature"),
                respiratory_rate=bucket.get("respiratory_rate"),
                systolic_bp=bucket.get("systolic_bp"),
                diastolic_bp=bucket.get("diastolic_bp"),
                source="fhir",
            )
        result = state.monitor.process(obs)
        state.last_processed = effective
        state.latest = result
        state.readings.append(obs)
        state.tier_counts[result.tier.value] += 1
        if result.suppressed:
            state.suppressed_count += 1
        for kind in result.artifact.kinds:
            state.artifact_counts[kind.value] += 1
        if result.raises_alert:
            state.alerts.append(result)
        self._publish(state, result)
        return result

    def _publish(self, state: PatientState, result: MonitorResult) -> None:
        patient_id = state.profile.patient_id
        self.hub.publish(patient_id, {"type": "clinical", "data": self.patient_state_payload(patient_id)})
        self.hub.publish(WARD_CHANNEL, {"type": "ward", "data": _patient_row(state)})

    # ---------------------------------------------------------------- views
    def patient_state_payload(self, patient_id: str) -> dict[str, Any]:
        state = self.patient(patient_id)
        latest = state.latest
        return {
            "patient_id": patient_id,
            "profile": state.profile.to_dict(),
            "timestamp": latest.timestamp.isoformat() if latest else None,
            "tier": latest.tier.value if latest else "NORMAL",
            "alert_kind": latest.alert_kind.value if latest else None,
            "news2": latest.news2.to_dict() if latest and latest.news2 else None,
            "drift": [d.to_dict() for d in latest.drift] if latest else [],
            "baselines": _baselines(state, latest),
            "artifact": latest.artifact.to_dict() if latest else None,
            "explanation": latest.explanation.to_dict() if latest and latest.explanation else None,
            "suppressed": latest.suppressed if latest else False,
            "baseline_ready": latest.baseline_ready if latest else False,
            "observation": _observation_dict(state.readings[-1]) if state.readings else None,
            "stored_observations": state.stored_observations,
            "suppressed_count": state.suppressed_count,
            "tier_counts": dict(state.tier_counts),
            "artifact_counts": dict(state.artifact_counts),
            "disclaimer": "Clinical decision support, not medical diagnosis.",
        }

    def list_patients(self) -> list[dict[str, Any]]:
        return [_patient_row(state) for state in self.patients.values()]

    def alerts(self, patient_id: str, limit: int = 50) -> list[dict[str, Any]]:
        state = self.patient(patient_id)
        items = list(state.alerts)[-limit:]
        return [a.to_dict() for a in reversed(items)]

    def readings(self, patient_id: str, limit: int = 300) -> list[dict[str, Any]]:
        state = self.patient(patient_id)
        return [_observation_dict(o) for o in list(state.readings)[-limit:]]

    async def close(self) -> None:
        await self.hapi.close()


def _monitor_for(profile: DischargeProfile) -> PersonalizedMonitor:
    # BIDMC is a 1 Hz recording; the virtual wearable reports every 5 minutes.
    interval = 1.0 if profile.scenario == "bidmc_replay" else 300.0
    return PersonalizedMonitor(
        profile, artifacts=ArtifactDetector(ArtifactConfig(expected_interval_s=interval))
    )


def _baselines(state: PatientState, latest: MonitorResult | None) -> dict[str, dict[str, float]]:
    """The patient's own current reference band, for charting and explanation."""
    if latest is None:
        return {}
    reference = state.monitor.baseline.reference(latest.timestamp)
    return {
        metric: {"center": round(center, 2), "spread": round(spread, 3)}
        for metric, (center, spread) in reference.items()
    }


def _patient_row(state: PatientState) -> dict[str, Any]:
    latest = state.latest
    profile = state.profile
    return {
        "patient_id": profile.patient_id,
        "display_name": profile.display_name,
        "age_years": profile.age_years,
        "sex": profile.sex,
        "scenario": profile.scenario,
        "conditions": [c.display for c in profile.conditions],
        "medications": [m.display for m in profile.medications],
        "spo2_scale": profile.spo2_scale,
        "beta_blocked": profile.beta_blocked,
        "timestamp": latest.timestamp.isoformat() if latest else None,
        "tier": latest.tier.value if latest else "NORMAL",
        "news2_total": latest.news2.total if latest and latest.news2 else None,
        "alert_kind": latest.alert_kind.value if latest else None,
        "suppressed": latest.suppressed if latest else False,
        "drift_metrics": [d.metric for d in latest.drift] if latest else [],
        "baseline_ready": latest.baseline_ready if latest else False,
        "alert_count": len(state.alerts),
        "suppressed_count": state.suppressed_count,
        "stored_observations": state.stored_observations,
    }


def _observation_dict(obs: Observation) -> dict[str, Any]:
    return {
        "timestamp": obs.timestamp.isoformat(),
        "heart_rate": obs.heart_rate,
        "spo2": obs.spo2,
        "respiratory_rate": obs.respiratory_rate,
        "temperature": obs.temperature,
        "systolic_bp": obs.systolic_bp,
        "diastolic_bp": obs.diastolic_bp,
        "activity_intensity": obs.activity_intensity,
        "source": obs.source,
    }