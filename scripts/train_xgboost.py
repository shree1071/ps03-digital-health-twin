"""Train a multi-class XGBoost clinical risk & anomaly model across all workspace datasets.

Categories (4 classes):
  0: NORMAL  (healthy / expected for current activity, including normal exercise)
  1: WATCH   (mild / single-parameter elevation or early anomaly)
  2: REVIEW  (moderate multi-parameter risk, NEWS2 5-6 or persistent anomaly)
  3: URGENT  (high clinical risk, NEWS2 >= 7)

Unifies all given data sources:
  1. data/twin_v2.db                          (digital-twin SQLite observations)
  2. data/demo_recording.jsonl                (labeled simulator recording)
  3. data/bidmc/bidmc_*_Numerics.csv          (BIDMC ICU patient numerics)
  4. data/sample_real/fitbit_user_5553957443/ (Fitbit HR, steps, sleep)
  5. scripts/sample_wearable_export.csv       (wearable CSV export)
  6. data/synthea/output/fhir/*.json          (Synthea FHIR patient encounters)

Also provides a Points & Risk Score system (0-100 XGBoost risk score + NEWS2 clinical
points + activity-aware false-alarm guard) via digital_twin/risk_scorer.py.

Usage:
    python -u scripts/train_xgboost.py
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clinical.news2 import News2Input, Tier, score_news2  # noqa: E402
from simulator.generator import SubjectProfile, generate_history  # noqa: E402

TIER_NAMES = ["NORMAL", "WATCH", "REVIEW", "URGENT"]
TIER_TO_INT = {name: idx for idx, name in enumerate(TIER_NAMES)}

FEATURE_COLS_BASE = [
    "heart_rate",
    "spo2",
    "temperature",
    "respiratory_rate",
    "systolic_bp",
    "steps",
    "activity_intensity",
    "is_asleep",
    "age_years",
    "sex_male",
    "spo2_scale_2",
]

TEMPORAL_COLS = [
    "hr_roll_mean_10",
    "hr_roll_std_10",
    "hr_delta_roll",
    "spo2_roll_mean_10",
    "rr_roll_mean_10",
    "temp_roll_mean_10",
    "hr_diff_1",
    "spo2_diff_1",
    "rr_diff_1",
    "hr_per_activity",
    "shock_index",
    "hr_pct_max",
    "temp_abs_dev",
]

SOURCE_TYPES = ["twin_db", "demo", "bidmc", "fitbit", "wearable_csv", "synthea", "simulator"]


def _news2_tier_int(
    *,
    hr: float | None = None,
    spo2: float | None = None,
    temp: float | None = None,
    rr: float | None = None,
    sbp: float | None = None,
    spo2_scale: int = 1,
) -> int:
    """Return NEWS2 tier index: 0=NORMAL, 1=WATCH, 2=REVIEW, 3=URGENT."""
    res = score_news2(
        News2Input(
            respiratory_rate=rr if pd.notna(rr) else None,
            spo2=spo2 if pd.notna(spo2) else None,
            temperature=temp if pd.notna(temp) else None,
            systolic_bp=sbp if pd.notna(sbp) else None,
            heart_rate=hr if pd.notna(hr) else None,
            spo2_scale=spo2_scale,
        )
    )
    return TIER_TO_INT[res.tier.value]


def load_twin_db(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    con = sqlite3.connect(str(db_path))
    try:
        obs = pd.read_sql_query("SELECT * FROM observations ORDER BY twin_id, timestamp", con)
        subj = pd.read_sql_query("SELECT id, age_years FROM subjects", con)
        twins = pd.read_sql_query("SELECT id AS twin_id, subject_id FROM twins", con)
    finally:
        con.close()

    if obs.empty:
        return pd.DataFrame()

    if not twins.empty and not subj.empty:
        meta = twins.merge(subj, left_on="subject_id", right_on="id", how="left")
        obs = obs.merge(meta[["twin_id", "age_years"]], on="twin_id", how="left")
    else:
        obs["age_years"] = np.nan

    labels = []
    for row in obs.itertuples(index=False):
        has_anom = bool(row.anomalous_metrics)
        has_watch = bool(row.watch_metrics)
        if row.state in ("STABLE", "HIGH_ACTIVITY", "ACTIVE", "RECOVERY") and not has_anom and not has_watch:
            labels.append(0)
        else:
            tier = _news2_tier_int(
                hr=row.heart_rate,
                spo2=row.spo2,
                temp=row.temperature,
                rr=row.respiratory_rate,
            )
            if has_anom:
                tier = max(tier, 2)
            elif has_watch or row.state == "ELEVATED":
                tier = max(tier, 1)
            labels.append(tier)

    return pd.DataFrame(
        {
            "subject_id": "twin_" + obs["twin_id"].astype(str),
            "source_type": "twin_db",
            "timestamp": pd.to_datetime(obs["timestamp"], errors="coerce"),
            "heart_rate": pd.to_numeric(obs["heart_rate"], errors="coerce"),
            "spo2": pd.to_numeric(obs["spo2"], errors="coerce"),
            "temperature": pd.to_numeric(obs["temperature"], errors="coerce"),
            "respiratory_rate": pd.to_numeric(obs["respiratory_rate"], errors="coerce"),
            "systolic_bp": np.nan,
            "steps": pd.to_numeric(obs["steps"], errors="coerce"),
            "activity_intensity": pd.to_numeric(obs["activity_intensity"], errors="coerce"),
            "is_asleep": pd.to_numeric(obs["is_asleep"], errors="coerce").fillna(0.0),
            "age_years": pd.to_numeric(obs["age_years"], errors="coerce"),
            "sex_male": np.nan,
            "spo2_scale_2": 0.0,
            "label": labels,
        }
    )


def load_demo_recording(jsonl_path: Path) -> pd.DataFrame:
    if not jsonl_path.exists():
        return pd.DataFrame()
    rows = []
    t0 = pd.Timestamp("2026-01-01T00:00:00Z")
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            r = rec["reading"]
            t0 = t0 + pd.Timedelta(seconds=float(rec.get("dt_s", 15.0)))
            scenario = rec.get("scenario", "NORMAL")
            if scenario == "ANOMALY":
                label = max(
                    1,
                    _news2_tier_int(
                        hr=r.get("heart_rate"),
                        spo2=r.get("spo2"),
                        temp=r.get("temperature"),
                        rr=r.get("respiratory_rate"),
                    ),
                )
            else:
                # NORMAL, EXERCISE, RECOVERY are non-anomalous (prevents exercise false positives)
                label = 0
            rows.append(
                {
                    "subject_id": "demo_subject",
                    "source_type": "demo",
                    "timestamp": t0,
                    "heart_rate": r.get("heart_rate"),
                    "spo2": r.get("spo2"),
                    "temperature": r.get("temperature"),
                    "respiratory_rate": r.get("respiratory_rate"),
                    "systolic_bp": np.nan,
                    "steps": r.get("steps"),
                    "activity_intensity": r.get("activity_intensity"),
                    "is_asleep": float(bool(r.get("is_asleep", False))),
                    "age_years": np.nan,
                    "sex_male": np.nan,
                    "spo2_scale_2": 0.0,
                    "label": label,
                }
            )
    return pd.DataFrame(rows)


def load_bidmc(bidmc_dir: Path) -> pd.DataFrame:
    files = sorted(bidmc_dir.glob("bidmc_*_Numerics.csv"))
    frames = []
    for f in files:
        raw = pd.read_csv(f)
        raw.columns = [c.strip() for c in raw.columns]
        hr = pd.to_numeric(raw.get("HR"), errors="coerce")
        rr = pd.to_numeric(raw.get("RESP"), errors="coerce")
        spo2 = pd.to_numeric(raw.get("SpO2"), errors="coerce")
        time_s = pd.to_numeric(raw.get("Time [s]"), errors="coerce").fillna(0.0)

        labels = [_news2_tier_int(hr=h, spo2=s, rr=r) for h, s, r in zip(hr, spo2, rr)]
        frames.append(
            pd.DataFrame(
                {
                    "subject_id": f.stem.replace("_Numerics", ""),
                    "source_type": "bidmc",
                    "timestamp": pd.Timestamp("2020-01-01T00:00:00Z") + pd.to_timedelta(time_s, unit="s"),
                    "heart_rate": hr,
                    "spo2": spo2,
                    "temperature": np.nan,
                    "respiratory_rate": rr,
                    "systolic_bp": np.nan,
                    "steps": 0.0,
                    "activity_intensity": 0.0,
                    "is_asleep": 0.0,
                    "age_years": np.nan,
                    "sex_male": np.nan,
                    "spo2_scale_2": 0.0,
                    "label": labels,
                }
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_fitbit(fitbit_dir: Path) -> pd.DataFrame:
    hr_path = fitbit_dir / "heartrate_seconds_merged.csv"
    stp_path = fitbit_dir / "minuteStepsNarrow_merged.csv"
    slp_path = fitbit_dir / "minuteSleep_merged.csv"
    if not hr_path.exists():
        return pd.DataFrame()

    hr = pd.read_csv(hr_path)
    hr["ts"] = pd.to_datetime(hr["Time"], format="%m/%d/%Y %I:%M:%S %p", errors="coerce").dt.floor("min")
    hr_min = hr.groupby(["Id", "ts"], as_index=False)["Value"].mean().rename(columns={"Value": "heart_rate"})

    if stp_path.exists():
        stp = pd.read_csv(stp_path)
        stp["ts"] = pd.to_datetime(stp["ActivityMinute"], format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
        hr_min = hr_min.merge(stp[["Id", "ts", "Steps"]], on=["Id", "ts"], how="left")
    else:
        hr_min["Steps"] = np.nan

    if slp_path.exists():
        slp = pd.read_csv(slp_path)
        slp["ts"] = pd.to_datetime(slp["date"], format="%m/%d/%Y %I:%M:%S %p", errors="coerce").dt.floor("min")
        slp_min = slp[["Id", "ts"]].drop_duplicates().assign(is_asleep=1.0)
        hr_min = hr_min.merge(slp_min, on=["Id", "ts"], how="left")
        hr_min["is_asleep"] = hr_min["is_asleep"].fillna(0.0)
    else:
        hr_min["is_asleep"] = 0.0

    steps = pd.to_numeric(hr_min["Steps"], errors="coerce")
    intensity = (steps.fillna(0.0) / 130.0).clip(0.0, 1.0)

    # Activity-aware label: only score resting/asleep HR with NEWS2 so normal
    # exercise tachycardia (steps > 0) is not falsely labeled as clinical risk.
    labels = [
        _news2_tier_int(hr=h) if (pd.isna(st) or st == 0) else 0
        for h, st in zip(hr_min["heart_rate"], steps)
    ]

    return pd.DataFrame(
        {
            "subject_id": "fitbit_" + hr_min["Id"].astype(str),
            "source_type": "fitbit",
            "timestamp": hr_min["ts"],
            "heart_rate": hr_min["heart_rate"],
            "spo2": np.nan,
            "temperature": np.nan,
            "respiratory_rate": np.nan,
            "systolic_bp": np.nan,
            "steps": steps,
            "activity_intensity": intensity,
            "is_asleep": hr_min["is_asleep"],
            "age_years": np.nan,
            "sex_male": np.nan,
            "spo2_scale_2": 0.0,
            "label": labels,
        }
    )


def load_wearable_csv(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        return pd.DataFrame()
    raw = pd.read_csv(csv_path)
    spo2 = pd.to_numeric(raw.get("oxygen_saturation"), errors="coerce")
    if spo2.max() <= 1.05:
        spo2 = spo2 * 100.0
    temp_f = pd.to_numeric(raw.get("temp_f"), errors="coerce")
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    hr = pd.to_numeric(raw.get("bpm"), errors="coerce")
    rr = pd.to_numeric(raw.get("rr"), errors="coerce")
    steps = pd.to_numeric(raw.get("steps"), errors="coerce")
    intensity = pd.to_numeric(raw.get("intensity"), errors="coerce")
    asleep = raw.get("asleep").astype(str).str.lower().isin(["true", "1", "yes"]).astype(float)

    labels = [
        _news2_tier_int(hr=h, spo2=s, temp=t, rr=r) if (pd.isna(inten) or inten < 0.2) else 0
        for h, s, t, r, inten in zip(hr, spo2, temp_c, rr, intensity)
    ]

    return pd.DataFrame(
        {
            "subject_id": "wearable_export_01",
            "source_type": "wearable_csv",
            "timestamp": pd.to_datetime(raw["timestamp"], errors="coerce"),
            "heart_rate": hr,
            "spo2": spo2,
            "temperature": temp_c,
            "respiratory_rate": rr,
            "systolic_bp": np.nan,
            "steps": steps,
            "activity_intensity": intensity,
            "is_asleep": asleep,
            "age_years": np.nan,
            "sex_male": np.nan,
            "spo2_scale_2": 0.0,
            "label": labels,
        }
    )


def _extract_fhir_vital(obs: dict, enc_vitals: dict) -> None:
    codings = obs.get("code", {}).get("coding", [])
    codes = {c.get("code") for c in codings if c.get("code")}
    text = (obs.get("code", {}).get("text") or "").lower()

    val = obs.get("valueQuantity", {}).get("value")
    if val is not None:
        val = float(val)
        if "8867-4" in codes or "heart rate" in text:
            enc_vitals["heart_rate"] = val
        elif "9279-1" in codes or "respiratory rate" in text:
            enc_vitals["respiratory_rate"] = val
        elif "2708-6" in codes or "59408-5" in codes or "oxygen saturation" in text:
            enc_vitals["spo2"] = val
        elif "8310-5" in codes or "body temperature" in text:
            enc_vitals["temperature"] = val

    for comp in obs.get("component", []):
        c_codes = {c.get("code") for c in comp.get("code", {}).get("coding", [])}
        c_val = comp.get("valueQuantity", {}).get("value")
        if c_val is not None and "8480-6" in c_codes:
            enc_vitals["systolic_bp"] = float(c_val)


def _parse_single_fhir_file(fpath: str) -> list[dict]:
    ref_year = 2026
    with open(fpath, "r", encoding="utf-8") as f:
        bundle = json.load(f)

    patient_id = Path(fpath).stem
    age_years = np.nan
    sex_male = np.nan
    spo2_scale_2 = 0.0
    encounters: dict[str, dict] = {}

    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        rt = res.get("resourceType")
        if rt == "Patient":
            bdate = res.get("birthDate")
            if bdate and len(bdate) >= 4:
                age_years = float(ref_year - int(bdate[:4]))
            gender = (res.get("gender") or "").lower()
            if gender in ("male", "female"):
                sex_male = float(gender == "male")
        elif rt == "Condition":
            disp = (res.get("code", {}).get("text") or "").lower()
            if "copd" in disp or "emphysema" in disp or "hypercapni" in disp:
                spo2_scale_2 = 1.0
        elif rt == "Observation":
            enc_ref = res.get("encounter", {}).get("reference") or res.get("effectiveDateTime") or "unknown"
            ev = encounters.setdefault(
                enc_ref,
                {
                    "timestamp": res.get("effectiveDateTime"),
                    "heart_rate": np.nan,
                    "spo2": np.nan,
                    "temperature": np.nan,
                    "respiratory_rate": np.nan,
                    "systolic_bp": np.nan,
                },
            )
            _extract_fhir_vital(res, ev)

    scale = 2 if spo2_scale_2 == 1.0 else 1
    out: list[dict] = []
    for ev in encounters.values():
        has_vital = any(
            pd.notna(ev[k])
            for k in ("heart_rate", "spo2", "temperature", "respiratory_rate", "systolic_bp")
        )
        if not has_vital:
            continue
        label = _news2_tier_int(
            hr=ev["heart_rate"],
            spo2=ev["spo2"],
            temp=ev["temperature"],
            rr=ev["respiratory_rate"],
            sbp=ev["systolic_bp"],
            spo2_scale=scale,
        )
        out.append(
            {
                "subject_id": f"synthea_{patient_id}",
                "source_type": "synthea",
                "timestamp": ev["timestamp"],
                "heart_rate": ev["heart_rate"],
                "spo2": ev["spo2"],
                "temperature": ev["temperature"],
                "respiratory_rate": ev["respiratory_rate"],
                "systolic_bp": ev["systolic_bp"],
                "steps": np.nan,
                "activity_intensity": np.nan,
                "is_asleep": np.nan,
                "age_years": age_years,
                "sex_male": sex_male,
                "spo2_scale_2": spo2_scale_2,
                "label": label,
            }
        )
    return out


def load_synthea_fhir(fhir_dir: Path) -> pd.DataFrame:
    cache_path = ROOT / "models" / "synthea_vitals_cache.csv"
    if cache_path.exists():
        print("    loading cached Synthea FHIR vitals...", flush=True)
        df = pd.read_csv(cache_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
        return df

    files = sorted(glob.glob(str(fhir_dir / "*.json")))
    if not files:
        return pd.DataFrame()
    rows: list[dict] = []
    print(f"    parsing {len(files)} Synthea FHIR files (and caching)...", flush=True)
    for idx, fpath in enumerate(files, start=1):
        if idx % 70 == 0 or idx == len(files):
            print(f"      {idx}/{len(files)}...", flush=True)
        rows.extend(_parse_single_fhir_file(fpath))
    df = pd.DataFrame(rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
    return df


def load_simulator_episodes() -> pd.DataFrame:
    """Generate multi-severity physiological episodes (including REVIEW/URGENT) via simulator."""
    from datetime import datetime, timedelta

    end = datetime(2026, 11, 10, 0, 0, 0)
    profiles = [
        ("sim_young", SubjectProfile(age_years=29, hr_rest=62, rr_rest=13.5, spo2=97.8, temp=36.65), 11),
        ("sim_mid", SubjectProfile(age_years=54, hr_rest=68, rr_rest=15.0, spo2=96.8, temp=36.70), 23),
        ("sim_senior", SubjectProfile(age_years=72, hr_rest=72, rr_rest=16.0, spo2=95.8, temp=36.60), 37),
    ]
    rows: list[dict] = []
    for subj_id, prof, seed in profiles:
        start = end - timedelta(days=5)
        windows = [
            (start + timedelta(hours=10), start + timedelta(hours=12), 0.55),
            (start + timedelta(hours=32), start + timedelta(hours=34), 0.95),
            (start + timedelta(hours=58), start + timedelta(hours=60), 1.35),
            (start + timedelta(hours=84), start + timedelta(hours=86), 1.70),
            (start + timedelta(hours=104), start + timedelta(hours=106), 1.95),
        ]
        obs_list = generate_history(
            prof, end=end, days=5, interval_s=300.0, seed=seed, anomaly_windows=windows, include_planned_anomaly=False
        )
        for o in obs_list:
            in_anom = any(w0 <= o.timestamp <= w1 for w0, w1, _ in windows)
            if in_anom:
                label = max(
                    1,
                    _news2_tier_int(
                        hr=o.heart_rate, spo2=o.spo2, temp=o.temperature, rr=o.respiratory_rate
                    ),
                )
            else:
                label = 0
            rows.append(
                {
                    "subject_id": subj_id,
                    "source_type": "simulator",
                    "timestamp": pd.Timestamp(o.timestamp, tz="UTC"),
                    "heart_rate": o.heart_rate,
                    "spo2": o.spo2,
                    "temperature": o.temperature,
                    "respiratory_rate": o.respiratory_rate,
                    "systolic_bp": np.nan,
                    "steps": o.steps,
                    "activity_intensity": o.activity_intensity,
                    "is_asleep": float(o.is_asleep),
                    "age_years": prof.age_years,
                    "sex_male": np.nan,
                    "spo2_scale_2": 0.0,
                    "label": label,
                }
            )
    return pd.DataFrame(rows)


def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["subject_id", "timestamp"], kind="mergesort").reset_index(drop=True)
    grp = df.groupby("subject_id", sort=False)

    df["hr_roll_mean_10"] = grp["heart_rate"].transform(lambda s: s.rolling(10, min_periods=1).mean())
    df["hr_roll_std_10"] = grp["heart_rate"].transform(lambda s: s.rolling(10, min_periods=2).std())
    df["hr_delta_roll"] = df["heart_rate"] - df["hr_roll_mean_10"]
    df["spo2_roll_mean_10"] = grp["spo2"].transform(lambda s: s.rolling(10, min_periods=1).mean())
    df["rr_roll_mean_10"] = grp["respiratory_rate"].transform(lambda s: s.rolling(10, min_periods=1).mean())
    df["temp_roll_mean_10"] = grp["temperature"].transform(lambda s: s.rolling(10, min_periods=1).mean())

    df["hr_diff_1"] = grp["heart_rate"].diff()
    df["spo2_diff_1"] = grp["spo2"].diff()
    df["rr_diff_1"] = grp["respiratory_rate"].diff()

    df["hr_per_activity"] = df["heart_rate"] / (1.0 + 3.0 * df["activity_intensity"].fillna(0.0))
    df["shock_index"] = df["heart_rate"] / df["systolic_bp"]
    df["hr_pct_max"] = df["heart_rate"] / (220.0 - df["age_years"].fillna(40.0))
    df["temp_abs_dev"] = (df["temperature"] - 37.0).abs()

    for st in SOURCE_TYPES:
        df[f"src_{st}"] = (df["source_type"] == st).astype(float)
    return df


def split_leak_free(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Chronological 70/15/15 split per time-series subject; patient-grouped 70/15/15 for Synthea."""
    train_idx, val_idx, test_idx = [], [], []

    ts_df = df[df["source_type"] != "synthea"]
    for _, sub in ts_df.groupby("subject_id", sort=False):
        n = len(sub)
        i1 = max(1, int(0.70 * n))
        i2 = max(i1 + 1, int(0.85 * n)) if n >= 3 else i1
        train_idx.extend(sub.index[:i1])
        val_idx.extend(sub.index[i1:i2])
        test_idx.extend(sub.index[i2:])

    syn_df = df[df["source_type"] == "synthea"]
    if not syn_df.empty:
        subjects = np.array(sorted(syn_df["subject_id"].unique()))
        rng = np.random.default_rng(42)
        rng.shuffle(subjects)
        n = len(subjects)
        s1, s2 = int(0.70 * n), int(0.85 * n)
        tr_s, va_s, te_s = set(subjects[:s1]), set(subjects[s1:s2]), set(subjects[s2:])
        train_idx.extend(syn_df[syn_df["subject_id"].isin(tr_s)].index)
        val_idx.extend(syn_df[syn_df["subject_id"].isin(va_s)].index)
        test_idx.extend(syn_df[syn_df["subject_id"].isin(te_s)].index)

    return df.loc[train_idx].copy(), df.loc[val_idx].copy(), df.loc[test_idx].copy()


