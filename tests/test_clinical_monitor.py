"""Personalized monitoring: the rules must demonstrably change scores and alerts."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from clinical.monitor import AlertKind, MonitorConfig, PersonalizedMonitor
from clinical.news2 import Tier
from clinical.profiles import Condition, DischargeProfile, Medication
from tests.conftest import make_obs

T0 = datetime(2026, 11, 5, 8, 0, 0)
STEP_S = 300.0


def profile(patient_id: str = "p1", **overrides) -> DischargeProfile:
    base = dict(
        patient_id=patient_id,
        display_name="Synthetic Patient",
        age_years=66,
        sex="male",
        discharged_on="2026-11-05",
    )
    base.update(overrides)
    return DischargeProfile(**base)


def copd_profile() -> DischargeProfile:
    return profile(
        "p-copd",
        conditions=(Condition("87433001", "Pulmonary emphysema (disorder)"),),
        hypercapnic_respiratory_failure=True,
        spo2_target=(88.0, 92.0),
    )


def beta_blocked_profile() -> DischargeProfile:
    return profile(
        "p-bb",
        conditions=(Condition("84114007", "Chronic congestive heart failure (disorder)"),),
        medications=(
            Medication("866412", "24 HR metoprolol succinate 100 MG Extended Release Oral Tablet", "metoprolol"),
        ),
        pulse_correction_bpm=10.0,
    )


def stable_series(n: int, start: datetime = T0, seed: int = 7) -> list:
    rng = random.Random(seed)
    return [
        make_obs(
            start + timedelta(seconds=STEP_S * i),
            heart_rate=round(64 + rng.gauss(0, 1.5), 1),
            respiratory_rate=round(14 + rng.gauss(0, 0.6), 1),
            spo2=round(97.5 + rng.gauss(0, 0.3), 1),
            temperature=round(36.8 + rng.gauss(0, 0.08), 2),
            systolic_bp=round(122 + rng.gauss(0, 3), 1),
        )
        for i in range(n)
    ]


def drift_series(n: int, start: datetime, targets: dict[str, float]) -> list:
    """Ramp from the stable means to ``targets``, staying inside population ranges."""
    origins = {"heart_rate": 64.0, "respiratory_rate": 14.0, "spo2": 97.5, "temperature": 36.8}
    out = []
    for i in range(n):
        fraction = (i + 1) / n
        values = {
            metric: round(origins[metric] + (target - origins[metric]) * fraction, 2)
            for metric, target in targets.items()
        }
        out.append(make_obs(start + timedelta(seconds=STEP_S * i), **values))
    return out


# ------------------------------------------------------- SpO2 Scale 2 rule
def test_scale2_scores_target_spo2_as_normal():
    monitor = PersonalizedMonitor(copd_profile())
    result = monitor.process(make_obs(T0, spo2=91.0, heart_rate=64.0, respiratory_rate=15.0))
    assert result.news2.spo2_scale == 2
    spo2 = next(p for p in result.news2.parameters if p.parameter == "spo2")
    assert spo2.score == 0
    assert result.tier is Tier.NORMAL


def test_identical_vitals_alert_on_scale1_but_not_scale2():
    reading = dict(spo2=91.0, heart_rate=64.0, respiratory_rate=15.0, temperature=36.7)
    scale2 = PersonalizedMonitor(copd_profile()).process(make_obs(T0, **reading))
    scale1 = PersonalizedMonitor(profile("p-plain")).process(make_obs(T0, **reading))
    assert scale2.news2.total == 0
    assert scale1.news2.total == 3
    assert scale2.tier is Tier.NORMAL
    assert scale1.tier is Tier.WATCH


def test_scale2_still_detects_real_desaturation():
    monitor = PersonalizedMonitor(copd_profile())
    result = monitor.process(make_obs(T0, spo2=85.0))
    spo2 = next(p for p in result.news2.parameters if p.parameter == "spo2")
    assert spo2.score == 2
    assert result.tier is not Tier.NORMAL


# ------------------------------------------------- beta-blocker pulse rule
def test_beta_blocker_correction_changes_the_pulse_score():
    reading = dict(heart_rate=85.0, spo2=97.0, respiratory_rate=16.0, temperature=36.8)
    corrected = PersonalizedMonitor(beta_blocked_profile()).process(make_obs(T0, **reading))
    plain = PersonalizedMonitor(profile("p-plain")).process(make_obs(T0, **reading))

    corrected_pulse = next(p for p in corrected.news2.parameters if p.parameter == "heart_rate")
    plain_pulse = next(p for p in plain.news2.parameters if p.parameter == "heart_rate")
    assert plain_pulse.score == 0  # 85 bpm looks reassuring
    assert corrected_pulse.score == 1  # 95 bpm after the +10 correction
    assert corrected.news2.total == plain.news2.total + 1


def test_beta_blocker_rule_names_the_medications():
    rules = beta_blocked_profile().rules()
    rule = next(r for r in rules if r.rule_id == "beta_blocker_pulse")
    assert "metoprolol" in rule.profile_fact
    assert "866412" in rule.profile_fact


# ---------------------------------------------------- rolling baseline rule
def test_gradual_drift_is_caught_while_news2_stays_zero():
    monitor = PersonalizedMonitor(profile("p-drift"))
    stable = stable_series(300)
    for obs in stable:
        monitor.process(obs)

    drifting = drift_series(
        30,
        stable[-1].timestamp + timedelta(seconds=STEP_S),
        {"heart_rate": 84.0, "respiratory_rate": 19.5, "temperature": 37.5},
    )
    results = [monitor.process(obs) for obs in drifting]
    final = results[-1]

    assert final.news2.total == 0, "every reading is still inside population ranges"
    assert {d.metric for d in final.drift} >= {"heart_rate", "respiratory_rate"}
    assert final.tier is Tier.WATCH, "drift escalates one tier above the NEWS2 aggregate"
    assert final.explanation is not None


def test_deterioration_cannot_drag_its_own_baseline():
    monitor = PersonalizedMonitor(profile("p-baseline"))
    stable = stable_series(300)
    for obs in stable:
        monitor.process(obs)
    drifting = drift_series(
        30, stable[-1].timestamp + timedelta(seconds=STEP_S), {"heart_rate": 84.0}
    )
    for obs in drifting:
        monitor.process(obs)

    reference = monitor.baseline.reference(drifting[-1].timestamp)
    center, _spread = reference["heart_rate"]
    assert center < 70.0, "the reference window must stop before the recent deterioration"


def test_baseline_needs_enough_history():
    monitor = PersonalizedMonitor(profile("p-new"))
    for obs in stable_series(5):
        result = monitor.process(obs)
    assert result.baseline_ready is False
    assert result.drift == []


def test_drift_must_be_sustained_not_a_single_reading():
    config = MonitorConfig(drift_sustained_readings=3)
    monitor = PersonalizedMonitor(profile("p-sustain"), config)
    stable = stable_series(300)
    for obs in stable:
        monitor.process(obs)

    spike = make_obs(stable[-1].timestamp + timedelta(seconds=STEP_S), heart_rate=95.0)
    assert monitor.process(spike).drift == []
    back = make_obs(spike.timestamp + timedelta(seconds=STEP_S), heart_rate=64.0)
    assert monitor.process(back).drift == []


# ------------------------------------------------------------- suppression
def test_probe_removal_is_a_sensor_alert_not_a_clinical_one():
    monitor = PersonalizedMonitor(profile("p-sensor"))
    for obs in stable_series(20):
        monitor.process(obs)
    removed = make_obs(
        stable_series(20)[-1].timestamp + timedelta(seconds=STEP_S), spo2=0.0, heart_rate=64.0
    )
    result = monitor.process(removed)
    assert result.suppressed is True
    assert result.alert_kind is AlertKind.SENSOR
    assert result.tier is Tier.NORMAL
    assert result.news2 is None


def test_explanation_carries_reading_rule_and_profile_fact():
    monitor = PersonalizedMonitor(copd_profile())
    result = monitor.process(make_obs(T0, spo2=85.0, respiratory_rate=26.0, temperature=38.6))
    explanation = result.explanation
    assert explanation is not None
    assert explanation.readings, "the alert must name the readings that triggered it"
    assert any(r["rule_id"] == "spo2_scale_2" for r in explanation.rules)
    assert explanation.profile_facts, "the alert must name the profile fact behind the rule"
    assert "Scale 2" in explanation.narrative
    assert "not a medical diagnosis" in explanation.narrative


def test_missing_vitals_are_reported_not_imputed():
    monitor = PersonalizedMonitor(profile("p-missing"))
    result = monitor.process(make_obs(T0, spo2=None, temperature=None))
    assert set(result.news2.missing) == {"spo2", "temperature"}
    assert result.news2.complete is False
    assert all(p.value is not None for p in result.news2.parameters)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_monitoring_is_deterministic(seed: int):
    def run() -> list:
        monitor = PersonalizedMonitor(profile("p-det"))
        readings = stable_series(60, seed=seed) + drift_series(
            10, T0 + timedelta(seconds=STEP_S * 60), {"heart_rate": 88.0}
        )
        return [(r.tier.value, r.news2.total if r.news2 else None) for r in map(monitor.process, readings)]

    assert run() == run()


def test_reset_broadcasts_cleared_patient_and_ward_state(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from backend.app.services.clinical import ClinicalService, WARD_CHANNEL

    monkeypatch.setattr("backend.app.services.clinical.HapiClient", Mock())
    hub = Mock()
    service = ClinicalService(hub, "http://127.0.0.1:8080/fhir", tmp_path)
    patient = profile()
    state = service.register_profile(patient)
    state.latest = state.monitor.process(make_obs(T0, heart_rate=120.0))
    state.readings.append(make_obs(T0))
    state.alerts.append(state.latest)

    service.reset()

    hub.publish.assert_any_call(patient.patient_id, {
        "type": "clinical", "data": service.patient_state_payload(patient.patient_id),
    })
    ward = next(call.args[1]["data"] for call in hub.publish.call_args_list if call.args[0] == WARD_CHANNEL)
    assert ward["timestamp"] is None
    assert ward["alert_count"] == 0
    assert not service.patient(patient.patient_id).readings
    assert not service.patient(patient.patient_id).alerts


# ------------------------------------------------------- comparator regressions
def test_evaluate_readings_replays_identical_observations_to_both_monitors(monkeypatch):
    from clinical.comparator import NaiveThresholdMonitor, evaluate_readings

    readings = [
        make_obs(T0 + timedelta(seconds=STEP_S * i), heart_rate=heart_rate)
        for i, heart_rate in enumerate((66.0, 95.0, 105.0, 120.0))
    ]
    original_readings = tuple(readings)
    proposed_seen = []
    naive_seen = []
    proposed_process = PersonalizedMonitor.process
    naive_process = NaiveThresholdMonitor.process

    def record_proposed(self, obs):
        proposed_seen.append(obs)
        return proposed_process(self, obs)

    def record_naive(self, obs):
        naive_seen.append(obs)
        return naive_process(self, obs)

    monkeypatch.setattr(PersonalizedMonitor, "process", record_proposed)
    monkeypatch.setattr(NaiveThresholdMonitor, "process", record_naive)

    evaluation = evaluate_readings(profile(), readings, onset_s=STEP_S, source="simulator")

    assert len(proposed_seen) == len(naive_seen) == evaluation.readings == len(readings)
    assert all(proposed is naive is original for proposed, naive, original in zip(
        proposed_seen, naive_seen, original_readings
    ))
    assert tuple(readings) == original_readings
    assert sum(evaluation.tier_counts.values()) == len(readings)
    assert evaluation.artifact_counts == {}
    assert evaluation.proposed_suppressed == 0


@pytest.mark.parametrize("heart_rate, respiratory_rate, expected_alerts", [
    (66.0, 14.0, 0),
    (105.0, 14.0, 1),
    (120.0, 26.0, 1),
], ids=["stable", "false-watch-alarm", "false-severity-alarm"])
def test_no_deterioration_has_no_valid_detection_timing(heart_rate, respiratory_rate, expected_alerts):
    from clinical.comparator import evaluate_readings

    readings = [make_obs(T0)] + [
        make_obs(
            T0 + timedelta(seconds=STEP_S * i),
            heart_rate=heart_rate,
            respiratory_rate=respiratory_rate,
        )
        for i in range(1, 4)
    ]
    evaluation = evaluate_readings(profile(), readings, onset_s=None, source="simulator")

    assert evaluation.onset_hours is None
    assert evaluation.artifact_counts == {}
    assert evaluation.proposed_suppressed == 0
    for run in (evaluation.proposed, evaluation.naive):
        assert run.alerts == run.false_alerts == expected_alerts
        assert run.detection_delay_hours is None
        assert run.lead_time_hours is None, "false alarms cannot have a valid deterioration lead time"
    assert evaluation.lead_time_gain_hours is None


def test_evaluate_readings_counts_clinical_episodes_not_elevated_readings():
    from clinical.comparator import evaluate_readings

    # Six normal readings end the proposed episode despite its hysteresis.
    heart_rates = [66.0, 105.0, 106.0, 107.0, 66.0, 67.0, 66.0, 67.0, 66.0, 67.0, 105.0, 106.0, 107.0]
    readings = [
        make_obs(T0 + timedelta(seconds=STEP_S * i), heart_rate=heart_rate)
        for i, heart_rate in enumerate(heart_rates)
    ]
    evaluation = evaluate_readings(profile(), readings, onset_s=STEP_S, source="simulator")

    assert evaluation.artifact_counts == {}
    assert evaluation.proposed_suppressed == 0
    assert evaluation.tier_counts == {"NORMAL": 2, "WATCH": 11}
    for run in (evaluation.proposed, evaluation.naive):
        assert run.alerts == 2
        assert run.false_alerts == 0
        assert run.alerts_per_patient_day == 48.0


def test_evaluate_readings_lead_time_and_detection_delay_have_distinct_anchors():
    from clinical.comparator import evaluate_readings

    # Exact quarter-hours make the time anchors independent of report rounding.
    interval_s = 900.0
    readings = [
        make_obs(
            T0 + timedelta(seconds=interval_s * i),
            heart_rate=heart_rate,
            respiratory_rate=26.0 if i >= 4 else 14.0,
        )
        for i, heart_rate in enumerate((66.0, 70.0, 95.0, 105.0, 120.0, 121.0))
    ]
    evaluation = evaluate_readings(
        profile(), readings, onset_s=interval_s, source="simulator", interval_s=interval_s
    )

    assert evaluation.artifact_counts == {}
    assert evaluation.proposed_suppressed == 0
    assert evaluation.onset_hours == 0.25
    assert evaluation.severity_at_hours == 1.0
    assert evaluation.proposed.first_alert_hours == 0.5
    assert evaluation.naive.first_alert_hours == 0.75
    for run, lead_time, detection_delay in (
        (evaluation.proposed, 0.5, 0.25),
        (evaluation.naive, 0.25, 0.5),
    ):
        assert run.alerts == 1
        assert run.false_alerts == 0
        assert run.lead_time_hours == lead_time
        assert run.lead_time_hours == evaluation.severity_at_hours - run.first_alert_hours
        assert run.detection_delay_hours == detection_delay
        assert run.detection_delay_hours == run.first_alert_hours - evaluation.onset_hours
    assert evaluation.lead_time_gain_hours == 0.25


def test_pre_onset_naive_alarm_is_false_not_early_detection():
    from clinical.comparator import evaluate_readings

    interval_s = 900.0
    # Target-range Scale 2 saturations keep the naive monitor crying wolf
    # continuously, while the personalized monitor detects new deterioration.
    readings = [
        make_obs(
            T0 + timedelta(seconds=interval_s * i),
            heart_rate=heart_rate,
            respiratory_rate=respiratory_rate,
            spo2=91.0 + (i % 2) * 0.1,
        )
        for i, (heart_rate, respiratory_rate) in enumerate((
            (66.0, 14.0), (67.0, 15.0), (95.0, 18.0), (105.0, 22.0), (120.0, 26.0)
        ))
    ]
    evaluation = evaluate_readings(
        copd_profile(), readings, onset_s=2 * interval_s, source="simulator", interval_s=interval_s
    )

    assert evaluation.artifact_counts == {}
    assert evaluation.proposed_suppressed == 0
    assert evaluation.naive_alerting_before_onset is True
    assert evaluation.naive.alerts == evaluation.naive.false_alerts == 1
    assert evaluation.naive.first_alert_hours is None
    assert evaluation.naive.detection_delay_hours is None
    assert evaluation.naive.lead_time_hours is None
    assert evaluation.proposed.alerts == 1
    assert evaluation.proposed.false_alerts == 0
    assert evaluation.proposed.first_alert_hours == evaluation.onset_hours == 0.5
    assert evaluation.proposed.detection_delay_hours == 0.0
    assert evaluation.proposed.lead_time_hours == 0.5
    assert evaluation.lead_time_gain_hours is None


@pytest.mark.parametrize("source, expected_rate", [("bidmc", None), ("simulator", 180.0)])
def test_evaluate_readings_does_not_extrapolate_bidmc_alerts_per_day(source, expected_rate):
    from clinical.comparator import evaluate_readings

    interval_s = 60.0
    # Eight minutes at a one-minute cadence, with all sensor vitals varying
    # enough to avoid the artifact detector's six-sample flatline rule.
    readings = [
        make_obs(
            T0 + timedelta(seconds=interval_s * i),
            heart_rate=66.0 if i == 0 else 105.0 + i * 0.1,
            respiratory_rate=14.0 + i * 0.1,
            spo2=97.7 - i * 0.05,
            temperature=36.8 + i * 0.02,
            source=source,
        )
        for i in range(9)
    ]
    evaluation = evaluate_readings(
        profile(), readings, onset_s=interval_s, source=source, interval_s=interval_s
    )

    assert evaluation.is_recorded is (source == "bidmc")
    assert evaluation.span_hours == pytest.approx(8 / 60)
    assert evaluation.patient_days == pytest.approx(480 / 86400)
    assert evaluation.artifact_counts == {}
    assert evaluation.proposed_suppressed == 0
    for run in (evaluation.proposed, evaluation.naive):
        assert run.alerts == 1
        assert run.false_alerts == 0
        assert run.alerts_per_patient_day == expected_rate
    payload = evaluation.to_dict()
    assert payload["proposed"]["alerts_per_patient_day"] == expected_rate
    assert payload["naive"]["alerts_per_patient_day"] == expected_rate


# ------------------------------------------------------- public tier hysteresis
@pytest.mark.parametrize("vitals, expected_tier", [
    ({"heart_rate": 95.0}, Tier.WATCH),
    ({"heart_rate": 120.0, "respiratory_rate": 26.0}, Tier.REVIEW),
    ({"heart_rate": 135.0, "respiratory_rate": 26.0, "spo2": 94.0}, Tier.URGENT),
])
def test_process_escalates_immediately_to_any_higher_tier(vitals, expected_tier):
    monitor = PersonalizedMonitor(profile())
    initial = monitor.process(make_obs(T0))
    assert initial.tier is Tier.NORMAL
    assert initial.alert_kind is AlertKind.CLINICAL
    assert initial.artifact.is_artifact is False

    escalated = monitor.process(make_obs(T0 + timedelta(seconds=STEP_S), **vitals))

    assert escalated.suppressed is False
    assert escalated.artifact.is_artifact is False
    assert escalated.alert_kind is AlertKind.CLINICAL
    assert escalated.news2.tier is expected_tier
    assert escalated.tier is expected_tier
    assert escalated.raises_alert is True


@pytest.mark.parametrize("lower_vitals, lower_tier", [
    ({"heart_rate": 66.0}, Tier.NORMAL),
    ({"heart_rate": 95.0}, Tier.WATCH),
    ({"heart_rate": 120.0, "respiratory_rate": 26.0}, Tier.REVIEW),
])
def test_process_requires_six_lower_tier_readings_before_deescalation(lower_vitals, lower_tier):
    monitor = PersonalizedMonitor(profile())
    raised = monitor.process(make_obs(T0, heart_rate=135.0, respiratory_rate=26.0, spo2=94.0))
    assert raised.tier is Tier.URGENT
    assert raised.artifact.is_artifact is False
    assert raised.alert_kind is AlertKind.CLINICAL

    for i in range(1, 7):
        result = monitor.process(make_obs(T0 + timedelta(seconds=STEP_S * i), **lower_vitals))
        assert result.suppressed is False
        assert result.artifact.is_artifact is False
        assert result.alert_kind is AlertKind.CLINICAL
        assert result.news2.tier is lower_tier
        assert result.tier is (Tier.URGENT if i < 6 else lower_tier)
        assert result.raises_alert is (result.tier is not Tier.NORMAL)


def test_process_resets_lower_tier_streak_when_the_raised_tier_recurs():
    monitor = PersonalizedMonitor(profile())
    heart_rates = [135.0, 66.0, 67.0, 66.0, 67.0, 66.0, 135.0, 66.0, 67.0, 66.0, 67.0, 66.0, 67.0]
    for i, heart_rate in enumerate(heart_rates):
        urgent = heart_rate == 135.0
        result = monitor.process(make_obs(
            T0 + timedelta(seconds=STEP_S * i),
            heart_rate=heart_rate,
            respiratory_rate=26.0 if urgent else 14.0,
            spo2=94.0 if urgent else 97.7,
        ))
        assert result.suppressed is False
        assert result.artifact.is_artifact is False
        assert result.alert_kind is AlertKind.CLINICAL
        assert result.news2.tier is (Tier.URGENT if urgent else Tier.NORMAL)
        assert result.tier is (Tier.NORMAL if i == len(heart_rates) - 1 else Tier.URGENT)
