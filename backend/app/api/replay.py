"""Scenario replay control endpoints (PS-03 §3.2, §3.7)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.app.api.deps import Container, get_container

router = APIRouter(tags=["clinical"])


def replay(container: Container = Depends(get_container)):
    runner = getattr(container, "replay", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="replay runner not configured")
    return runner


def report_service(container: Container = Depends(get_container)):
    service = getattr(container, "report", None)
    if service is None:
        raise HTTPException(status_code=503, detail="report service not configured")
    return service


class StartIn(BaseModel):
    patient_ids: list[str] | None = None


@router.get("/api/clinical/replay/status")
def replay_status(runner=Depends(replay)) -> dict[str, Any]:
    return runner.status()


@router.post("/api/clinical/replay/start")
async def replay_start(body: StartIn | None = None, runner=Depends(replay)) -> dict[str, Any]:
    try:
        return await runner.start(body.patient_ids if body else None)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/clinical/replay/stop")
async def replay_stop(runner=Depends(replay)) -> dict[str, Any]:
    return await runner.stop()


@router.post("/api/clinical/replay/reset")
async def replay_reset(runner=Depends(replay)) -> dict[str, Any]:
    return await runner.reset()


@router.get("/api/clinical/report")
async def performance_report(
    refresh: bool = Query(default=False), service=Depends(report_service)
) -> dict[str, Any]:
    """Replay every scenario through both monitors and compare (§3.7).

    Runs off the event loop: it scores a few thousand readings twice.
    """
    return await asyncio.to_thread(service.generate, refresh)