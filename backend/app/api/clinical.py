"""Clinical monitoring and FHIR ingestion endpoints (PS-03 §3.1, §3.6)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import parse_qs
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket
from pydantic import BaseModel, Field

from backend.app.api.deps import Container, get_container
from backend.app.services.clinical import WARD_CHANNEL
from backend.app.services.groq_qa import answer_clinical_doubt
from backend.app.services.whatsapp import send_whatsapp_alert, send_whatsapp_report
from backend.app.services.xgb_risk import compare_with_xgboost
from clinical.fhir import VITAL_CODINGS, build_reading_bundle, naive_utc
from clinical.hapi_client import HapiError
from digital_twin.types import Observation

router = APIRouter(tags=["clinical"])

DISCLAIMER = "Clinical decision support, not medical diagnosis."
_whatsapp_jobs: dict[str, dict[str, Any]] = {}
_active_session: dict[str, Any] = {"role": None, "display_name": None, "patient_id": None}


def clinical(container: Container = Depends(get_container)):
    service = getattr(container, "clinical", None)
    if service is None:
        raise HTTPException(status_code=503, detail="clinical service not configured")
    return service


class ReadingIn(BaseModel):
    """A normalized reading from the simulator or BIDMC replay."""

    timestamp: str
    heart_rate: float = Field(ge=0, le=300)
    spo2: float | None = Field(default=None, ge=0, le=100)
    respiratory_rate: float | None = Field(default=None, ge=0, le=90)
    temperature: float | None = Field(default=None, ge=25, le=45)
    systolic_bp: float | None = Field(default=None, ge=40, le=300)
    diastolic_bp: float | None = Field(default=None, ge=20, le=200)
    activity_intensity: float = Field(default=0.0, ge=0, le=1)
    source: str = "simulator"
    send_whatsapp: bool = False
    whatsapp_to: str | None = None


@router.get("/api/clinical/patients")
def list_patients(service=Depends(clinical)) -> dict[str, Any]:
    return {"patients": service.list_patients(), "disclaimer": DISCLAIMER}


@router.get("/api/clinical/patients/{patient_id}/state")
def patient_state(patient_id: str, service=Depends(clinical)) -> dict[str, Any]:
    try:
        return service.patient_state_payload(patient_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/clinical/patients/{patient_id}/alerts")
def patient_alerts(
    patient_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    service=Depends(clinical),
) -> dict[str, Any]:
    try:
        return {"alerts": service.alerts(patient_id, limit), "disclaimer": DISCLAIMER}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/clinical/patients/{patient_id}/readings")
def patient_readings(
    patient_id: str,
    limit: int = Query(default=300, ge=1, le=2000),
    service=Depends(clinical),
) -> dict[str, Any]:
    try:
        return {"readings": service.readings(patient_id, limit)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/fhir/Observation")
async def ingest_observation(request: Request, service=Depends(clinical)) -> dict[str, Any]:
    """Accept a FHIR R4 Vital Signs Observation, store it in HAPI, then score."""
    body = await request.json()
    if body.get("resourceType") != "Observation":
        raise HTTPException(status_code=400, detail="resourceType must be Observation")
    try:
        return await service.ingest_fhir_observation(body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, HapiError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/fhir/Bundle")
async def ingest_bundle(request: Request, service=Depends(clinical)) -> dict[str, Any]:
    """Accept a FHIR Bundle of Vital Signs Observations."""
    body = await request.json()
    if body.get("resourceType") != "Bundle":
        raise HTTPException(status_code=400, detail="resourceType must be Bundle")
    try:
        return await service.ingest_fhir_bundle(body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, HapiError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/clinical/patients/{patient_id}/readings")
async def ingest_reading(
    patient_id: str, reading: ReadingIn, service=Depends(clinical)
) -> dict[str, Any]:
    """Internal ingestion for the simulator and BIDMC replay.

    Each vital is stored as its own LOINC-coded FHIR Observation in HAPI.
    """
    try:
        ts = naive_utc(datetime.fromisoformat(reading.timestamp.replace("Z", "+00:00")))
        if reading.source == "smartwatch_demo":
            patient_state = service.patient(patient_id)
            if patient_state.last_processed is not None and (
                ts <= patient_state.last_processed
                or (ts - patient_state.last_processed).total_seconds() > 300
            ):
                ts = patient_state.last_processed + timedelta(seconds=60)
            obs = Observation(
                timestamp=ts,
                heart_rate=reading.heart_rate,
                spo2=reading.spo2,
                respiratory_rate=reading.respiratory_rate,
                temperature=reading.temperature,
                systolic_bp=reading.systolic_bp,
                diastolic_bp=reading.diastolic_bp,
                activity_intensity=reading.activity_intensity,
                source=reading.source,
            )
            # Score immediately so the live demo isn't blocked by HAPI H2 write contention,
            # and persist the FHIR bundle asynchronously in the background.
            bundle_obs = [
                entry["resource"] for entry in build_reading_bundle(patient_id, obs)["entry"]
            ]
            if bundle_obs:
                async def _store_bg() -> None:
                    try:
                        await service.hapi.store_bundle(bundle_obs)
                        patient_state.stored_observations += len(bundle_obs)
                    except Exception:
                        pass
                asyncio.create_task(_store_bg())
            result = service._score_timestamp(
                patient_state,
                obs.timestamp,
                {m: getattr(obs, m, None) for m in VITAL_CODINGS},
                obs=obs,
            )
        else:
            obs = Observation(
                timestamp=ts,
                heart_rate=reading.heart_rate,
                spo2=reading.spo2,
                respiratory_rate=reading.respiratory_rate,
                temperature=reading.temperature,
                systolic_bp=reading.systolic_bp,
                diastolic_bp=reading.diastolic_bp,
                activity_intensity=reading.activity_intensity,
                source=reading.source,
            )
            result = await service.ingest_reading(patient_id, obs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, HapiError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        return {"scored": False}
    payload = result.to_dict()
    patient_state = service.patient(patient_id)
    payload["xgboost"] = compare_with_xgboost(
        patient_state=patient_state,
        vitals={
            "heart_rate": reading.heart_rate,
            "spo2": reading.spo2,
            "respiratory_rate": reading.respiratory_rate,
            "temperature": reading.temperature,
            "systolic_bp": reading.systolic_bp,
            "activity_intensity": reading.activity_intensity,
        },
        rule_tier=result.tier.value,
        news2_total=result.news2.total if result.news2 else None,
        suppressed=result.suppressed,
    )
    if (
        reading.send_whatsapp
        and result.alert_kind.value == "CLINICAL"
        and not result.suppressed
        and result.tier.value in ("WATCH", "REVIEW", "URGENT")
    ):
        profile = service.patient(patient_id).profile
        job_id = uuid.uuid4().hex[:12]
        _whatsapp_jobs[job_id] = {"queued": True, "sent": False}
        vitals_snapshot = {
            "heart_rate": reading.heart_rate,
            "spo2": reading.spo2,
            "respiratory_rate": reading.respiratory_rate,
            "temperature": reading.temperature,
        }
        narrative_text = (
            result.explanation.narrative
            if result.explanation
            else "Abnormal reading detected."
        )
        tier_value = result.tier.value
        news2_total = result.news2.total if result.news2 else None

        async def _send_wa_bg() -> None:
            try:
                wa = await asyncio.to_thread(
                    send_whatsapp_alert,
                    patient_name=profile.display_name,
                    patient_id=patient_id,
                    tier=tier_value,
                    news2_total=news2_total,
                    vitals=vitals_snapshot,
                    narrative=narrative_text,
                    recipient_override=reading.whatsapp_to,
                )
                _whatsapp_jobs[job_id] = {"queued": False, **wa}
            except Exception as exc:
                _whatsapp_jobs[job_id] = {"queued": False, "sent": False, "error": str(exc)}

        asyncio.create_task(_send_wa_bg())
        payload["whatsapp"] = {"queued": True, "job_id": job_id, "sent": False}
    return payload


@router.get("/api/clinical/whatsapp/{job_id}")
async def whatsapp_status(job_id: str) -> dict[str, Any]:
    return _whatsapp_jobs.get(job_id) or {"queued": False, "sent": False, "error": "Unknown job"}


class WhatsAppReportIn(BaseModel):
    title: str
    summary_text: str
    whatsapp_to: str | None = None


@router.post("/api/clinical/whatsapp/report")
async def send_report_via_whatsapp(body: WhatsAppReportIn) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    _whatsapp_jobs[job_id] = {"queued": True, "sent": False}

    async def _send_report_bg() -> None:
        try:
            wa = await asyncio.to_thread(
                send_whatsapp_report,
                title=body.title,
                summary_text=body.summary_text,
                recipient_override=body.whatsapp_to,
            )
            _whatsapp_jobs[job_id] = {"queued": False, **wa}
        except Exception as exc:
            _whatsapp_jobs[job_id] = {"queued": False, "sent": False, "error": str(exc)}

    asyncio.create_task(_send_report_bg())
    return {"queued": True, "job_id": job_id, "sent": False}


@router.get("/api/clinical/patients/{patient_id}/xgboost")
async def patient_xgboost_comparison(patient_id: str, service=Depends(clinical)) -> dict[str, Any]:
    try:
        ps = service.patient(patient_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    latest = ps.readings[-1] if getattr(ps, "readings", None) else None
    last_alert = ps.alerts[-1] if getattr(ps, "alerts", None) else None
    vitals = {
        "heart_rate": getattr(latest, "heart_rate", None),
        "spo2": getattr(latest, "spo2", None),
        "respiratory_rate": getattr(latest, "respiratory_rate", None),
        "temperature": getattr(latest, "temperature", None),
        "systolic_bp": getattr(latest, "systolic_bp", None),
        "activity_intensity": getattr(latest, "activity_intensity", 0.0),
    }
    news2_total = last_alert.news2.total if (last_alert and last_alert.news2) else None
    latest_res = ps.latest or last_alert
    return compare_with_xgboost(
        patient_state=ps,
        vitals=vitals,
        rule_tier=latest_res.tier.value if latest_res else "NORMAL",
        news2_total=news2_total,
        suppressed=bool(latest_res.suppressed) if latest_res else False,
    )


@router.get("/api/clinical/hapi/health")
async def hapi_health(service=Depends(clinical)) -> dict[str, Any]:
    healthy = await service.hapi.healthy()
    return {"healthy": healthy, "base_url": service.hapi.base_url}


@router.websocket("/ws/clinical/ward")
async def ward_socket(websocket: WebSocket) -> None:
    """Ward overview: one row per patient, pushed as readings are scored."""
    c: Container = websocket.app.state.container
    await websocket.accept()
    if c.clinical is None:
        await websocket.send_json({"type": "error", "detail": "clinical service not configured"})
        await websocket.close()
        return
    initial: list[dict[str, Any]] = [
        {"type": "ward_snapshot", "data": {"patients": c.clinical.list_patients()}},
        {"type": "replay", "data": c.replay.status() if c.replay else {}},
    ]
    await c.hub.serve(websocket, WARD_CHANNEL, initial)


@router.websocket("/ws/clinical/{patient_id}")
async def clinical_socket(websocket: WebSocket, patient_id: str) -> None:
    c: Container = websocket.app.state.container
    await websocket.accept()
    service = c.clinical
    if service is None or patient_id not in service.patients:
        await websocket.send_json({"type": "error", "detail": f"unknown patient {patient_id!r}"})
        await websocket.close()
        return
    initial = [
        {"type": "clinical", "data": service.patient_state_payload(patient_id)},
        {"type": "alerts", "data": {"alerts": service.alerts(patient_id, 50)}},
        {"type": "replay", "data": c.replay.status() if c.replay else {}},
    ]
    await c.hub.serve(websocket, patient_id, initial)


@router.get("/api/clinical/hapi/observations/{patient_id}")
async def hapi_observations(
    patient_id: str,
    loinc: str | None = Query(default=None),
    count: int = Query(default=20, ge=1, le=200),
    service=Depends(clinical),
) -> dict[str, Any]:
    """Proxy a HAPI search, to demonstrate stored FHIR validity."""
    try:
        bundle = await service.hapi.search_observations(patient_id, loinc=loinc, count=count)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail="HAPI FHIR is unavailable or timed out. Check the FHIR server and retry.",
        ) from exc
    except HapiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "total": bundle.get("total"),
        "entries": [e.get("resource") for e in bundle.get("entry", [])],
    }


class ActiveSessionIn(BaseModel):
    role: str | None = None
    display_name: str | None = None
    patient_id: str | None = None


@router.post("/api/clinical/session/active")
async def set_active_session(body: ActiveSessionIn) -> dict[str, Any]:
    _active_session["role"] = body.role
    _active_session["display_name"] = body.display_name
    _active_session["patient_id"] = body.patient_id
    return {"ok": True, **_active_session}


class WhatsAppAskIn(BaseModel):
    question: str = Field(..., min_length=1)
    send_to_whatsapp: bool = True
    whatsapp_to: str | None = None
    role: str | None = None
    display_name: str | None = None
    patient_id: str | None = None


@router.post("/api/clinical/whatsapp/ask")
async def ask_clinical_whatsapp(body: WhatsAppAskIn, service=Depends(clinical)) -> dict[str, Any]:
    session_ctx = {
        "role": body.role or _active_session.get("role"),
        "display_name": body.display_name or _active_session.get("display_name"),
        "patient_id": body.patient_id or _active_session.get("patient_id"),
    }
    qa = await asyncio.to_thread(
        answer_clinical_doubt,
        service=service,
        question=body.question,
        active_session=session_ctx,
    )
    if body.send_to_whatsapp and qa.get("answer"):
        job_id = uuid.uuid4().hex[:12]
        _whatsapp_jobs[job_id] = {"queued": True, "sent": False}
        title = f"VitalTwin Q&A for {qa['addressed_to']}"

        async def _send_qa_bg() -> None:
            try:
                wa = await asyncio.to_thread(
                    send_whatsapp_report,
                    title=title,
                    summary_text=qa["answer"],
                    recipient_override=body.whatsapp_to,
                )
                _whatsapp_jobs[job_id] = {"queued": False, **wa}
            except Exception as exc:
                _whatsapp_jobs[job_id] = {"queued": False, "sent": False, "error": str(exc)}

        asyncio.create_task(_send_qa_bg())
        qa["whatsapp"] = {"queued": True, "job_id": job_id, "sent": False}
    return qa


@router.post("/api/clinical/whatsapp/webhook")
async def whatsapp_incoming_webhook(request: Request, service=Depends(clinical)) -> Response:
    raw = (await request.body()).decode("utf-8", errors="ignore")
    form = parse_qs(raw)
    question = (form.get("Body") or [""])[0].strip()
    if not question:
        twiml = "<Response><Message>Please send your health or patient question.</Message></Response>"
        return Response(content=twiml, media_type="application/xml")
    try:
        qa = await asyncio.to_thread(
            answer_clinical_doubt,
            service=service,
            question=question,
            active_session=_active_session,
        )
        reply = qa.get("answer") or "Unable to answer right now."
    except Exception as exc:
        reply = f"Error answering question: {exc}"
    twiml = f"<Response><Message>{escape(reply[:1550])}</Message></Response>"
    return Response(content=twiml, media_type="application/xml")