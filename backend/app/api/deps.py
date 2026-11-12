"""Shared FastAPI dependencies and error mapping."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.services.clinical import ClinicalService
from backend.app.services.devices import DeviceService
from backend.app.services.hub import Hub
from backend.app.services.live import LiveRunner
from backend.app.services.queries import QueryService
from backend.app.services.replay import ReplayRunner
from backend.app.services.report import ReportService
from backend.app.services.twin_service import NoBaselineError, TwinNotFoundError, TwinService


@dataclass
class Container:
    settings: Settings
    db: Database
    twins: TwinService
    queries: QueryService
    live: LiveRunner
    hub: Hub
    devices: DeviceService
    clinical: ClinicalService | None = None
    replay: ReplayRunner | None = None
    report: ReportService | None = None


def get_container(request: Request) -> Container:
    return request.app.state.container


def not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=f"not found: {exc}")


def map_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, TwinNotFoundError):
        return not_found(exc)
    if isinstance(exc, NoBaselineError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ValueError, KeyError)):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc
