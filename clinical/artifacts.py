"""Sensor artifact detection and false-alert suppression (PS-03 §3.5).

The hard part of remote monitoring is not spotting an abnormal number, it is
deciding whether the number is real. A pulse oximeter that falls off produces
a SpO2 of 0 and a frozen heart rate, which a naive threshold monitor reads as
a patient in extremis.

Discriminating principle used here: genuine deterioration is *coherent and
physiologically plausible* — several vitals drift together within possible
ranges. Sensor faults are *incoherent or impossible* — a value outside the
range compatible with life, a signal with literally zero variance, or a
single reading that contradicts its neighbours.

Artifact findings never silently disappear: they are reported as sensor
issues so the clinician can see the device needs attention.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from digital_twin.types import Observation


class ArtifactKind(str, Enum):
    PROBE_REMOVAL = "PROBE_REMOVAL"
    FLATLINE = "FLATLINE"
    IMPLAUSIBLE = "IMPLAUSIBLE"
    MOTION_NOISE = "MOTION_NOISE"
    DROPOUT = "DROPOUT"


# Ranges outside which a reading cannot be a real measurement of a living
# adult. SpO2 of 0 is excluded from the plausible range on purpose: it is the
# signature of a disconnected probe, not of a patient.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "heart_rate": (20.0, 250.0),
    "spo2": (50.0, 100.0),
    "respiratory_rate": (4.0, 60.0),
    "temperature": (30.0, 43.0),
}

METRIC_LABELS: dict[str, str] = {
    "heart_rate": "Heart rate",
    "spo2": "SpO₂",
    "respiratory_rate": "Respiratory rate",
    "temperature": "Temperature",
}


@dataclass(frozen=True)
class ArtifactConfig:
    flatline_min_samples: int = 6
    flatline_window_s: float = 180.0
    flatline_tolerance: float = 0.01
    motion_hr_jump: float = 30.0
    motion_spo2_jump: float = 6.0
    motion_min_intensity: float = 0.45
    # A gap is a dropout only when it is clearly longer than the device's own
    # reporting cadence — a reading every 5 min is not a connectivity gap.
    expected_interval_s: float = 300.0
    dropout_gap_factor: float = 1.5

    @property
    def dropout_gap_s(self) -> float:
        return self.expected_interval_s * self.dropout_gap_factor


@dataclass
class ArtifactVerdict:
    """Whether a reading can be trusted, and why not."""

    timestamp: datetime
    is_artifact: bool = False
    kinds: list[ArtifactKind] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    untrusted_metrics: list[str] = field(default_factory=list)
    gap_seconds: float | None = None

    @property
    def suppress_clinical_alert(self) -> bool:
        return self.is_artifact

    def trusted(self, metric: str) -> bool:
        return metric not in self.untrusted_metrics

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "is_artifact": self.is_artifact,
            "kinds": [k.value for k in self.kinds],
            "reasons": list(self.reasons),
            "untrusted_metrics": list(self.untrusted_metrics),
            "gap_seconds": self.gap_seconds,
        }


def _is_flatline(values: Sequence[float], tolerance: float) -> bool:
    if len(values) < 2:
        return False
    return (max(values) - min(values)) <= tolerance


class ArtifactDetector:
    """Stateful per-patient detector. Feed it readings in timestamp order."""

    def __init__(self, config: ArtifactConfig | None = None) -> None:
        self.config = config or ArtifactConfig()
        self._history: list[Observation] = []
        self._last_timestamp: datetime | None = None

    def assess(self, obs: Observation) -> ArtifactVerdict:
        cfg = self.config
        verdict = ArtifactVerdict(timestamp=obs.timestamp)

        if self._last_timestamp is not None:
            gap = (obs.timestamp - self._last_timestamp).total_seconds()
            if gap >= cfg.dropout_gap_s:
                verdict.gap_seconds = gap
                verdict.kinds.append(ArtifactKind.DROPOUT)
                verdict.reasons.append(
                    f"No readings for {gap / 60:.0f} min before this one — device connectivity gap."
                )

        self._check_implausible(obs, verdict)
        self._check_probe_removal(obs, verdict)
        self._check_flatlines(obs, verdict)
        self._check_motion(obs, verdict)

        verdict.is_artifact = bool(verdict.kinds)
        # De-duplicate: a metric flagged twice is still one untrusted metric.
        verdict.untrusted_metrics = sorted(set(verdict.untrusted_metrics))
        self._history.append(obs)
        history_limit = max(120, cfg.flatline_min_samples - 1)
        if len(self._history) > history_limit:
            del self._history[:-history_limit]
        self._last_timestamp = obs.timestamp
        return verdict

    # ------------------------------------------------------------- checks
    def _check_implausible(self, obs: Observation, verdict: ArtifactVerdict) -> None:
        for metric, (low, high) in PLAUSIBLE_RANGES.items():
            value = getattr(obs, metric)
            if value is None:
                continue
            if metric == "spo2" and value == 0:
                continue  # handled as probe removal, not as a generic outlier
            if not (low <= value <= high):
                verdict.kinds.append(ArtifactKind.IMPLAUSIBLE)
                verdict.untrusted_metrics.append(metric)
                verdict.reasons.append(
                    f"{METRIC_LABELS[metric]} {value:g} is outside the physiologically "
                    f"possible range {low:g}–{high:g} — sensor fault, not a patient value."
                )

    def _check_probe_removal(self, obs: Observation, verdict: ArtifactVerdict) -> None:
        spo2_missing = obs.spo2 is None or obs.spo2 == 0
        if not spo2_missing:
            return
        cfg = self.config
        hr_window = self._recent_values(
            "heart_rate",
            obs,
            window_s=max(
                cfg.flatline_window_s,
                cfg.expected_interval_s * (cfg.flatline_min_samples - 1),
            ),
            max_gap_s=cfg.dropout_gap_s,
        )
        hr_flat = _is_flatline(hr_window, cfg.flatline_tolerance) and len(
            hr_window
        ) >= cfg.flatline_min_samples
        if obs.spo2 == 0:
            verdict.kinds.append(ArtifactKind.PROBE_REMOVAL)
            verdict.untrusted_metrics.extend(["spo2", "heart_rate"])
            detail = "SpO₂ reads exactly 0%"
            if hr_flat:
                detail += f" and heart rate is frozen at {hr_window[-1]:g} bpm"
            verdict.reasons.append(
                f"{detail}. 0% saturation is not compatible with a monitored patient — "
                "probe removal. Clinical alert suppressed."
            )
        elif hr_flat:
            verdict.kinds.append(ArtifactKind.PROBE_REMOVAL)
            verdict.untrusted_metrics.extend(["spo2", "heart_rate"])
            verdict.reasons.append(
                f"SpO₂ absent while heart rate is frozen at {hr_window[-1]:g} bpm "
                f"over {len(hr_window)} readings — probe removal. Clinical alert suppressed."
            )

    def _check_flatlines(self, obs: Observation, verdict: ArtifactVerdict) -> None:
        for metric in ("heart_rate", "respiratory_rate", "spo2", "temperature"):
            if metric in verdict.untrusted_metrics:
                continue
            values = self._recent_values(metric, obs)
            if len(values) < self.config.flatline_min_samples:
                continue
            if _is_flatline(values, self.config.flatline_tolerance):
                verdict.kinds.append(ArtifactKind.FLATLINE)
                verdict.untrusted_metrics.append(metric)
                verdict.reasons.append(
                    f"{METRIC_LABELS[metric]} has not varied by more than "
                    f"{self.config.flatline_tolerance:g} across {len(values)} readings "
                    f"({values[-1]:g}) — flatlined signal, treated as sensor fault."
                )

    def _check_motion(self, obs: Observation, verdict: ArtifactVerdict) -> None:
        if obs.activity_intensity < self.config.motion_min_intensity or not self._history:
            return
        prev = self._history[-1]
        jumps: list[str] = []
        if prev.heart_rate is not None and obs.heart_rate is not None:
            if abs(obs.heart_rate - prev.heart_rate) >= self.config.motion_hr_jump:
                jumps.append(
                    f"heart rate jumped {abs(obs.heart_rate - prev.heart_rate):g} bpm"
                )
        if prev.spo2 is not None and obs.spo2 is not None:
            if abs(obs.spo2 - prev.spo2) >= self.config.motion_spo2_jump:
                jumps.append(f"SpO₂ jumped {abs(obs.spo2 - prev.spo2):g}%")
        if jumps:
            verdict.kinds.append(ArtifactKind.MOTION_NOISE)
            verdict.untrusted_metrics.extend(["heart_rate", "spo2"])
            verdict.reasons.append(
                "During high movement (intensity "
                f"{obs.activity_intensity:.2f}), {' and '.join(jumps)} between consecutive "
                "readings — motion artifact rather than a physiological change."
            )

    # -------------------------------------------------------------- helpers
    def _recent_values(self, metric: str, obs: Observation) -> list[float]:
        """Values for ``metric`` inside the flatline window, including ``obs``."""
        cutoff = self.config.flatline_window_s
        values: list[float] = []
        current = getattr(obs, metric)
        if current is not None:
            values.append(float(current))
        for item in reversed(self._history):
            if (obs.timestamp - item.timestamp).total_seconds() > cutoff:
                break
            value = getattr(item, metric)
            if value is not None:
                values.append(float(value))
        values.reverse()
        return values