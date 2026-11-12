"""The clinical monitoring engine: readings in, explained alerts out.

Pipeline for one reading (PS-03 §4 steps 5-8):

    Observation
      -> artifact detection      (unreliable? raise a SENSOR alert, suppress clinical)
      -> profile personalization (SpO2 scale, beta-blocker pulse correction)
      -> NEWS2 scoring           (deterministic, auditable)
      -> rolling-baseline drift  (catches gradual change NEWS2 alone misses)
      -> tier + explanation      (reading + rule + profile fact)

The tier is decided entirely by code. No language model is in this path, so
detection lead time and alerts-per-patient-day are reproducible.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from clinical.artifacts import ArtifactDetector, ArtifactVerdict
from clinical.news2 import (
    PARAMETER_LABELS,
    PARAMETER_UNITS,
    Consciousness,
    News2Input,
    News2Result,
    Tier,
    score_news2,
)
from clinical.profiles import DischargeProfile, PersonalizationRule
from digital_twin.baseline import robust_center_spread
from digital_twin.types import Observation

TIER_ORDER: tuple[Tier, ...] = (Tier.NORMAL, Tier.WATCH, Tier.REVIEW, Tier.URGENT)

# Direction that counts as deterioration for each vital.
DETERIORATION_DIRECTION: dict[str, int] = {
    "heart_rate": +1,
    "respiratory_rate": +1,
    "temperature": +1,
    "spo2": -1,
    "systolic_bp": +1,
}

SPREAD_FLOOR: dict[str, float] = {
    "heart_rate": 3.5,
    "respiratory_rate": 1.2,
    "spo2": 0.6,
    "temperature": 0.12,
    "systolic_bp": 6.0,
}


class AlertKind(str, Enum):
    CLINICAL = "CLINICAL"
    SENSOR = "SENSOR"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"


@dataclass
class DriftFinding:
    """A vital that has moved away from this patient's own normal."""

    metric: str
    label: str
    value: float
    baseline_center: float
    baseline_spread: float
    robust_z: float
    direction: int
    sustained: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "label": self.label,
            "value": round(self.value, 2),
            "baseline_center": round(self.baseline_center, 2),
            "baseline_spread": round(self.baseline_spread, 3),
            "robust_z": round(self.robust_z, 2),
            "direction": self.direction,
            "sustained": self.sustained,
        }


@dataclass
class AlertExplanation:
    """The three things PS-03 §3.6 requires every alert to carry."""

    readings: list[dict[str, Any]]
    rules: list[dict[str, str]]
    profile_facts: list[str]
    narrative: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "readings": self.readings,
            "rules": self.rules,
            "profile_facts": self.profile_facts,
            "narrative": self.narrative,
        }


@dataclass
class MonitorResult:
    patient_id: str
    timestamp: datetime
    tier: Tier
    alert_kind: AlertKind
    artifact: ArtifactVerdict
    news2: News2Result | None
    drift: list[DriftFinding] = field(default_factory=list)
    explanation: AlertExplanation | None = None
    suppressed: bool = False
    baseline_ready: bool = False

    @property
    def raises_alert(self) -> bool:
        return self.tier is not Tier.NORMAL or self.alert_kind is not AlertKind.CLINICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "timestamp": self.timestamp.isoformat(),
            "tier": self.tier.value,
            "alert_kind": self.alert_kind.value,
            "suppressed": self.suppressed,
            "baseline_ready": self.baseline_ready,
            "artifact": self.artifact.to_dict(),
            "news2": self.news2.to_dict() if self.news2 else None,
            "drift": [d.to_dict() for d in self.drift],
            "explanation": self.explanation.to_dict() if self.explanation else None,
        }


class RollingBaseline:
    """Trailing per-patient baseline that excludes the most recent readings.

    The reference window deliberately stops ``exclude_recent`` before now, so
    an ongoing deterioration cannot drag its own baseline along and hide
    itself. Centers are medians and spreads are scaled MAD, which tolerate
    the occasional artifact.
    """

    def __init__(
        self,
        window: timedelta = timedelta(days=7),
        exclude_recent: timedelta = timedelta(hours=2),
        min_samples: int = 12,
    ) -> None:
        self.window = window
        self.exclude_recent = exclude_recent
        self.min_samples = min_samples
        self._history: deque[Observation] = deque(maxlen=20000)

    def update(self, obs: Observation) -> None:
        self._history.append(obs)

    def reference(self, now: datetime) -> dict[str, tuple[float, float]]:
        cutoff_old = now - self.window
        cutoff_new = now - self.exclude_recent
        samples = [o for o in self._history if cutoff_old <= o.timestamp <= cutoff_new]
        if len(samples) < self.min_samples:
            return {}
        out: dict[str, tuple[float, float]] = {}
        for metric, floor in SPREAD_FLOOR.items():
            values = [v for v in (getattr(o, metric, None) for o in samples) if v is not None]
            if len(values) >= self.min_samples:
                out[metric] = robust_center_spread(values, floor)
        return out


