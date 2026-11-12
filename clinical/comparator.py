"""Naive fixed-threshold comparator and performance report (PS-03 §3.7).

The evaluation criteria ask for detection lead time and alerts per patient-day
*compared with naive fixed-threshold monitoring*. This module supplies that
comparator and runs both systems over identical readings, so the comparison is
fair: same data, same seeds, same alert-counting rule.

The naive monitor is deliberately what a first attempt looks like:

* fixed population thresholds, identical for every patient
* no SpO2 Scale 2, so a hypercapnic patient's own normal reads as hypoxia
* no beta-blocker correction, so a blunted pulse reads as reassurance
* no rolling baseline, so gradual drift inside the reference ranges is invisible
* no artifact detection, so a removed probe reads as a patient in extremis

Timing, defined precisely because "lead time" is ambiguous:

* ``detection_delay``  first alert minus ground-truth onset. Lower is better.
* ``lead_time``        severity milestone minus first alert — how long *before
  the patient became more severe* the system flagged them. Higher is better.
  The milestone is the first personalized NEWS2 aggregate >= 5 (medium risk).

Alerts are counted as *episodes* — a transition from not-alerting to alerting —
for both systems, because that is the unit a clinician actually experiences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from clinical.artifacts import ArtifactConfig, ArtifactDetector
from clinical.monitor import AlertKind, PersonalizedMonitor
from clinical.profiles import DischargeProfile
from digital_twin.types import Observation

# Fixed thresholds a naive monitor would use, applied to every patient alike.
NAIVE_THRESHOLDS: dict[str, tuple[str, float]] = {
    "heart_rate": (">", 100.0),
    "respiratory_rate": (">", 20.0),
    "spo2": ("<", 92.0),
    "temperature": (">", 38.0),
    "systolic_bp": ("<", 90.0),
}

# NEWS2 aggregate at which the patient has become "more severe" (medium risk).
SEVERITY_NEWS2 = 5

TIER_RANK = {"NORMAL": 0, "WATCH": 1, "REVIEW": 2, "URGENT": 3}


class NaiveThresholdMonitor:
    """Fixed thresholds, no personalization, no artifact handling."""

    def __init__(self) -> None:
        self.alerting = False

    def process(self, obs: Observation) -> bool:
        breached = False
        for metric, (op, limit) in NAIVE_THRESHOLDS.items():
            value = getattr(obs, metric, None)
            if value is None:
                continue
            if (op == ">" and value > limit) or (op == "<" and value < limit):
                breached = True
                break
        episode = breached and not self.alerting
        self.alerting = breached
        return episode


@dataclass
class SystemRun:
    first_alert_hours: float | None = None
    detection_delay_hours: float | None = None
    lead_time_hours: float | None = None
    alerts: int = 0
    false_alerts: int | None = 0
    alerts_per_patient_day: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_alert_hours": self.first_alert_hours,
            "detection_delay_hours": self.detection_delay_hours,
            "lead_time_hours": self.lead_time_hours,
            "alerts": self.alerts,
            "false_alerts": self.false_alerts,
            "alerts_per_patient_day": self.alerts_per_patient_day,
        }


@dataclass
class ScenarioEvaluation:
    patient_id: str
    display_name: str
    scenario: str
    source: str
    readings: int
    span_hours: float
    patient_days: float
    onset_hours: float | None
    severity_at_hours: float | None
    proposed: SystemRun
    naive: SystemRun
    # A naive monitor that was already alerting before onset never produced a
    # meaningful detection: it was crying wolf, not detecting deterioration.
    naive_alerting_before_onset: bool | None = False
    proposed_suppressed: int = 0
    proposed_max_tier: str = "NORMAL"
    artifact_counts: dict[str, int] = field(default_factory=dict)
    tier_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_recorded(self) -> bool:
        return self.source == "bidmc"

    @property
    def lead_time_gain_hours(self) -> float | None:
        if self.proposed.lead_time_hours is None or self.naive.lead_time_hours is None:
            return None
        return round(self.proposed.lead_time_hours - self.naive.lead_time_hours, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "display_name": self.display_name,
            "scenario": self.scenario,
            "source": self.source,
            "readings": self.readings,
            "span_hours": round(self.span_hours, 2),
            "patient_days": round(self.patient_days, 3),
            "onset_hours": self.onset_hours,
            "severity_at_hours": self.severity_at_hours,
            "proposed": {**self.proposed.to_dict(), "suppressed": self.proposed_suppressed, "max_tier": self.proposed_max_tier},
            "naive": {**self.naive.to_dict(), "alerting_before_onset": self.naive_alerting_before_onset},
            "lead_time_gain_hours": self.lead_time_gain_hours,
            "artifact_counts": dict(self.artifact_counts),
            "tier_counts": dict(self.tier_counts),
        }


def evaluate_readings(
    profile: DischargeProfile,
    readings: list[Observation],
    *,
    onset_s: float | None,
    source: str,
    interval_s: float = 300.0,
) -> ScenarioEvaluation:
    """Run both monitors over the same readings and score the difference."""
    monitor = PersonalizedMonitor(
        profile, artifacts=ArtifactDetector(ArtifactConfig(expected_interval_s=interval_s))
    )
    naive = NaiveThresholdMonitor()

    has_ground_truth = source != "bidmc" or onset_s is not None
    proposed = SystemRun(false_alerts=0 if has_ground_truth else None)
    naive_run = SystemRun(false_alerts=0 if has_ground_truth else None)
    proposed_alerting = False
    naive_alerting_before_onset = False if has_ground_truth else None
    suppressed = 0
    max_tier = "NORMAL"
    severity_s: float | None = None
    tier_counts: dict[str, int] = {}
    artifact_counts: dict[str, int] = {}

    start = readings[0].timestamp
    span_s = 0.0

    for obs in readings:
        elapsed = (obs.timestamp - start).total_seconds()
        span_s = max(span_s, elapsed)
        # Before ground-truth onset the patient is stable, so any clinical
        # alert there is false. In a sensor-artifact scenario the patient never
        # deteriorates, so every clinical alert is false.
        stable = has_ground_truth and (onset_s is None or elapsed < onset_s)

        result = monitor.process(obs)
        tier_counts[result.tier.value] = tier_counts.get(result.tier.value, 0) + 1
        if TIER_RANK[result.tier.value] > TIER_RANK[max_tier]:
            max_tier = result.tier.value
        for kind in result.artifact.kinds:
            artifact_counts[kind.value] = artifact_counts.get(kind.value, 0) + 1
        if result.suppressed:
            suppressed += 1
        if severity_s is None and result.news2 is not None and result.news2.total >= SEVERITY_NEWS2:
            severity_s = elapsed

        clinical = result.alert_kind is AlertKind.CLINICAL and result.tier.value != "NORMAL"
        if clinical and not proposed_alerting:
            proposed.alerts += 1
            if proposed.first_alert_hours is None and not stable:
                proposed.first_alert_hours = elapsed / 3600.0
            if stable and proposed.false_alerts is not None:
                proposed.false_alerts += 1
        proposed_alerting = clinical

        if naive.process(obs):
            if stable and naive_run.false_alerts is not None:
                naive_alerting_before_onset = True
                naive_run.false_alerts += 1
            naive_run.alerts += 1
            if naive_run.first_alert_hours is None and not stable:
                naive_run.first_alert_hours = elapsed / 3600.0

    days = max(span_s / 86400.0, 1e-9)
    # An 8-minute ICU recording cannot be extrapolated to a per-day alert rate.
    per_day = (lambda n: round(n / days, 2)) if source != "bidmc" else (lambda n: None)
    proposed.alerts_per_patient_day = per_day(proposed.alerts)
    naive_run.alerts_per_patient_day = per_day(naive_run.alerts)

    onset_hours = round(onset_s / 3600.0, 2) if onset_s is not None else None
    severity_hours = round(severity_s / 3600.0, 2) if severity_s is not None else None

    def timing(run: SystemRun) -> None:
        if run.first_alert_hours is None or onset_hours is None:
            return
        run.detection_delay_hours = round(run.first_alert_hours - onset_hours, 2)
        if severity_hours is not None:
            run.lead_time_hours = round(severity_hours - run.first_alert_hours, 2)

    timing(proposed)
    timing(naive_run)
    for run in (proposed, naive_run):
        if run.first_alert_hours is not None:
            run.first_alert_hours = round(run.first_alert_hours, 2)

    return ScenarioEvaluation(
        patient_id=profile.patient_id,
        display_name=profile.display_name,
        scenario=profile.scenario,
        source=source,
        readings=len(readings),
        span_hours=span_s / 3600.0,
        patient_days=days,
        onset_hours=onset_hours,
        severity_at_hours=severity_hours,
        proposed=proposed,
        naive=naive_run,
        naive_alerting_before_onset=naive_alerting_before_onset,
        proposed_suppressed=suppressed,
        proposed_max_tier=max_tier,
        artifact_counts=artifact_counts,
        tier_counts=tier_counts,
    )


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _totals(evaluations: list[ScenarioEvaluation]) -> dict[str, Any]:
    days = sum(e.patient_days for e in evaluations) or 1e-9
    proposed_alerts = sum(e.proposed.alerts for e in evaluations)
    naive_alerts = sum(e.naive.alerts for e in evaluations)
    has_ground_truth = all(e.proposed.false_alerts is not None for e in evaluations)
    per_day_available = all(e.proposed.alerts_per_patient_day is not None for e in evaluations)
    proposed_false = sum(e.proposed.false_alerts for e in evaluations) if has_ground_truth else None
    naive_false = sum(e.naive.false_alerts for e in evaluations) if has_ground_truth else None

    artifacts: dict[str, int] = {}
    for e in evaluations:
        for kind, count in e.artifact_counts.items():
            artifacts[kind] = artifacts.get(kind, 0) + count

    return {
        "patients": len(evaluations),
        "readings": sum(e.readings for e in evaluations),
        "patient_days": round(days, 2),
        "proposed_alerts": proposed_alerts,
        "naive_alerts": naive_alerts,
        "proposed_alerts_per_patient_day": round(proposed_alerts / days, 2) if per_day_available else None,
        "naive_alerts_per_patient_day": round(naive_alerts / days, 2) if per_day_available else None,
        "alert_reduction_percent": (
            round(100.0 * (naive_alerts - proposed_alerts) / naive_alerts, 1) if naive_alerts else None
        ),
        "proposed_false_alerts": proposed_false,
        "naive_false_alerts": naive_false,
        "false_alert_reduction_percent": (
            round(100.0 * (naive_false - proposed_false) / naive_false, 1) if naive_false else None
        ),
        "suppressed_readings": sum(e.proposed_suppressed for e in evaluations),
        "artifact_counts": artifacts,
        "mean_lead_time_hours": _mean(
            [e.proposed.lead_time_hours for e in evaluations if e.proposed.lead_time_hours is not None]
        ),
        "naive_mean_lead_time_hours": _mean(
            [e.naive.lead_time_hours for e in evaluations if e.naive.lead_time_hours is not None]
        ),
        "mean_detection_delay_hours": _mean(
            [e.proposed.detection_delay_hours for e in evaluations if e.proposed.detection_delay_hours is not None]
        ),
        "naive_mean_detection_delay_hours": _mean(
            [e.naive.detection_delay_hours for e in evaluations if e.naive.detection_delay_hours is not None]
        ),
    }


def build_report(evaluations: list[ScenarioEvaluation]) -> dict[str, Any]:
    """Aggregate per-patient evaluations into the §3.7 performance report.

    Scripted scenarios and the BIDMC recording are reported separately: the
    recording is eight minutes of stable ICU data, so extrapolating it to a
    per-day alert rate would be meaningless.
    """
    scenarios = [e for e in evaluations if not e.is_recorded]
    recorded = [e for e in evaluations if e.is_recorded]
    return {
        "scenarios": [e.to_dict() for e in scenarios],
        "recorded": [e.to_dict() for e in recorded],
        "totals": _totals(scenarios),
        "recorded_totals": _totals(recorded) if recorded else None,
        "method": {
            "alert_unit": "episode (transition from not-alerting to alerting), identical for both systems",
            "detection_delay": "first alert minus ground-truth deterioration onset; lower is better",
            "lead_time": (
                f"severity milestone minus first alert, where the milestone is the first "
                f"personalized NEWS2 aggregate >= {SEVERITY_NEWS2} (medium risk); higher is better"
            ),
            "naive_thresholds": {
                metric: f"{op} {limit:g}" for metric, (op, limit) in NAIVE_THRESHOLDS.items()
            },
            "naive_omits": [
                "SpO₂ Scale 2 personalization",
                "beta-blocker pulse correction",
                "per-patient rolling baseline",
                "sensor artifact detection and suppression",
                "tier de-escalation hysteresis",
            ],
            "false_alert_definition": (
                "a clinical alert raised before ground-truth deterioration onset, "
                "or any clinical alert in a scenario where the patient never deteriorates"
            ),
            "invalid_naive_detection": (
                "a naive monitor already alerting before onset is counted as a false alert, "
                "not as early detection"
            ),
            "recorded_data": (
                "BIDMC is reported separately; its 8-minute span is not extrapolated per day. "
                "Without deterioration ground truth, false-alert labels and detection timing are unavailable."
            ),
            "data": "identical seeded readings replayed through both monitors",
        },
        "disclaimer": "Clinical decision support, not medical diagnosis.",
    }