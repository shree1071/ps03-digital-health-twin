"""Clinical monitoring and FHIR ingestion endpoints (PS-03 §3.1, §3.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, Field

from backend.app.api.deps import Container, get_container
from backend.app.services.clinical import WARD_CHANNEL
from clinical.fhir import naive_utc
from clinical.hapi_client import HapiError
from digital_twin.types import Observation

router = APIRouter(tags=["clinical"])

DISCLAIMER = "Clinical decision support, not medical diagnosis."


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
        obs = Observation(
            timestamp=naive_utc(datetime.fromisoformat(reading.timestamp.replace("Z", "+00:00"))),
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
    return result.to_dict() if result else {"scored": False}


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