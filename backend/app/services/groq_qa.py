"""Groq-powered clinical Q&A grounded in the logged-in user and live patient vitals."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from groq import Groq

from backend.app.services.xgb_risk import compare_with_xgboost

DEFAULT_W_ENV = Path(r"C:\Users\shree\Downloads\whatsaap\w.env")


def _groq_api_key() -> str:
    env_key = os.environ.get("GROQ_API_KEY", "").strip()
    if env_key:
        return env_key
    if DEFAULT_W_ENV.exists():
        vals = dotenv_values(DEFAULT_W_ENV)
        k = (vals.get("GROQ_API_KEY") or "").strip()
        if k:
            return k
    return ""


def _resolve_patient(service: Any, question: str, active_session: dict[str, Any]) -> Any | None:
    """Resolve which patient the question is about based on login or mentioned name."""
    patients = list(service.patients.values())
    if not patients:
        return None

    role = active_session.get("role")
    login_pid = active_session.get("patient_id")
    login_name = (active_session.get("display_name") or "").strip().lower()

    # 1. If logged in as a patient, always scope to their own profile (or match by name).
    if role == "patient":
        if login_pid:
            for ps in patients:
                if ps.profile.patient_id == login_pid:
                    return ps
        if login_name:
            for ps in patients:
                if ps.profile.display_name.lower() == login_name:
                    return ps

    # 2. If a patient name (first/last/full) is mentioned in the question, match it.
    q_lower = question.lower()
    for ps in patients:
        name = ps.profile.display_name.lower()
        parts = [p for p in name.split() if len(p) >= 3]
        if name in q_lower or any(part in q_lower for part in parts):
            return ps

    # 3. Fallback to active patient_id if set, otherwise first patient.
    if login_pid:
        for ps in patients:
            if ps.profile.patient_id == login_pid:
                return ps
    return patients[0]


def _format_patient_context(ps: Any) -> str:
    prof = ps.profile
    latest = ps.readings[-1] if getattr(ps, "readings", None) else None
    latest_res = ps.latest or (ps.alerts[-1] if getattr(ps, "alerts", None) else None)
    tier_val = latest_res.tier.value if latest_res else "NORMAL"
    suppressed_val = bool(latest_res.suppressed) if latest_res else False
    news2_total = latest_res.news2.total if (latest_res and latest_res.news2) else None

    vitals = {
        "heart_rate": getattr(latest, "heart_rate", None),
        "spo2": getattr(latest, "spo2", None),
        "respiratory_rate": getattr(latest, "respiratory_rate", None),
        "temperature": getattr(latest, "temperature", None),
        "systolic_bp": getattr(latest, "systolic_bp", None),
        "activity_intensity": getattr(latest, "activity_intensity", 0.0),
    }
    xgb = compare_with_xgboost(
        patient_state=ps,
        vitals=vitals,
        rule_tier=tier_val,
        news2_total=news2_total,
        suppressed=suppressed_val,
    )
    xgb_line = ""
    if xgb.get("with_xgboost"):
        w = xgb["with_xgboost"]
        wo = xgb["without_xgboost"]
        xgb_line = (
            f"Dual-Engine Risk: Without XGBoost={wo['tier']} ({wo['risk_score']}/100, NEWS2={news2_total}) | "
            f"With XGBoost={w['tier']} ({w['risk_score']}/100, conf={int(w['confidence']*100)}%, {xgb['agreement']})"
        )

    conds = ", ".join(
        getattr(c, "display", None) or getattr(c, "name", None) or str(c)
        for c in (prof.conditions or [])[:5]
    ) or "None recorded"
    meds = ", ".join(
        getattr(m, "display", None) or getattr(m, "name", None) or str(m)
        for m in (prof.medications or [])[:5]
    ) or "None recorded"
    return (
        f"Patient: {prof.display_name} (ID: {prof.patient_id}, Age {prof.age_years}, {prof.sex})\n"
        f"Current Tier: {tier_val} (suppressed={suppressed_val}) at {ps.last_processed or 'N/A'}\n"
        f"Latest Vitals: HR={vitals['heart_rate']} bpm, SpO2={vitals['spo2']}%, "
        f"RR={vitals['respiratory_rate']} br/min, Temp={vitals['temperature']} C\n"
        f"{xgb_line}\n"
        f"Personalization: SpO2 Scale {prof.spo2_scale} (target {prof.spo2_target}), beta_blocked={prof.beta_blocked}\n"
        f"Conditions: {conds}\n"
        f"Medications: {meds}\n"
        f"Latest Alert Narrative: {latest_res.explanation.narrative if (latest_res and latest_res.explanation) else 'None'}"
    )


def answer_clinical_doubt(
    *,
    service: Any,
    question: str,
    active_session: dict[str, Any],
) -> dict[str, Any]:
    """Answer a clinical/patient doubt using Groq openai/gpt-oss-120b."""
    ps = _resolve_patient(service, question, active_session)
    login_user = active_session.get("display_name") or "Guest"
    login_role = active_session.get("role") or "unknown"

    ward_summary = "\n".join(
        f"- {p.profile.display_name}: tier={p.latest.tier.value if p.latest else 'NORMAL'}, alerts={len(p.alerts)}"
        for p in service.patients.values()
    )
    patient_ctx = _format_patient_context(ps) if ps else "No patient loaded."

    system_prompt = (
        "You are VitalTwin Clinical Assistant, answering patient or doctor questions over WhatsApp. "
        "Address the logged-in user by name, tailor your explanation to their role "
        "(plain, reassuring language for a patient; concise clinical detail for a doctor), "
        "and base all numbers strictly on the live patient context below. "
        "Keep replies concise (under 1100 characters) for WhatsApp readability, and always end with: "
        "'Clinical decision support, not medical diagnosis.'\n\n"
        f"Logged-in User: {login_user} (role: {login_role})\n\n"
        f"Focused Patient Context:\n{patient_ctx}\n\n"
        f"Ward Overview:\n{ward_summary}"
    )

    client = Groq(api_key=_groq_api_key())
    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question.strip()},
        ],
        temperature=1,
        max_completion_tokens=2048,
        top_p=1,
        reasoning_effort="medium",
        stream=False,
        stop=None,
    )
    answer = (completion.choices[0].message.content or "").strip()
    return {
        "answer": answer,
        "addressed_to": login_user,
        "role": login_role,
        "patient_name": ps.profile.display_name if ps else None,
        "patient_id": ps.profile.patient_id if ps else None,
    }
