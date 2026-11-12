"""FastAPI application factory (imported by main.py and by the tests)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from backend.app.api import clinical, devices, replay, simulation, system, twins
from backend.app.api.deps import Container
from backend.app.config import Settings, get_settings
from backend.app.db import Database
from backend.app.services.clinical import ClinicalService
from backend.app.services.devices import DeviceService
from backend.app.services.hub import Hub
from backend.app.services.live import LiveRunner
from backend.app.services.queries import QueryService
from backend.app.services.replay import ReplayRunner
from backend.app.services.report import ReportService
from backend.app.services.seed import DEFAULT_TWIN_ID, seed_database
from backend.app.services.twin_service import TwinService

log = logging.getLogger("digital_twin")

DESCRIPTION = """
**PS-03 — Personalized Post-Discharge Deterioration Monitor.**

Wearable vital signs are ingested as FHIR R4 Vital Signs Observations and
stored in a HAPI FHIR server. A NEWS2-style score is personalized per patient
using their Synthea discharge profile (conditions and medications) and their
own rolling baseline. Sensor artifacts are detected and suppressed, and every
Watch / Review / Urgent alert explains the reading, the rule and the profile
fact behind it.

⚠️ Synthetic data only. Clinical decision support, not medical diagnosis.
"""


def build_container(settings: Settings, app: FastAPI) -> Container:
    db = Database(settings.database_url)
    db.create_all()
    twins_service = TwinService(db)
    hub = Hub()
    live = LiveRunner(twins_service, settings, on_status=lambda s: hub.publish_all({"type": "live", "data": s}))
    twins_service.add_listener(lambda twin_id, payload: hub.publish(twin_id, {"type": "state", "data": payload}))
    clinical_service = ClinicalService(
        hub, settings.hapi_base_url, Path(settings.clinical_profile_dir)
    )
    replay_runner = ReplayRunner(
        clinical_service, settings,
        ingestion_client=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost",
            headers={"Content-Type": "application/fhir+json"}, trust_env=False,
        ),
        on_status=lambda s: hub.publish_all({"type": "replay", "data": s})
    )
    return Container(
        settings=settings, db=db, twins=twins_service, queries=QueryService(twins_service), live=live, hub=hub,
        devices=DeviceService(twins_service), clinical=clinical_service, replay=replay_runner,
        report=ReportService(clinical_service, settings),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container: Container = app.state.container
        container.hub.bind(asyncio.get_running_loop())
        if container.clinical is not None:
            count = await asyncio.to_thread(container.clinical.load_profiles)
            log.info("loaded %d discharge profiles", count)
            try:
                await container.clinical.ensure_patients()
            except Exception:
                log.exception("could not register patients in HAPI; is the FHIR server running?")
        if settings.seed_on_startup:
            await asyncio.to_thread(seed_database, container.twins, settings)
        if settings.autostart_live:
            try:
                await container.live.start(DEFAULT_TWIN_ID, "NORMAL")
            except Exception:
                log.exception("could not autostart live simulator")
        if settings.clinical_autostart and container.replay is not None:
            try:
                await container.replay.start()
            except Exception:
                log.exception("could not autostart clinical replay")
        yield
        if container.replay is not None:
            await container.replay.stop()
            await container.replay.ingestion_client.aclose()
        await container.live.stop()
        if container.clinical is not None:
            await container.clinical.close()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.container = build_container(settings, app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    app.include_router(twins.router)
    app.include_router(simulation.router)
    app.include_router(system.router)
    app.include_router(devices.router)
    app.include_router(clinical.router)
    app.include_router(replay.router)

    dist = Path(settings.frontend_dist)
    if dist.is_dir() and (dist / "index.html").exists():
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "ws/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app