@dataclass
class MonitorConfig:
    drift_z_threshold: float = 3.0
    drift_sustained_readings: int = 3
    device_offline_gap_s: float = 450.0
    # Escalate immediately, de-escalate only after this many consecutive
    # readings at the lower tier. Without it, noise around a threshold makes
    # the tier flap and every flap reads as a fresh alert — alert fatigue.
    deescalate_readings: int = 6


class PersonalizedMonitor:
    """One instance per patient. Feed readings in timestamp order."""

    def __init__(
        self,
        profile: DischargeProfile,
        config: MonitorConfig | None = None,
        baseline: RollingBaseline | None = None,
        artifacts: ArtifactDetector | None = None,
    ) -> None:
        self.profile = profile
        self.config = config or MonitorConfig()
        self.artifacts = artifacts or ArtifactDetector()
        self.baseline = baseline or RollingBaseline()
        self._drift_streak: dict[str, int] = {}
        self._last_timestamp: datetime | None = None
        self._confirmed_tier: Tier = Tier.NORMAL
        self._lower_streak = 0

    def process(self, obs: Observation) -> MonitorResult:
        artifact = self.artifacts.assess(obs)
        self.baseline.update(obs)

        # A connectivity gap is a device alert, never a clinical one.
        offline = (
            artifact.gap_seconds is not None
            and artifact.gap_seconds >= self.config.device_offline_gap_s
        )

        if artifact.suppress_clinical_alert:
            self._drift_streak.clear()
            return MonitorResult(
                patient_id=self.profile.patient_id,
                timestamp=obs.timestamp,
                tier=Tier.NORMAL,
                alert_kind=AlertKind.DEVICE_OFFLINE if offline else AlertKind.SENSOR,
                artifact=artifact,
                news2=None,
                suppressed=True,
                explanation=self._sensor_explanation(artifact, offline),
                baseline_ready=bool(self.baseline.reference(obs.timestamp)),
            )

        news2 = self._score(obs, artifact)
        drift = self._detect_drift(obs, artifact)
        tier = self._confirm_tier(self._combine(news2.tier, drift))

        result = MonitorResult(
            patient_id=self.profile.patient_id,
            timestamp=obs.timestamp,
            tier=tier,
            alert_kind=AlertKind.CLINICAL,
            artifact=artifact,
            news2=news2,
            drift=drift,
            baseline_ready=bool(self.baseline.reference(obs.timestamp)),
        )
        if tier is not Tier.NORMAL:
            result.explanation = self._clinical_explanation(obs, news2, drift, tier)
        self._last_timestamp = obs.timestamp
        return result

    # ------------------------------------------------------------- scoring
    def _score(self, obs: Observation, artifact: ArtifactVerdict) -> News2Result:
        """Apply profile personalization, then score.

        Untrusted metrics are withheld from scoring rather than scored as if
        they were real.
        """
        pulse = obs.heart_rate
        if pulse is not None and self.profile.beta_blocked:
            pulse = pulse + self.profile.pulse_correction_bpm

        def trusted(metric: str, value: float | None) -> float | None:
            return value if artifact.trusted(metric) else None

        vitals = News2Input(
            respiratory_rate=trusted("respiratory_rate", obs.respiratory_rate),
            spo2=trusted("spo2", obs.spo2),
            temperature=trusted("temperature", obs.temperature),
            heart_rate=trusted("heart_rate", pulse),
            systolic_bp=trusted("systolic_bp", getattr(obs, "systolic_bp", None)),
            consciousness=Consciousness.ALERT,
            supplemental_oxygen=False,
            spo2_scale=self.profile.spo2_scale,
        )
        return score_news2(vitals)

    def _detect_drift(self, obs: Observation, artifact: ArtifactVerdict) -> list[DriftFinding]:
        reference = self.baseline.reference(obs.timestamp)
        findings: list[DriftFinding] = []
        for metric, direction in DETERIORATION_DIRECTION.items():
            value = getattr(obs, metric, None)
            if value is None or metric not in reference or not artifact.trusted(metric):
                self._drift_streak[metric] = 0
                continue
            center, spread = reference[metric]
            signed = (value - center) * direction
            z = signed / spread if spread > 0 else 0.0
            if z >= self.config.drift_z_threshold:
                self._drift_streak[metric] = self._drift_streak.get(metric, 0) + 1
            else:
                self._drift_streak[metric] = 0
            sustained = self._drift_streak[metric]
            if sustained >= self.config.drift_sustained_readings:
                findings.append(
                    DriftFinding(
                        metric=metric,
                        label=PARAMETER_LABELS.get(metric, metric),
                        value=float(value),
                        baseline_center=center,
                        baseline_spread=spread,
                        robust_z=z,
                        direction=direction,
                        sustained=sustained,
                    )
                )
        return findings

    def _combine(self, news2_tier: Tier, drift: list[DriftFinding]) -> Tier:
        """Gradual drift escalates one tier above the NEWS2 aggregate.

        This is what catches a slow deterioration while every individual
        reading is still inside population reference ranges.
        """
        if not drift:
            return news2_tier
        index = TIER_ORDER.index(news2_tier)
        return TIER_ORDER[min(index + 1, len(TIER_ORDER) - 1)]

    def _confirm_tier(self, tier: Tier) -> Tier:
        """Hold a raised tier until the improvement is sustained.

        Suppressed readings never reach here, so a sensor fault cannot quietly
        downgrade a patient who was already flagged.
        """
        if TIER_ORDER.index(tier) >= TIER_ORDER.index(self._confirmed_tier):
            self._confirmed_tier = tier
            self._lower_streak = 0
            return tier
        self._lower_streak += 1
        if self._lower_streak >= self.config.deescalate_readings:
            self._confirmed_tier = tier
            self._lower_streak = 0
        return self._confirmed_tier

    # -------------------------------------------------------- explanations
    def _clinical_explanation(
        self,
        obs: Observation,
        news2: News2Result,
        drift: list[DriftFinding],
        tier: Tier,
    ) -> AlertExplanation:
        readings: list[dict[str, Any]] = []
        for param in news2.parameters:
            if param.score <= 0:
                continue
            readings.append(
                {
                    "metric": param.parameter,
                    "label": param.label,
                    "value": param.value,
                    "unit": param.unit,
                    "news2_points": param.score,
                    "band": param.band,
                    "scale": param.scale,
                }
            )
        for finding in drift:
            readings.append(
                {
                    "metric": finding.metric,
                    "label": finding.label,
                    "value": round(finding.value, 2),
                    "unit": PARAMETER_UNITS.get(finding.metric, ""),
                    "news2_points": 0,
                    "band": f"usual {finding.baseline_center:.1f} (z={finding.robust_z:.1f})",
                    "drift": True,
                }
            )

        rules = [r.to_dict() for r in self.profile.rules()]
        if drift:
            rules.append(
                {
                    "rule_id": "rolling_baseline_drift",
                    "title": "Gradual drift from personal baseline",
                    "profile_fact": f"Baseline learned from {self.profile.patient_id}'s own history",
                    "effect": "Escalated one tier above the NEWS2 aggregate because the change is sustained.",
                }
            )

        facts = [r.profile_fact for r in self.profile.rules() if r.rule_id != "rolling_baseline"]
        narrative = self._narrative(obs, news2, drift, tier)
        return AlertExplanation(
            readings=readings, rules=rules, profile_facts=facts, narrative=narrative
        )

    def _narrative(
        self,
        obs: Observation,
        news2: News2Result,
        drift: list[DriftFinding],
        tier: Tier,
    ) -> str:
        parts: list[str] = []
        scoring = [p for p in news2.scoring_parameters if p.parameter in PARAMETER_UNITS]
        if scoring:
            detail = ", ".join(
                f"{p.label} {p.value}{p.unit} scores {p.score} ({p.band})" for p in scoring
            )
            parts.append(f"NEWS2 aggregate {news2.total}: {detail}.")
        if self.profile.hypercapnic_respiratory_failure:
            parts.append(
                "SpO₂ scored on Scale 2 (target 88–92%) because of documented "
                "hypercapnic respiratory failure."
            )
        if self.profile.beta_blocked:
            parts.append(
                f"Pulse corrected by +{self.profile.pulse_correction_bpm:g} bpm for "
                "beta-blocker therapy, which blunts the heart-rate response."
            )
        if drift:
            detail = "; ".join(
                f"{d.label} {d.value:.1f} versus usual {d.baseline_center:.1f} "
                f"(z={d.robust_z:.1f}, {d.sustained} consecutive readings)"
                for d in drift
            )
            parts.append(f"Gradual drift from this patient's own baseline: {detail}.")
        parts.append(f"Tier {tier.value}. Clinical decision support, not a medical diagnosis.")
        return " ".join(parts)

    def _sensor_explanation(
        self, artifact: ArtifactVerdict, offline: bool
    ) -> AlertExplanation:
        kind = "Device connectivity" if offline else "Sensor artifact"
        narrative = (
            f"{kind} detected — clinical alert suppressed. "
            + " ".join(artifact.reasons)
            + " This is a device issue, not evidence of patient deterioration."
        )
        return AlertExplanation(
            readings=[],
            rules=[
                {
                    "rule_id": "artifact_suppression",
                    "title": "Sensor artifact suppression",
                    "profile_fact": "Signal reliability check across recent readings",
                    "effect": "Unreliable metrics withheld from NEWS2; no clinical tier raised.",
                }
            ],
            profile_facts=[],
            narrative=narrative,
        )