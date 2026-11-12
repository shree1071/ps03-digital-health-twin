"""Unified Points & Risk Score system backed by the trained multi-class XGBoost model
and NEWS2 clinical point bands, with an activity-aware false-alarm guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from clinical.news2 import News2Input, score_news2

DEFAULT_BUNDLE_PATH = Path(__file__).resolve().parent.parent / "models" / "xgboost_risk_bundle.joblib"


@dataclass(frozen=True)
class RiskAssessment:
    predicted_category: str
    risk_score: float  # 0.0 to 100.0
    points: int  # NEWS2-style clinical points (0 to 20)
    points_breakdown: dict[str, int]
    class_probabilities: dict[str, float]
    false_alarm_suppressed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicted_category": self.predicted_category,
            "risk_score": self.risk_score,
            "points": self.points,
            "points_breakdown": self.points_breakdown,
            "class_probabilities": self.class_probabilities,
            "false_alarm_suppressed": self.false_alarm_suppressed,
            "reason": self.reason,
        }


class RiskScorer:
    """Scores vital-sign readings using XGBoost probabilities + NEWS2 points."""

    def __init__(self, bundle_path: Path = DEFAULT_BUNDLE_PATH) -> None:
        bundle = joblib.load(bundle_path)
        self.model = bundle["model"]
        self.feature_cols: list[str] = bundle["feature_cols"]
        self.source_types: list[str] = bundle["source_types"]
        self.tier_names: list[str] = bundle["tier_names"]
        self.tier_weights = np.array(bundle["tier_weights_for_risk_score"], dtype=float)

    def score(
        self,
        *,
        heart_rate: float | None = None,
        spo2: float | None = None,
        temperature: float | None = None,
        respiratory_rate: float | None = None,
        systolic_bp: float | None = None,
        steps: float | None = 0.0,
        activity_intensity: float | None = 0.0,
        is_asleep: bool = False,
        age_years: float | None = None,
        sex_male: float | None = None,
        spo2_scale: int = 1,
        source_type: str = "twin_db",
        hr_roll_mean_10: float | None = None,
        hr_roll_std_10: float | None = None,
        spo2_roll_mean_10: float | None = None,
        rr_roll_mean_10: float | None = None,
        temp_roll_mean_10: float | None = None,
    ) -> RiskAssessment:
        # 1. Compute NEWS2 clinical points & per-parameter breakdown
        news2 = score_news2(
            News2Input(
                respiratory_rate=respiratory_rate,
                spo2=spo2,
                temperature=temperature,
                systolic_bp=systolic_bp,
                heart_rate=heart_rate,
                spo2_scale=spo2_scale,
            )
        )
        breakdown = {p.parameter: p.score for p in news2.parameters}
        raw_points = news2.total

        # 2. Activity-aware false-alarm guard:
        # Normal exercise raises HR and RR; if SpO2 and temperature are normal,
        # suppress exercise-induced HR/RR points so we don't trigger false alarms.
        is_active = (activity_intensity is not None and activity_intensity >= 0.25) or (
            steps is not None and steps >= 20
        )
        spo2_ok = spo2 is None or spo2 >= 94.0
        temp_ok = temperature is None or (36.1 <= temperature <= 38.0)
        false_alarm_suppressed = False
        effective_points = raw_points
        if is_active and spo2_ok and temp_ok and not is_asleep:
            exercise_pts = breakdown.get("heart_rate", 0) + breakdown.get("respiratory_rate", 0)
            if exercise_pts > 0:
                effective_points = max(0, raw_points - exercise_pts)
                false_alarm_suppressed = True

        # 3. Build feature row for XGBoost
        hr_mean = hr_roll_mean_10 if hr_roll_mean_10 is not None else heart_rate
        hr_delta = (heart_rate - hr_mean) if (heart_rate is not None and hr_mean is not None) else np.nan
        row: dict[str, float] = {
            "heart_rate": _nan(heart_rate),
            "spo2": _nan(spo2),
            "temperature": _nan(temperature),
            "respiratory_rate": _nan(respiratory_rate),
            "systolic_bp": _nan(systolic_bp),
            "steps": _nan(steps),
            "activity_intensity": _nan(activity_intensity),
            "is_asleep": float(bool(is_asleep)),
            "age_years": _nan(age_years),
            "sex_male": _nan(sex_male),
            "spo2_scale_2": 1.0 if spo2_scale == 2 else 0.0,
            "hr_roll_mean_10": _nan(hr_mean),
            "hr_roll_std_10": _nan(hr_roll_std_10),
            "hr_delta_roll": _nan(hr_delta),
            "spo2_roll_mean_10": _nan(spo2_roll_mean_10 if spo2_roll_mean_10 is not None else spo2),
            "rr_roll_mean_10": _nan(rr_roll_mean_10 if rr_roll_mean_10 is not None else respiratory_rate),
            "temp_roll_mean_10": _nan(temp_roll_mean_10 if temp_roll_mean_10 is not None else temperature),
            "hr_diff_1": 0.0,
            "spo2_diff_1": 0.0,
            "rr_diff_1": 0.0,
            "hr_per_activity": (
                float(heart_rate) / (1.0 + 3.0 * float(activity_intensity or 0.0))
                if heart_rate is not None
                else np.nan
            ),
            "shock_index": (
                float(heart_rate) / float(systolic_bp)
                if (heart_rate is not None and systolic_bp is not None and systolic_bp > 0)
                else np.nan
            ),
            "hr_pct_max": (
                float(heart_rate) / (220.0 - float(age_years if age_years is not None else 40.0))
                if heart_rate is not None
                else np.nan
            ),
            "temp_abs_dev": abs(float(temperature) - 37.0) if temperature is not None else np.nan,
        }
        for st in self.source_types:
            row[f"src_{st}"] = 1.0 if st == source_type else 0.0

        X = pd.DataFrame([row])[self.feature_cols]
        probs = self.model.predict_proba(X)[0]
        class_probs = {name: round(float(p), 4) for name, p in zip(self.tier_names, probs)}
        risk_score = round(float(np.dot(probs, self.tier_weights)), 1)
        pred_idx = int(np.argmax(probs))
        pred_cat = self.tier_names[pred_idx]

        # Enforce false-alarm guard on final category & risk score during clean exercise,
        # and clinical point escalation so severe multi-vital deterioration is never under-triaged.
        if false_alarm_suppressed and pred_cat != "NORMAL":
            pred_cat = "NORMAL"
            risk_score = min(risk_score, 15.0)
        elif not false_alarm_suppressed:
            if effective_points >= 7:
                pred_cat = "URGENT"
                risk_score = max(risk_score, min(100.0, round(65.0 + 2.5 * effective_points, 1)))
            elif effective_points >= 5 and pred_cat in ("NORMAL", "WATCH"):
                pred_cat = "REVIEW"
                risk_score = max(risk_score, 55.0)

        if false_alarm_suppressed:
            reason = "Elevated HR/RR explained by active exercise; false alarm suppressed."
        elif pred_cat == "NORMAL":
            reason = "Vitals and activity within expected personal/clinical range."
        else:
            scoring = ", ".join(f"{k}=+{v}pt" for k, v in breakdown.items() if v > 0)
            reason = f"{pred_cat} risk (score {risk_score}/100, {effective_points} pts: {scoring or 'pattern deviation'})."

        return RiskAssessment(
            predicted_category=pred_cat,
            risk_score=risk_score,
            points=effective_points,
            points_breakdown=breakdown,
            class_probabilities=class_probs,
            false_alarm_suppressed=false_alarm_suppressed,
            reason=reason,
        )


def _nan(v: float | None) -> float:
    return np.nan if v is None else float(v)


if __name__ == "__main__":
    scorer = RiskScorer()
    scenarios = [
        ("Normal at rest", dict(heart_rate=68, spo2=98, temperature=36.7, respiratory_rate=14, steps=0, activity_intensity=0.03)),
        ("Exercise (no false alarm)", dict(heart_rate=138, spo2=97.2, temperature=37.0, respiratory_rate=23, steps=110, activity_intensity=0.75)),
        ("Mild Watch (resting tachy)", dict(heart_rate=98, spo2=95.5, temperature=37.3, respiratory_rate=19, steps=0, activity_intensity=0.02)),
        ("Urgent Deterioration", dict(heart_rate=134, spo2=89.0, temperature=39.2, respiratory_rate=27, steps=0, activity_intensity=0.0)),
    ]
    for label, kwargs in scenarios:
        res = scorer.score(**kwargs)
        print(f"{label:26s} -> category={res.predicted_category:6s} risk_score={res.risk_score:5.1f}/100 points={res.points:2d} | {res.reason}")
