"""Plain data types shared by the engine, simulator and backend.

Dataclasses (not Pydantic) keep the engine independent of the API layer.
The backend converts between these and its Pydantic/ORM models.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TwinState(str, Enum):
    """Discrete wellness/activity states of the twin.

    These are descriptive states of the *data*, not medical diagnoses.
    Priority (highest first) is defined in ``state_engine.STATE_PRIORITY``.
    """

    STABLE = "STABLE"
    ACTIVE = "ACTIVE"
    HIGH_ACTIVITY = "HIGH_ACTIVITY"
    RECOVERY = "RECOVERY"
    SLEEPING = "SLEEPING"
    ELEVATED = "ELEVATED"
    ANOMALOUS = "ANOMALOUS"


# Vital-sign metrics that the anomaly layer assesses.
VITAL_METRICS: tuple[str, ...] = ("heart_rate", "spo2", "temperature", "respiratory_rate")

METRIC_UNITS: dict[str, str] = {
    "heart_rate": "bpm",
    "spo2": "%",
    "temperature": "°C",
    "respiratory_rate": "br/min",
    "steps": "steps",
    "activity_intensity": "0–1",
}

METRIC_LABELS: dict[str, str] = {
    "heart_rate": "Heart rate",
    "spo2": "SpO₂",
    "temperature": "Skin/core temperature",
    "respiratory_rate": "Respiratory rate",
}


@dataclass(frozen=True)
class Observation:
    """One normalized reading from any source (simulator, CSV, wearable, API).

    Heart rate is required. SpO2, temperature and respiratory rate are
    optional because many consumer wearables do not measure them; a missing
    vital is never invented, it is simply not assessed.

    ``steps`` is the number of steps counted since the previous reading.
    ``activity_intensity`` is a normalized 0–1 movement intensity
    (0 = still, ~0.3 = walking, ~0.8 = running), as produced by an
    accelerometer-based activity classifier.
    """

    timestamp: datetime
    heart_rate: float
    spo2: float | None = None
    temperature: float | None = None
    respiratory_rate: float | None = None
    steps: int = 0
    activity_intensity: float = 0.0
    is_asleep: bool = False
    source: str = "simulator"
    # NEWS2 also scores systolic BP. BIDMC provides neither BP nor temperature,
    # so both come from the simulator (PS-03 §3.2A).
    systolic_bp: float | None = None
    diastolic_bp: float | None = None


@dataclass
class Baseline:
    """Personal baseline learned from the subject's own history.

    Centers are medians and spreads are scaled MAD (median absolute
    deviation x 1.4826), both robust to the occasional outlier or
    anomalous period in the history.
    """

    hr_rest: float
    hr_rest_spread: float
    hr_sleep: float
    rr_rest: float
    rr_rest_spread: float
    rr_sleep: float
    spo2: float
    spo2_spread: float
    temp_awake: float
    temp_spread: float
    temp_sleep: float
    hr_max_est: float
    daily_steps: float
    sleep_hours: float
    sample_count: int
    window_start: datetime | None = None
    window_end: datetime | None = None
    # Vitals actually present in the history. Others use population fallbacks
    # and are never assessed.
    metrics_available: list[str] = field(default_factory=lambda: list(VITAL_METRICS))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("window_start", "window_end"):
            value = data[key]
            data[key] = value.isoformat() if value else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Baseline":
        data = dict(data)
        for key in ("window_start", "window_end"):
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        return cls(**data)


@dataclass
class MetricAssessment:
    """How one vital compares with what the twin expects right now."""

    metric: str
    value: float
    expected: float  # nearest edge of the expected band (or its centre when inside)
    expected_low: float
    expected_high: float
    deviation: float  # distance outside the expected band, in metric units (0 inside)
    deviation_pct: float  # relative deviation, %
    robust_z: float  # deviation / personal spread
    status: str  # "normal" | "watch" | "anomalous"
    consecutive: int  # consecutive readings the candidate condition has held
    context: str  # e.g. "at rest", "during high activity", "asleep"
    reason: str | None = None  # human-readable explanation when not normal
    reference_flag: str | None = None  # outside general adult reference range

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TwinSnapshot:
    """The complete twin state after ingesting one observation."""

    timestamp: datetime
    state: TwinState
    previous_state: TwinState | None
    state_since: datetime
    state_reason: str
    transitioned: bool
    observation: Observation
    assessments: dict[str, MetricAssessment]
    effective_intensity: float
    rolling: dict[str, float] = field(default_factory=dict)
    new_anomalies: list[MetricAssessment] = field(default_factory=list)
    resolved_anomalies: list[str] = field(default_factory=list)
    hr_recovery_bpm: float | None = None

    @property
    def active_anomalies(self) -> list[MetricAssessment]:
        return [a for a in self.assessments.values() if a.status == "anomalous"]
