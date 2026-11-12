from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

log = logging.getLogger("ps03.whatsapp")

DEFAULT_W_ENV = Path(r"C:\Users\shree\Downloads\whatsaap\w.env")


def _load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if DEFAULT_W_ENV.exists():
        for k, v in dotenv_values(DEFAULT_W_ENV).items():
            if v is not None:
                cfg[k] = v.strip()
    for key in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_API_KEY",
        "TWILIO_API_SECRET",
        "TWILIO_WHATSAPP_FROM",
        "TWILIO_MESSAGING_SERVICE_SID",
        "ALERT_RECIPIENTS",
        "DRY_RUN",
    ):
        env_val = os.environ.get(key)
        if env_val:
            cfg[key] = env_val.strip()
    return cfg


def _normalize_whatsapp(number: str) -> str:
    raw = number.strip()
    if raw.startswith("whatsapp:"):
        return raw
    if not raw.startswith("+"):
        raw = f"+{raw}"
    return f"whatsapp:{raw}"


def _send_twilio_message(body: str, recipient_override: str | None = None) -> dict[str, Any]:
    """Send a WhatsApp message via Twilio using w.env credentials."""
    cfg = _load_config()
    sender = cfg.get("TWILIO_WHATSAPP_FROM", "")
    sid = cfg.get("TWILIO_ACCOUNT_SID", "")
    auth_token = cfg.get("TWILIO_AUTH_TOKEN", "")
    api_key = cfg.get("TWILIO_API_KEY", "")
    api_secret = cfg.get("TWILIO_API_SECRET", "")
    dry_run = cfg.get("DRY_RUN", "false").lower() in ("1", "true", "yes")

    raw_recipients = (
        recipient_override.strip()
        if recipient_override and recipient_override.strip()
        else cfg.get("ALERT_RECIPIENTS", "")
    )
    recipients = [
        _normalize_whatsapp(r)
        for r in raw_recipients.split(",")
        if r.strip()
    ]

    if not recipients:
        return {"sent": False, "error": "No ALERT_RECIPIENTS configured."}
    if not sender or not sid or (not auth_token and not (api_key and api_secret)):
        return {"sent": False, "error": "Twilio WhatsApp credentials missing in w.env."}

    if dry_run:
        log.info("WhatsApp DRY_RUN message to %s: %s", recipients, body)
        return {"sent": True, "dry_run": True, "recipients": recipients, "sids": []}

    try:
        from twilio.rest import Client

        client = (
            Client(api_key, api_secret, sid)
            if (api_key and api_secret)
            else Client(sid, auth_token)
        )
        from_wa = _normalize_whatsapp(sender)
        sids: list[str] = []
        for to_wa in recipients:
            msg = client.messages.create(from_=from_wa, to=to_wa, body=body[:1580])
            sids.append(msg.sid)
        return {"sent": True, "dry_run": False, "recipients": recipients, "sids": sids}
    except Exception as exc:
        log.warning("WhatsApp send failed: %s", exc)
        return {"sent": False, "error": str(exc), "recipients": recipients}


def send_whatsapp_alert(
    *,
    patient_name: str,
    patient_id: str,
    tier: str,
    news2_total: int | None,
    vitals: dict[str, float | None],
    narrative: str,
    recipient_override: str | None = None,
) -> dict[str, Any]:
    """Send a WhatsApp alert via Twilio using w.env credentials."""
    hr = vitals.get("heart_rate")
    spo2 = vitals.get("spo2")
    rr = vitals.get("respiratory_rate")
    temp = vitals.get("temperature")

    body = (
        f"🚨 *Post-Discharge {tier} Alert*\n"
        f"Patient: *{patient_name}* (`{patient_id}`)\n"
        f"NEWS2 Score: *{news2_total if news2_total is not None else '—'}*\n"
        f"Vitals: HR {hr if hr is not None else '—'} bpm | "
        f"SpO₂ {spo2 if spo2 is not None else '—'}% | "
        f"RR {rr if rr is not None else '—'} br/min | "
        f"Temp {temp if temp is not None else '—'}°C\n"
        f"Details: {narrative}\n\n"
        f"_Clinical decision support, not medical diagnosis._"
    )
    return _send_twilio_message(body, recipient_override=recipient_override)


def send_whatsapp_report(
    *,
    title: str,
    summary_text: str,
    recipient_override: str | None = None,
) -> dict[str, Any]:
    """Send a formatted clinical/health report summary via WhatsApp."""
    body = f"📋 *{title}*\n\n{summary_text.strip()}"
    if len(body) > 1580:
        body = body[:1550].rstrip() + "\n…_(truncated for WhatsApp)_"
    return _send_twilio_message(body, recipient_override=recipient_override)