def evaluate_multiclass(name: str, y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    y_pred = np.argmax(y_prob, axis=1)
    present_classes = sorted(np.unique(np.concatenate([y_true, y_pred])).tolist())
    macro_f1 = float(f1_score(y_true, y_pred, average="macro", zero_division=0))
    weighted_f1 = float(f1_score(y_true, y_pred, average="weighted", zero_division=0))

    # False-positive rate on NORMAL (class 0): fraction of truly NORMAL rows flagged as >= WATCH
    normal_mask = y_true == 0
    false_alarm_rate = float((y_pred[normal_mask] > 0).mean()) if normal_mask.any() else 0.0

    # Binary risk detection (any tier >= 1 vs NORMAL)
    y_true_bin = (y_true > 0).astype(int)
    y_pred_bin = (y_pred > 0).astype(int)
    y_prob_bin = 1.0 - y_prob[:, 0]
    bin_auc = float(roc_auc_score(y_true_bin, y_prob_bin)) if len(np.unique(y_true_bin)) > 1 else None
    bin_recall = float(recall_score(y_true_bin, y_pred_bin, zero_division=0))
    bin_precision = float(precision_score(y_true_bin, y_pred_bin, zero_division=0))

    counts = {TIER_NAMES[c]: int((y_true == c).sum()) for c in range(4)}
    metrics = {
        "rows": int(len(y_true)),
        "class_counts": counts,
        "macro_f1": round(macro_f1, 4),
        "weighted_f1": round(weighted_f1, 4),
        "false_alarm_rate": round(false_alarm_rate, 4),
        "risk_roc_auc": round(bin_auc, 4) if bin_auc is not None else None,
        "risk_precision": round(bin_precision, 4),
        "risk_recall": round(bin_recall, 4),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=[0, 1, 2, 3]).tolist(),
    }
    print(
        f"[{name}] n={metrics['rows']} counts={counts} | "
        f"MacroF1={metrics['macro_f1']} WeightedF1={metrics['weighted_f1']} "
        f"RiskAUC={metrics['risk_roc_auc']} RiskP={metrics['risk_precision']} "
        f"RiskR={metrics['risk_recall']} FalseAlarmRate={metrics['false_alarm_rate']:.2%}",
        flush=True,
    )
    return metrics


