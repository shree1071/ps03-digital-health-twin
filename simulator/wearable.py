"""Virtual wearable simulator with scripted sensor events (PS-03 §3.2A).

Wraps the existing :class:`PhysiologySimulator` and applies a scripted event
layer on top of its correlated physiology. Events are explicit and seeded, so
a scenario replays identically every time — which is what makes detection
lead time measurable.

Supported events, as required by §3.2A:

* ``DRIFT``         gradual vital-sign drift over hours or days
* ``SPIKE``         sudden spike
* ``DROPOUT``       missing readings (nothing is emitted)
* ``MOTION_NOISE``  motion-related noise during high movement
* ``PROBE_REMOVAL`` SpO2 zero/null while heart rate flatlines

Body temperature and blood pressure are generated here, because the BIDMC
dataset provides neither (§3.2A).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum

from digital_twin.types import Observation
from simulator.generator import ActivityInput, PhysiologySimulator, SubjectProfile


class EventKind(str, Enum):
    DRIFT = "DRIFT"
    SPIKE = "SPIKE"
    DROPOUT = "DROPOUT"
    MOTION_NOISE = "MOTION_NOISE"
    PROBE_REMOVAL = "PROBE_REMOVAL"


@dataclass(frozen=True)
class ScriptedEvent:
    kind: EventKind
    start_s: float
    duration_s: float
    magnitude: float = 1.0
    # Restrict a SPIKE to specific channels. A lone impossible value with the
    # other vitals unchanged is a sensor fault; a coherent multi-vital change
    # is genuine deterioration and must not be suppressed.
    channels: tuple[str, ...] | None = None

    def active(self, elapsed_s: float) -> bool:
        return self.start_s <= elapsed_s < self.start_s + self.duration_s

    def progress(self, elapsed_s: float) -> float:
        if self.duration_s <= 0:
            return 1.0
        return min(max((elapsed_s - self.start_s) / self.duration_s, 0.0), 1.0)


@dataclass(frozen=True)
class VitalTargets:
    """Resting vitals and blood pressure for a simulated patient."""

    systolic_bp: float = 122.0
    diastolic_bp: float = 78.0


# Per-unit-magnitude drift applied at full progress. Chosen so a slow
# deterioration stays inside population reference ranges for a long time
# before crossing fixed thresholds — the case the rolling baseline must catch.
DRIFT_PER_MAGNITUDE: dict[str, float] = {
    "heart_rate": 18.0,
    "respiratory_rate": 8.0,
    "temperature": 1.6,
    "spo2": -6.0,
    "systolic_bp": 26.0,
    "diastolic_bp": 12.0,
}

SPIKE_PER_MAGNITUDE: dict[str, float] = {
    "heart_rate": 45.0,
    "respiratory_rate": 12.0,
    "temperature": 1.9,
    "spo2": -55.0,
    "systolic_bp": 40.0,
}


@dataclass
class WearableScript:
    """A named, deterministic sequence of events over simulated time."""

    key: str
    label: str
    description: str
    duration_s: float
    events: list[ScriptedEvent] = field(default_factory=list)
    deterioration_onset_s: float | None = None

    def active_events(self, elapsed_s: float) -> list[ScriptedEvent]:
        return [e for e in self.events if e.active(elapsed_s)]


DAY = 86400.0
HOUR = 3600.0


def stable_recovery_script() -> WearableScript:
    """Stable post-discharge recovery, with brief everyday movement."""
    return WearableScript(
        key="stable_recovery",
        label="Stable recovery",
        description="Patient recovers at home. Vitals stay near their own baseline.",
        duration_s=2 * DAY,
        events=[
            ScriptedEvent(EventKind.MOTION_NOISE, 6 * HOUR, 120.0, 0.6),
            ScriptedEvent(EventKind.DROPOUT, 20 * HOUR, 240.0),
        ],
    )


def slow_deterioration_script() -> WearableScript:
    """Gradual deterioration over two days, the lead-time scenario.

    Twelve stable hours let the rolling baseline form, then vitals drift for
    thirty-six hours. Ground-truth onset is recorded so lead time is measurable.
    The span is compressed to demo length: at one reading per five simulated
    minutes this replays in about five minutes of presentation time.
    """
    onset = 12 * HOUR
    return WearableScript(
        key="slow_deterioration",
        label="Slow deterioration",
        description=(
            "Twelve stable hours, then a gradual post-discharge infection: breathing "
            "rate and temperature climb, SpO₂ falls, pulse rises slowly."
        ),
        duration_s=48 * HOUR,
        events=[ScriptedEvent(EventKind.DRIFT, onset, 36 * HOUR, 1.0)],
        deterioration_onset_s=onset,
    )


def sensor_artifact_script() -> WearableScript:
    """Sensor faults on an otherwise stable patient."""
    return WearableScript(
        key="sensor_artifact",
        label="Sensor artifact",
        description=(
            "Stable patient, unreliable device: motion noise, a dropout, an "
            "impossible SpO₂ collapse on one channel, then probe removal with "
            "SpO₂ at zero and a frozen heart rate."
        ),
        duration_s=12 * HOUR,
        events=[
            ScriptedEvent(EventKind.MOTION_NOISE, 2 * HOUR, 300.0, 1.0),
            ScriptedEvent(EventKind.DROPOUT, 5 * HOUR, 900.0),
            ScriptedEvent(EventKind.SPIKE, 7 * HOUR, 180.0, 1.0, channels=("spo2",)),
            ScriptedEvent(EventKind.PROBE_REMOVAL, 9 * HOUR, 1800.0),
        ],
    )


SCRIPTS: dict[str, WearableScript] = {
    s.key: s for s in (stable_recovery_script(), slow_deterioration_script(), sensor_artifact_script())
}


class VirtualWearable:
    """Streams readings for one patient at a configurable rate.

    ``rate_s`` is simulated seconds between readings. ``None`` from
    :meth:`next` means the device emitted nothing (a dropout).
    """

    def __init__(
        self,
        profile: SubjectProfile,
        script: WearableScript,
        *,
        seed: int = 42,
        start: datetime | None = None,
        rate_s: float = 300.0,
        targets: VitalTargets | None = None,
        probe_spo2_null: bool = False,
    ) -> None:
        self.profile = profile
        self.script = script
        self.rate_s = rate_s
        self.targets = targets or VitalTargets()
        self.probe_spo2_null = probe_spo2_null
        self.simulator = PhysiologySimulator(profile, seed=seed, start=start)
        self.elapsed_s = 0.0
        self._frozen_hr: float | None = None
        self._bp_noise = 0.0
        self._rng = self.simulator.rng

    @property
    def finished(self) -> bool:
        return self.elapsed_s >= self.script.duration_s

    def next(self) -> Observation | None:
        if self.finished:
            return None
        events = self.script.active_events(self.elapsed_s)
        kinds = {e.kind for e in events}

        if EventKind.DROPOUT in kinds:
            self._advance(self.rate_s, ActivityInput(intensity=0.03))
            self.elapsed_s += self.rate_s
            return None

        intensity = 0.03
        if EventKind.MOTION_NOISE in kinds:
            intensity = 0.85
        activity = ActivityInput(intensity=intensity)
        self._advance(self.rate_s, activity)
        obs = self.simulator.emit(activity, source="simulator")
        obs = self._apply_bp(obs)
        obs = self._apply_events(obs, events, kinds)
        self.elapsed_s += self.rate_s
        return obs

    # ------------------------------------------------------------- internals
    def _advance(self, dt_s: float, activity: ActivityInput) -> None:
        remaining = dt_s
        while remaining > 1e-9:
            chunk = min(60.0, remaining)
            self.simulator.advance(chunk, activity)
            remaining -= chunk

    def _apply_bp(self, obs: Observation) -> Observation:
        self._bp_noise = 0.85 * self._bp_noise + float(self._rng.normal(0.0, 2.0))
        return replace(
            obs,
            systolic_bp=round(self.targets.systolic_bp + self._bp_noise, 1),
            diastolic_bp=round(self.targets.diastolic_bp + self._bp_noise * 0.6, 1),
        )

    def _apply_events(
        self, obs: Observation, events: list[ScriptedEvent], kinds: set[EventKind]
    ) -> Observation:
        if EventKind.PROBE_REMOVAL in kinds:
            if self._frozen_hr is None:
                self._frozen_hr = obs.heart_rate
            return replace(
                obs,
                spo2=None if self.probe_spo2_null else 0.0,
                heart_rate=self._frozen_hr,
                source="simulator:probe_removal",
            )
        self._frozen_hr = None

        deltas: dict[str, float] = {}
        for event in events:
            if event.kind is EventKind.DRIFT:
                progress = event.progress(self.elapsed_s)
                for metric, per_unit in DRIFT_PER_MAGNITUDE.items():
                    deltas[metric] = deltas.get(metric, 0.0) + per_unit * event.magnitude * progress
            elif event.kind is EventKind.SPIKE:
                for metric, per_unit in SPIKE_PER_MAGNITUDE.items():
                    if event.channels is not None and metric not in event.channels:
                        continue
                    deltas[metric] = deltas.get(metric, 0.0) + per_unit * event.magnitude

        if EventKind.MOTION_NOISE in kinds:
            deltas["heart_rate"] = deltas.get("heart_rate", 0.0) + float(
                self._rng.normal(0.0, 22.0)
            )
            deltas["spo2"] = deltas.get("spo2", 0.0) + float(self._rng.normal(0.0, 5.0))
            obs = replace(obs, activity_intensity=0.85)

        if not deltas:
            return obs

        def adjusted(metric: str, value: float | None) -> float | None:
            if value is None:
                return None
            return round(value + deltas.get(metric, 0.0), 2)

        return replace(
            obs,
            heart_rate=max(adjusted("heart_rate", obs.heart_rate) or 0.0, 25.0),
            spo2=min(max(adjusted("spo2", obs.spo2) or 0.0, 40.0), 100.0),
            respiratory_rate=max(adjusted("respiratory_rate", obs.respiratory_rate) or 0.0, 4.0),
            temperature=adjusted("temperature", obs.temperature),
            systolic_bp=adjusted("systolic_bp", obs.systolic_bp),
            diastolic_bp=adjusted("diastolic_bp", obs.diastolic_bp),
            source="simulator:drift" if EventKind.DRIFT in kinds else obs.source,
        )