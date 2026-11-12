"""Artifact detection tests, including the critical no-false-suppression case."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clinical.artifacts import ArtifactConfig, ArtifactDetector, ArtifactKind
from digital_twin.types import Observation

START = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)


def reading(seconds: float, **kwargs) -> Observation:
    base = dict(
        timestamp=START + timedelta(seconds=seconds),
        heart_rate=72.0,
        spo2=97.0,
        respiratory_rate=16.0,
        temperature=36.8,
        activity_intensity=0.0,
    )
    base.update(kwargs)
    return Observation(**base)


def test_probe_removal_spo2_zero_with_flat_hr():
    detector = ArtifactDetector()
    verdict = None
    for i in range(8):
        verdict = detector.assess(reading(i * 20, spo2=0.0 if i == 7 else 97.0))
    assert verdict is not None
    assert verdict.is_artifact
    assert ArtifactKind.PROBE_REMOVAL in verdict.kinds
    assert verdict.suppress_clinical_alert
    assert "spo2" in verdict.untrusted_metrics
    assert "heart_rate" in verdict.untrusted_metrics
    assert any("probe removal" in r.lower() for r in verdict.reasons)


def test_probe_removal_spo2_absent_with_flat_hr():
    detector = ArtifactDetector()
    verdict = None
    for i in range(8):
        verdict = detector.assess(reading(i * 20, spo2=None if i == 7 else 97.0))
    assert verdict is not None
    assert ArtifactKind.PROBE_REMOVAL in verdict.kinds


@pytest.mark.parametrize(
    ("config", "interval_s"),
    [
        (None, 300),
        (None, 1),
        (ArtifactConfig(expected_interval_s=1), 1),
        (ArtifactConfig(flatline_min_samples=122), 300),
    ],
)
def test_null_spo2_frozen_hr_requires_minimum_samples(config, interval_s):
    detector = ArtifactDetector(config)
    minimum = detector.config.flatline_min_samples
    for i in range(minimum):
        verdict = detector.assess(reading(i * interval_s, spo2=None))
        if i < minimum - 1:
            assert ArtifactKind.PROBE_REMOVAL not in verdict.kinds
        else:
            assert ArtifactKind.PROBE_REMOVAL in verdict.kinds
            assert verdict.suppress_clinical_alert
            assert not verdict.trusted("spo2")
            assert not verdict.trusted("heart_rate")


@pytest.mark.parametrize("interval_s", [1, 300])
def test_absent_spo2_with_varied_hr_is_not_probe_removal(interval_s):
    detector = ArtifactDetector(ArtifactConfig(expected_interval_s=interval_s))
    for i in range(8):
        verdict = detector.assess(
            reading(i * interval_s, spo2=None, heart_rate=70.0 + i)
        )
        assert ArtifactKind.PROBE_REMOVAL not in verdict.kinds
        assert verdict.trusted("heart_rate")


@pytest.mark.parametrize("expected_interval_s", [1, 300])
def test_zero_spo2_is_immediate_probe_removal(expected_interval_s):
    detector = ArtifactDetector(
        ArtifactConfig(expected_interval_s=expected_interval_s)
    )
    verdict = detector.assess(reading(0, spo2=0.0))
    assert ArtifactKind.PROBE_REMOVAL in verdict.kinds
    assert verdict.suppress_clinical_alert
    assert not verdict.trusted("spo2")
    assert not verdict.trusted("heart_rate")


@pytest.mark.parametrize(
    ("interval_s", "gap_s"), [(300, 450), (300, 600), (1, 1.5)]
)
def test_probe_removal_does_not_combine_hr_across_dropout(interval_s, gap_s):
    detector = ArtifactDetector(ArtifactConfig(expected_interval_s=interval_s))
    minimum = detector.config.flatline_min_samples
    for i in range(minimum - 1):
        detector.assess(reading(i * interval_s, spo2=None))
    resumed_at = (minimum - 2) * interval_s + gap_s
    for i in range(minimum):
        verdict = detector.assess(reading(resumed_at + i * interval_s, spo2=None))
        if i == 0:
            assert ArtifactKind.DROPOUT in verdict.kinds
            assert verdict.gap_seconds == gap_s
        else:
            assert ArtifactKind.DROPOUT not in verdict.kinds
        if i < minimum - 1:
            assert ArtifactKind.PROBE_REMOVAL not in verdict.kinds
        else:
            assert ArtifactKind.PROBE_REMOVAL in verdict.kinds


def test_implausible_heart_rate():
    detector = ArtifactDetector()
    verdict = detector.assess(reading(0, heart_rate=300.0))
    assert ArtifactKind.IMPLAUSIBLE in verdict.kinds
    assert "heart_rate" in verdict.untrusted_metrics


def test_implausible_spo2_above_100():
    detector = ArtifactDetector()
    verdict = detector.assess(reading(0, spo2=140.0))
    assert ArtifactKind.IMPLAUSIBLE in verdict.kinds
    assert "spo2" in verdict.untrusted_metrics


def test_implausible_temperature():
    detector = ArtifactDetector()
    verdict = detector.assess(reading(0, temperature=20.0))
    assert ArtifactKind.IMPLAUSIBLE in verdict.kinds
    assert "temperature" in verdict.untrusted_metrics


def test_flatlined_heart_rate_is_sensor_fault():
    detector = ArtifactDetector()
    verdict = None
    for i in range(8):
        # SpO2 varies so only the heart rate flatlines.
        verdict = detector.assess(reading(i * 20, spo2=96.0 + (i % 3)))
    assert verdict is not None
    assert ArtifactKind.FLATLINE in verdict.kinds
    assert "heart_rate" in verdict.untrusted_metrics


def test_repeated_coarse_vitals_with_spo2_are_not_flatlines():
    detector = ArtifactDetector()
    for i in range(8):
        verdict = detector.assess(reading(i * 300))
        assert not verdict.is_artifact


def test_motion_noise_during_high_activity():
    detector = ArtifactDetector()
    detector.assess(reading(0, heart_rate=70.0, spo2=97.0, activity_intensity=0.0))
    verdict = detector.assess(
        reading(20, heart_rate=115.0, spo2=88.0, activity_intensity=0.8)
    )
    assert ArtifactKind.MOTION_NOISE in verdict.kinds
    assert verdict.suppress_clinical_alert


def test_same_jump_at_rest_is_not_motion_artifact():
    detector = ArtifactDetector()
    detector.assess(reading(0, heart_rate=70.0, activity_intensity=0.0))
    verdict = detector.assess(reading(20, heart_rate=115.0, activity_intensity=0.0))
    assert ArtifactKind.MOTION_NOISE not in verdict.kinds


def test_dropout_gap_is_flagged():
    detector = ArtifactDetector()
    detector.assess(reading(0))
    verdict = detector.assess(reading(900))
    assert ArtifactKind.DROPOUT in verdict.kinds
    assert verdict.gap_seconds == 900


def test_genuine_gradual_deterioration_is_not_suppressed():
    """The decisive test: real deterioration must survive the artifact filter.

    Coherent, plausible, multi-vital drift with a varying (not flatlined)
    heart rate and no movement must produce no artifact verdict.
    """
    detector = ArtifactDetector()
    verdicts = []
    for i in range(12):
        verdicts.append(
            detector.assess(
                reading(
                    i * 60,
                    heart_rate=72.0 + i * 2.5,
                    spo2=97.0 - i * 0.5,
                    respiratory_rate=16.0 + i * 0.8,
                    temperature=36.8 + i * 0.15,
                )
            )
        )
    assert not any(v.is_artifact for v in verdicts), [
        (i, v.kinds, v.reasons) for i, v in enumerate(verdicts) if v.is_artifact
    ]


def test_normal_varied_readings_are_clean():
    detector = ArtifactDetector()
    for i in range(10):
        verdict = detector.assess(
            reading(
                i * 30,
                heart_rate=70.0 + (i % 5),
                spo2=96.0 + (i % 4),
                respiratory_rate=15.0 + (i % 3),
                temperature=36.7 + (i % 3) * 0.1,
            )
        )
    assert not verdict.is_artifact


def test_artifact_reasons_are_human_readable():
    detector = ArtifactDetector()
    verdict = detector.assess(reading(0, heart_rate=300.0))
    assert verdict.reasons
    assert all(len(r) > 20 for r in verdict.reasons)