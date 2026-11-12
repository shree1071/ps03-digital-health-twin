"""NEWS2-style early-warning scoring.

Implements the Royal College of Physicians NEWS2 parameter bands
(https://www.rcp.ac.uk/file/9436/download) as an auditable table.

This is a NEWS2-*style* decision-support score, not a certified NEWS2
implementation and not a medical device.

Bands use a low-inclusive / high-exclusive convention: a value matches the
first band where ``low <= value < high``. Integer chart bands are expressed
as continuous ranges so fractional wearable readings still score sensibly
(e.g. RR 20.4 falls in the 12-20 band, RR 20.6 in the 21-24 band).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

INF = float("inf")


class Band(NamedTuple):
    low: float
    high: float
    score: int
    label: str


class Tier(str, Enum):
    """PS-03 alert levels."""

    NORMAL = "NORMAL"
    WATCH = "WATCH"
    REVIEW = "REVIEW"
    URGENT = "URGENT"


class Consciousness(str, Enum):
    ALERT = "A"
    CONFUSION = "C"
    VOICE = "V"
    PAIN = "P"
    UNRESPONSIVE = "U"


RESPIRATORY_RATE: tuple[Band, ...] = (
    Band(-INF, 9, 3, "≤8"),
    Band(9, 12, 1, "9–11"),
    Band(12, 21, 0, "12–20"),
    Band(21, 25, 2, "21–24"),
    Band(25, INF, 3, "≥25"),
)

SPO2_SCALE_1: tuple[Band, ...] = (
    Band(-INF, 92, 3, "≤91"),
    Band(92, 94, 2, "92–93"),
    Band(94, 96, 1, "94–95"),
    Band(96, INF, 0, "≥96"),
)

# Scale 2 targets 88-92% for documented hypercapnic respiratory failure.
SPO2_SCALE_2: tuple[Band, ...] = (
    Band(-INF, 84, 3, "≤83"),
    Band(84, 86, 2, "84–85"),
    Band(86, 88, 1, "86–87"),
    Band(88, 93, 0, "88–92 (target)"),
    Band(93, 95, 1, "93–94"),
    Band(95, 97, 2, "95–96"),
    Band(97, INF, 3, "≥97"),
)

TEMPERATURE: tuple[Band, ...] = (
    Band(-INF, 35.1, 3, "≤35.0"),
    Band(35.1, 36.1, 1, "35.1–36.0"),
    Band(36.1, 38.1, 0, "36.1–38.0"),
    Band(38.1, 39.1, 1, "38.1–39.0"),
    Band(39.1, INF, 2, "≥39.1"),
)

SYSTOLIC_BP: tuple[Band, ...] = (
    Band(-INF, 91, 3, "≤90"),
    Band(91, 101, 2, "91–100"),
    Band(101, 111, 1, "101–110"),
    Band(111, 220, 0, "111–219"),
    Band(220, INF, 3, "≥220"),
)

PULSE: tuple[Band, ...] = (
    Band(-INF, 41, 3, "≤40"),
    Band(41, 51, 1, "41–50"),
    Band(51, 91, 0, "51–90"),
    Band(91, 111, 1, "91–110"),
    Band(111, 131, 2, "111–130"),
    Band(131, INF, 3, "≥131"),
)

PARAMETER_LABELS: dict[str, str] = {
    "respiratory_rate": "Respiratory rate",
    "spo2": "SpO₂",
    "temperature": "Temperature",
    "systolic_bp": "Systolic BP",
    "heart_rate": "Pulse",
    "consciousness": "Consciousness",
    "supplemental_oxygen": "Supplemental oxygen",
}

PARAMETER_UNITS: dict[str, str] = {
    "respiratory_rate": "br/min",
    "spo2": "%",
    "temperature": "°C",
    "systolic_bp": "mmHg",
    "heart_rate": "bpm",
}


@dataclass(frozen=True)
class News2Input:
    """Vitals for one scoring pass.

    Only the four PS-03 mandated vitals are required to be present; BP,
    consciousness and oxygen status are scored when available. Missing
    parameters are reported, never imputed.
    """

    respiratory_rate: float | None = None
    spo2: float | None = None
    temperature: float | None = None
    heart_rate: float | None = None
    systolic_bp: float | None = None
    consciousness: Consciousness = Consciousness.ALERT
    supplemental_oxygen: bool = False
    spo2_scale: int = 1


@dataclass(frozen=True)
class ParameterScore:
    parameter: str
    label: str
    value: float | str | None
    unit: str
    score: int
    band: str
    scale: int | None = None

    @property
    def is_three(self) -> bool:
        return self.score == 3


@dataclass(frozen=True)
class News2Result:
    total: int
    tier: Tier
    risk_level: str
    parameters: tuple[ParameterScore, ...]
    missing: tuple[str, ...]
    spo2_scale: int
    single_parameter_three: bool
    complete: bool

    @property
    def scoring_parameters(self) -> tuple[ParameterScore, ...]:
        return tuple(p for p in self.parameters if p.score > 0)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "tier": self.tier.value,
            "risk_level": self.risk_level,
            "spo2_scale": self.spo2_scale,
            "single_parameter_three": self.single_parameter_three,
            "complete": self.complete,
            "missing": list(self.missing),
            "parameters": [
                {
                    "parameter": p.parameter,
                    "label": p.label,
                    "value": p.value,
                    "unit": p.unit,
                    "score": p.score,
                    "band": p.band,
                    "scale": p.scale,
                }
                for p in self.parameters
            ],
        }


def _match(bands: tuple[Band, ...], value: float) -> Band:
    for band in bands:
        if band.low <= value < band.high:
            return band
    raise ValueError(f"value {value} matched no band")


def _score_numeric(
    parameter: str, value: float | None, bands: tuple[Band, ...], scale: int | None = None
) -> ParameterScore | None:
    if value is None:
        return None
    band = _match(bands, float(value))
    return ParameterScore(
        parameter=parameter,
        label=PARAMETER_LABELS[parameter],
        value=round(float(value), 2),
        unit=PARAMETER_UNITS.get(parameter, ""),
        score=band.score,
        band=band.label,
        scale=scale,
    )


def tier_for(total: int, single_three: bool) -> tuple[Tier, str]:
    """Map a NEWS2 aggregate to the PS-03 Watch / Review / Urgent tiers.

    A single parameter scoring 3 is escalated to at least Watch even when the
    aggregate is low, per the NEWS2 single-parameter rule.
    """
    if total >= 7:
        return Tier.URGENT, "High clinical risk"
    if total >= 5:
        return Tier.REVIEW, "Medium clinical risk"
    if total >= 1 or single_three:
        return Tier.WATCH, "Low-medium clinical risk"
    return Tier.NORMAL, "Low clinical risk"


def score_news2(vitals: News2Input) -> News2Result:
    """Score one set of vitals. Deterministic: no model, no randomness."""
    scores: list[ParameterScore] = []
    missing: list[str] = []

    for parameter, value, bands in (
        ("respiratory_rate", vitals.respiratory_rate, RESPIRATORY_RATE),
        ("temperature", vitals.temperature, TEMPERATURE),
        ("heart_rate", vitals.heart_rate, PULSE),
        ("systolic_bp", vitals.systolic_bp, SYSTOLIC_BP),
    ):
        result = _score_numeric(parameter, value, bands)
        if result is None:
            missing.append(parameter)
        else:
            scores.append(result)

    if vitals.spo2 is None:
        missing.append("spo2")
    else:
        scale = 2 if vitals.spo2_scale == 2 else 1
        bands = SPO2_SCALE_2 if scale == 2 else SPO2_SCALE_1
        spo2_score = _score_numeric("spo2", vitals.spo2, bands, scale=scale)
        assert spo2_score is not None
        # Above the 88-92% target, Scale 2 only scores when breathing air:
        # a hypercapnic patient on oxygen with SpO2 >92% scores 0.
        if scale == 2 and vitals.spo2 > 92 and vitals.supplemental_oxygen:
            spo2_score = ParameterScore(
                parameter="spo2",
                label=PARAMETER_LABELS["spo2"],
                value=round(float(vitals.spo2), 2),
                unit="%",
                score=0,
                band=">92 on oxygen (Scale 2)",
                scale=2,
            )
        scores.append(spo2_score)

    scores.append(
        ParameterScore(
            parameter="consciousness",
            label=PARAMETER_LABELS["consciousness"],
            value=vitals.consciousness.value,
            unit="AVPU",
            score=0 if vitals.consciousness is Consciousness.ALERT else 3,
            band="Alert" if vitals.consciousness is Consciousness.ALERT else "New confusion / V / P / U",
        )
    )
    scores.append(
        ParameterScore(
            parameter="supplemental_oxygen",
            label=PARAMETER_LABELS["supplemental_oxygen"],
            value="Yes" if vitals.supplemental_oxygen else "No",
            unit="",
            score=2 if vitals.supplemental_oxygen else 0,
            band="Supplemental O₂" if vitals.supplemental_oxygen else "Air",
        )
    )

    total = sum(p.score for p in scores)
    single_three = any(p.is_three for p in scores)
    tier, risk = tier_for(total, single_three)
    # BP, consciousness and oxygen are optional in PS-03; the four mandated
    # vitals determine whether the score is complete for this project.
    mandated_missing = tuple(m for m in missing if m != "systolic_bp")

    return News2Result(
        total=total,
        tier=tier,
        risk_level=risk,
        parameters=tuple(scores),
        missing=mandated_missing,
        spo2_scale=2 if vitals.spo2_scale == 2 else 1,
        single_parameter_three=single_three,
        complete=not mandated_missing,
    )