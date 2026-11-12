"""Scripted wearable events and BIDMC replay (PS-03 §3.2A and §3.2B)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from digital_twin.types import Observation
from simulator.bidmc import BidmcReplay, load_records
from simulator.generator import SubjectProfile
from simulator.wearable import (
    DAY,
    HOUR,
    SCRIPTS,
    EventKind,
    ScriptedEvent,
    VirtualWearable,
    WearableScript,
    sensor_artifact_script,
    slow_deterioration_script,
    stable_recovery_script,
)

T0 = datetime(2026, 11, 5, 8, 0, 0)


def wearable(script: WearableScript, **kwargs) -> VirtualWearable:
    return VirtualWearable(
        SubjectProfile(), script, seed=kwargs.pop("seed", 42), start=T0, rate_s=300.0, **kwargs
    )


def collect(device: VirtualWearable, limit: int = 4000) -> list[Observation | None]:
    out = []
    while not device.finished and len(out) < limit:
        out.append(device.next())
    return out


# ---------------------------------------------------------------- scripts
def test_named_scripts_cover_the_required_scenarios():
    assert set(SCRIPTS) == {"stable_recovery", "slow_deterioration", "sensor_artifact"}
    assert stable_recovery_script().deterioration_onset_s is None
    onset = slow_deterioration_script().deterioration_onset_s
    assert onset is not None and onset > 0
    # The stable lead-in must be long enough for the rolling baseline to form:
    # 12 samples minimum plus the 2 h recently-excluded window, at 5 min/reading.
    assert onset >= (12 + 24) * 300


def test_slow_deterioration_records_ground_truth_onset():
    script = slow_deterioration_script()
    assert script.deterioration_onset_s is not None
    drift = next(e for e in script.events if e.kind is EventKind.DRIFT)
    assert drift.start_s == script.deterioration_onset_s
    assert drift.start_s + drift.duration_s <= script.duration_s


def test_sensor_artifact_script_contains_every_fault_type():
    kinds = {e.kind for e in sensor_artifact_script().events}
    assert kinds == {
        EventKind.MOTION_NOISE,
        EventKind.DROPOUT,
        EventKind.SPIKE,
        EventKind.PROBE_REMOVAL,
    }


# ------------------------------------------------------------- determinism
def test_same_seed_replays_identically():
    script = stable_recovery_script()
    first = collect(wearable(script, seed=11))
    second = collect(wearable(script, seed=11))
    assert first == second


def test_different_seed_changes_the_noise():
    script = stable_recovery_script()
    first = collect(wearable(script, seed=11))
    second = collect(wearable(script, seed=12))
    assert first != second


# ----------------------------------------------------------------- events
def test_dropout_emits_nothing_but_advances_time():
    script = WearableScript(
        key="t", label="t", description="t", duration_s=6 * HOUR,
        events=[ScriptedEvent(EventKind.DROPOUT, HOUR, 900.0)],
    )
    device = wearable(script)
    readings = collect(device)
    assert None in readings, "a dropout must produce no observation"
    emitted = [r for r in readings if r is not None]
    gaps = [
        (b.timestamp - a.timestamp).total_seconds()
        for a, b in zip(emitted, emitted[1:])
    ]
    assert max(gaps) > 900.0, "the gap must span the dropout window"


def test_probe_removal_zeroes_spo2_and_freezes_heart_rate():
    script = WearableScript(
        key="t", label="t", description="t", duration_s=6 * HOUR,
        events=[ScriptedEvent(EventKind.PROBE_REMOVAL, HOUR, 1800.0)],
    )
    readings = [r for r in collect(wearable(script)) if r is not None]
    removed = [r for r in readings if r.source == "simulator:probe_removal"]
    assert removed, "probe removal must be labelled in the reading source"
    assert all(r.spo2 == 0.0 for r in removed)
    assert len({r.heart_rate for r in removed}) == 1, "heart rate must be frozen"


def test_probe_removal_can_report_absent_spo2_instead_of_zero():
    script = WearableScript(
        key="t", label="t", description="t", duration_s=6 * HOUR,
        events=[ScriptedEvent(EventKind.PROBE_REMOVAL, HOUR, 1800.0)],
    )
    readings = [r for r in collect(wearable(script, probe_spo2_null=True)) if r is not None]
    removed = [r for r in readings if r.source == "simulator:probe_removal"]
    assert removed and all(r.spo2 is None for r in removed)


def test_drift_ramps_gradually_and_monotonically():
    script = WearableScript(
        key="t", label="t", description="t", duration_s=3 * DAY,
        events=[ScriptedEvent(EventKind.DRIFT, DAY, 2 * DAY, 1.0)],
        deterioration_onset_s=DAY,
    )
    readings = [r for r in collect(wearable(script)) if r is not None]
    before = [r for r in readings if r.timestamp < T0 + timedelta(seconds=DAY)]
    after = [r for r in readings if r.timestamp >= T0 + timedelta(seconds=2 * DAY)]
    assert before and after
    # Respiratory rate and temperature climb; SpO2 falls. All are gradual.
    assert sum(r.respiratory_rate for r in after) / len(after) > sum(
        r.respiratory_rate for r in before
    ) / len(before)
    assert sum(r.spo2 for r in after) / len(after) < sum(r.spo2 for r in before) / len(before)
    steps = [
        b.respiratory_rate - a.respiratory_rate for a, b in zip(readings, readings[1:])
    ]
    # Sensor noise still varies reading to reading; what must not happen is a
    # step change. A scripted SPIKE would move this by 12 br/min at once.
    assert max(steps) < 4.0


def test_motion_noise_is_labelled_with_high_activity():
    script = WearableScript(
        key="t", label="t", description="t", duration_s=4 * HOUR,
        events=[ScriptedEvent(EventKind.MOTION_NOISE, HOUR, 600.0, 1.0)],
    )
    readings = [r for r in collect(wearable(script)) if r is not None]
    # A reading is stamped at the end of its interval, so the one stamped at
    # the event start still describes the interval before it.
    moving = [
        r for r in readings
        if HOUR + 300 <= (r.timestamp - T0).total_seconds() < HOUR + 600
    ]
    assert moving and all(r.activity_intensity >= 0.8 for r in moving)


def test_wearable_supplies_temperature_and_blood_pressure():
    readings = [r for r in collect(wearable(stable_recovery_script())) if r is not None]
    assert all(r.temperature is not None for r in readings)
    assert all(r.systolic_bp is not None and r.diastolic_bp is not None for r in readings)


# ------------------------------------------------------------ BIDMC replay
@pytest.fixture(scope="module")
def records():
    return load_records()


def test_bidmc_records_load(records):
    assert len(records) >= 5
    for record in records:
        assert record.samples
        assert record.duration_s > 60


def test_bidmc_timestamps_strictly_increase(records):
    replay = BidmcReplay(records[:1], patient_id="p-bidmc", start=T0)
    observations = list(replay.observations())
    assert len(observations) == len(records[0].samples)
    assert all(b.timestamp > a.timestamp for a, b in zip(observations, observations[1:]))


def test_bidmc_has_no_temperature_or_blood_pressure(records):
    replay = BidmcReplay(records[:1], patient_id="p-bidmc", start=T0)
    for obs in replay.observations():
        assert obs.temperature is None
        assert obs.systolic_bp is None and obs.diastolic_bp is None
        assert obs.source.startswith("bidmc:")


def test_bidmc_vitals_are_plausible(records):
    replay = BidmcReplay(records[:1], patient_id="p-bidmc", start=T0)
    for obs in replay.observations():
        assert 20 <= obs.heart_rate <= 250
        assert obs.spo2 is None or 50 <= obs.spo2 <= 100
        assert obs.respiratory_rate is None or 4 <= obs.respiratory_rate <= 60


def test_bidmc_replay_is_deterministic(records):
    def run():
        return list(BidmcReplay(records[:1], patient_id="p", start=T0).observations())

    assert run() == run()


def test_bidmc_loops_when_asked(records):
    replay = BidmcReplay(records[:1], patient_id="p", start=T0, loop=True)
    count = len(records[0].samples)
    for _ in range(count + 10):
        assert replay.next() is not None
    assert not replay.finished