def main() -> None:
    print("Loading all datasets...", flush=True)
    parts = [
        ("twin_db", load_twin_db(ROOT / "data" / "twin_v2.db")),
        ("demo", load_demo_recording(ROOT / "data" / "demo_recording.jsonl")),
        ("bidmc", load_bidmc(ROOT / "data" / "bidmc")),
        ("fitbit", load_fitbit(ROOT / "data" / "sample_real" / "fitbit_user_5553957443")),
        ("wearable_csv", load_wearable_csv(ROOT / "scripts" / "sample_wearable_export.csv")),
        ("synthea", load_synthea_fhir(ROOT / "data" / "synthea" / "output" / "fhir")),
        ("simulator", load_simulator_episodes()),
    ]

    non_empty = []
    for name, part in parts:
        if part.empty:
            print(f"  - {name}: 0 rows (skipped)", flush=True)
        else:
            dist = {TIER_NAMES[k]: int(v) for k, v in part["label"].value_counts().sort_index().items()}
            print(f"  - {name}: {len(part)} rows -> {dist}", flush=True)
            non_empty.append(part)

    full_df = pd.concat(non_empty, ignore_index=True)
    full_df["timestamp"] = pd.to_datetime(full_df["timestamp"], utc=True, errors="coerce")
    full_df = add_temporal_features(full_df)

    feature_cols = FEATURE_COLS_BASE + TEMPORAL_COLS
    train_df, val_df, test_df = split_leak_free(full_df)

    X_train, y_train = train_df[feature_cols], train_df["label"].to_numpy()
    X_val, y_val = val_df[feature_cols], val_df["label"].to_numpy()
    X_test, y_test = test_df[feature_cols], test_df["label"].to_numpy()

    # Cap balanced weights to avoid over-predicting rare classes and causing false alarms
    sample_weights = np.clip(compute_sample_weight("balanced", y_train), 0.25, 8.0)

    print(f"\nSplit sizes -> train={len(train_df)}, val={len(val_df)}, test={len(test_df)}", flush=True)
    print(
        "Train class distribution:",
        {TIER_NAMES[k]: int((y_train == k).sum()) for k in range(4)},
        flush=True,
    )

    model = XGBClassifier(
        objective="multi:softprob",
        num_class=4,
        n_estimators=75,
        max_depth=3,
        learning_rate=0.045,
        subsample=0.75,
        colsample_bytree=0.7,
        min_child_weight=15,
        gamma=1.5,
        reg_alpha=4.0,
        reg_lambda=18.0,
        eval_metric="mlogloss",
        early_stopping_rounds=15,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(
        X_train,
        y_train,
        sample_weight=sample_weights,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    print(f"\nBest iteration: {model.best_iteration}", flush=True)
    train_prob = model.predict_proba(X_train)
    val_prob = model.predict_proba(X_val)
    test_prob = model.predict_proba(X_test)

    train_acc = float((np.argmax(train_prob, axis=1) == y_train).mean())
    val_acc = float((np.argmax(val_prob, axis=1) == y_val).mean())
    test_acc = float((np.argmax(test_prob, axis=1) == y_test).mean())
    print(f"Accuracy -> TRAIN={train_acc:.4f} | VAL={val_acc:.4f} | TEST={test_acc:.4f}", flush=True)

    val_metrics = evaluate_multiclass("VAL", y_val, val_prob)
    test_metrics = evaluate_multiclass("TEST", y_test, test_prob)

    print("\nPer-source TEST breakdown:", flush=True)
    per_source = {}
    for st in SOURCE_TYPES:
        mask = test_df["source_type"] == st
        if mask.sum() == 0:
            continue
        per_source[st] = evaluate_multiclass(f"TEST/{st}", y_test[mask], test_prob[mask])

    y_pred_test = np.argmax(test_prob, axis=1)
    print("\nTest Classification Report:", flush=True)
    print(
        classification_report(
            y_test,
            y_pred_test,
            labels=[0, 1, 2, 3],
            target_names=TIER_NAMES,
            digits=4,
            zero_division=0,
        ),
        flush=True,
    )

    importances = sorted(
        zip(feature_cols, model.feature_importances_.tolist()),
        key=lambda x: -x[1],
    )
    print("Top 12 Features:", flush=True)
    for feat, imp in importances[:12]:
        print(f"  {feat:24s} {imp:.4f}", flush=True)

    out_dir = ROOT / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_json = out_dir / "xgboost_risk_model.json"
    bundle_path = out_dir / "xgboost_risk_bundle.joblib"
    report_path = out_dir / "xgboost_training_report.json"

    model.save_model(str(model_json))
    joblib.dump(
        {
            "model": model,
            "feature_cols": feature_cols,
            "source_types": SOURCE_TYPES,
            "tier_names": TIER_NAMES,
            "tier_weights_for_risk_score": [0.0, 25.0, 60.0, 100.0],
        },
        bundle_path,
    )

    report = {
        "total_rows": int(len(full_df)),
        "tier_names": TIER_NAMES,
        "dataset_counts": {name: int(len(part)) for name, part in parts if not part.empty},
        "best_iteration": int(model.best_iteration),
        "val_metrics": val_metrics,
        "test_metrics": test_metrics,
        "per_source_test": per_source,
        "top_features": [{"feature": f, "importance": round(i, 6)} for f, i in importances[:15]],
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved model to {model_json}", flush=True)
    print(f"Saved bundle to {bundle_path}", flush=True)
    print(f"Saved report to {report_path}", flush=True)


if __name__ == "__main__":
    main()
