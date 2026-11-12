"""XGBoost clinical deterioration risk inference alongside rule-based NEWS2."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BUNDLE_PATH = ROOT / "models" / "xgboost_risk_bundle.joblib"

_BUNDLE: dict[str, Any] | None = None


def _get_bundle() -> dict[str, Any] | None:
    global _BUNDLE
    if _BUNDLE is None and BUNDLE_PATH.exists():
        _BUNDLE = joblib.load(BUNDLE_PATH)
    return _BUNDLE


def _mean(vals: list[float]) -> float:
    return float(sum(vals) / len(vals)) if vals else float("nan")


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return float("nan")
    m = _mean(vals)
    return float(math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1)))


def _news2_to_risk_score(tier: str, news2_total: int | None) -> float:
    """Map NEWS2 score/tier onto the same 0-100 risk scale as XGBoost."""
    total = float(news2_total or 0)
    if tier == "NORMAL":
        return round(min(18.0, total * 5.0), 1)
    if tier == "WATCH":
        return round(min(42.0, 20.0 + total * 4.0), 1)
    if tier == "REVIEW":
        return round(min(62.0, 44.0 + total * 3.0), 1)
    return round(min(88.0, 56.0 + total * 2.0), 1)


def compare_with_xgboost(
    *,
    patient_state: Any,
    vitals: dict[str, float | None],
    rule_tier: str,
    news2_total: int | None,
    suppressed: bool = False,
) -> dict[str, Any]:
    """Run the trained XGBoost risk bundle and return a side-by-side comparison."""
    rule_score = _news2_to_risk_score(rule_tier, news2_total)
    without_xgb = {
        "engine": "NEWS2 + Personalized Baseline (Without XGBoost)",
        "tier": rule_tier,
        "risk_score": rule_score,
        "news2_total": news2_total,
        "suppressed": suppressed,
    }

    bundle = _get_bundle()
    if bundle is None:
        return {
            "available": False,
            "without_xgboost": without_xgb,
            "with_xgboost": None,
            "agreement": "UNAVAILABLE",
            "score_delta": None,
            "should_use_both": (
                "XGBoost bundle not found on disk; running deterministic NEWS2 + rolling baseline only."
            ),
        }

    model = bundle["model"]
    feature_cols: list[str] = bundle["feature_cols"]
    tier_names: list[str] = bundle["tier_names"]
    weights = np.array(
        bundle.get("tier_weights_for_risk_score", [0.0, 25.0, 60.0, 100.0]),
        dtype=float,
    )

    history = list(getattr(patient_state, "readings", []) or [])[-9:]
    hr = vitals.get("heart_rate")
    spo2 = vitals.get("spo2")
    temp = vitals.get("temperature")
    rr = vitals.get("respiratory_rate")
    sbp = vitals.get("systolic_bp")
    activity = vitals.get("activity_intensity") or 0.0

    hr_hist = [float(r.heart_rate) for r in history if getattr(r, "heart_rate", None) is not None]
    spo2_hist = [float(r.spo2) for r in history if getattr(r, "spo2", None) is not None]
    rr_hist = [float(r.respiratory_rate) for r in history if getattr(r, "respiratory_rate", None) is not None]
    temp_hist = [float(r.temperature) for r in history if getattr(r, "temperature", None) is not None]

    if hr is not None:
        hr_hist.append(float(hr))
    if spo2 is not None:
        spo2_hist.append(float(spo2))
    if rr is not None:
        rr_hist.append(float(rr))
    if temp is not None:
        temp_hist.append(float(temp))

    hr_roll_mean = _mean(hr_hist)
    hr_roll_std = _std(hr_hist)
    hr_delta_roll = (float(hr) - hr_roll_mean) if (hr is not None and not math.isnan(hr_roll_mean)) else float("nan")

    hr_diff_1 = (float(hr) - hr_hist[-2]) if (hr is not None and len(hr_hist) >= 2) else float("nan")
    spo2_diff_1 = (float(spo2) - spo2_hist[-2]) if (spo2 is not None and len(spo2_hist) >= 2) else float("nan")
    rr_diff_1 = (float(rr) - rr_hist[-2]) if (rr is not None and len(rr_hist) >= 2) else float("nan")

    profile = patient_state.profile
    age = float(getattr(profile, "age_years", 65) or 65)
    sex_male = 1.0 if str(getattr(profile, "sex", "")).lower() == "male" else 0.0
    spo2_scale_2 = 1.0 if int(getattr(profile, "spo2_scale", 1) or 1) == 2 else 0.0

    row: dict[str, float] = {
        "heart_rate": float(hr) if hr is not None else float("nan"),
        "spo2": float(spo2) if spo2 is not None else float("nan"),
        "temperature": float(temp) if temp is not None else float("nan"),
        "respiratory_rate": float(rr) if rr is not None else float("nan"),
        "systolic_bp": float(sbp) if sbp is not None else float("nan"),
        "steps": float("nan"),
        "activity_intensity": float(activity),
        "is_asleep": 0.0,
        "age_years": age,
        "sex_male": sex_male,
        "spo2_scale_2": spo2_scale_2,
        "hr_roll_mean_10": hr_roll_mean,
        "hr_roll_std_10": hr_roll_std,
        "hr_delta_roll": hr_delta_roll,
        "spo2_roll_mean_10": _mean(spo2_hist),
        "rr_roll_mean_10": _mean(rr_hist),
        "temp_roll_mean_10": _mean(temp_hist),
        "hr_diff_1": hr_diff_1,
        "spo2_diff_1": spo2_diff_1,
        "rr_diff_1": rr_diff_1,
        "hr_per_activity": (float(hr) / (1.0 + 3.0 * float(activity))) if hr is not None else float("nan"),
        "shock_index": (float(hr) / float(sbp)) if (hr is not None and sbp) else float("nan"),
        "hr_pct_max": (float(hr) / max(1.0, 220.0 - age)) if hr is not None else float("nan"),
        "temp_abs_dev": abs(float(temp) - 37.0) if temp is not None else float("nan"),
    }

    X = pd.DataFrame([{col: row.get(col, float("nan")) for col in feature_cols}])
    probs = model.predict_proba(X)[0]
    pred_idx = int(np.argmax(probs))
    xgb_tier = tier_names[pred_idx]
    xgb_confidence = round(float(probs[pred_idx]), 3)
    xgb_score = round(float(np.dot(probs, weights)), 1)

    delta = round(abs(xgb_score - rule_score), 1)
    if xgb_tier == rule_tier:
        agreement = "ALIGNED"
    elif delta <= 20.0:
        agreement = "NEARLY_SIMILAR"
    else:
        agreement = "DIVERGENT"

    return {
        "available": True,
        "without_xgboost": without_xgb,
        "with_xgboost": {
            "engine": "Trained XGBoost Classifier (40.5k rows, AUC 0.998)",
            "tier": xgb_tier,
            "risk_score": xgb_score,
            "confidence": xgb_confidence,
            "probabilities": {
                name: round(float(probs[i]), 3) for i, name in enumerate(tier_names)
            },
        },
        "agreement": agreement,
        "score_delta": delta,
        "should_use_both": (
            "Yes — use both together as a hybrid dual-engine: NEWS2 + personalized baseline "
            "guarantees transparent, guideline-auditable clinical rules (and artifact suppression), "
            "while XGBoost adds learned multivariate trend confidence across HR, SpO₂, RR, and temperature."
        ),
    }
