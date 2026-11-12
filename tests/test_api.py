"""REST and WebSocket API."""

import time
from datetime import datetime, timedelta


TWIN = "twin-001"


def _last_time(client) -> datetime:
    t = client.get(f"/api/twin/{TWIN}/state").json()["twin_time"]
    return datetime.fromisoformat(t.rstrip("Z"))


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["twins"] == 1


def test_twin_identity_has_no_pii(client):
    twin = client.get(f"/api/twin/{TWIN}").json()
    assert twin["subject"]["is_synthetic"] is True
    assert set(twin["subject"]) == {"id", "pseudonym", "age_years", "is_synthetic", "utc_offset_hours", "data_source"}
    assert client.get("/api/twin/nope").status_code == 404


def test_state_and_vitals(client):
    state = client.get(f"/api/twin/{TWIN}/state").json()
    assert state["state"] in {"STABLE", "ACTIVE", "HIGH_ACTIVITY", "RECOVERY", "SLEEPING", "ELEVATED", "ANOMALOUS"}
    assert set(state["assessments"]) == {"heart_rate", "spo2", "temperature", "respiratory_rate"}
    vitals = client.get(f"/api/twin/{TWIN}/vitals").json()
    assert "expected" in vitals["vitals"]["heart_rate"]


def test_history_ranges(client):
    for rng in ("1h", "today", "24h", "7d"):
        body = client.get(f"/api/twin/{TWIN}/history", params={"range": rng}).json()
        assert body["points"], rng
        assert len(body["points"]) <= 601
    end = _last_time(client)
    custom = client.get(
        f"/api/twin/{TWIN}/history",
        params={"range": "custom", "start": (end - timedelta(hours=3)).isoformat(), "end": end.isoformat()},
    )
    assert custom.status_code == 200 and custom.json()["raw_points"] > 30
    assert client.get(f"/api/twin/{TWIN}/history", params={"range": "custom"}).status_code == 400


def test_baseline_and_events_and_wellness(client):
    b = client.get(f"/api/twin/{TWIN}/baseline").json()
    assert b["data"]["hr_rest"] > 40 and len(b["comparison"]) == 4
    events = client.get(f"/api/twin/{TWIN}/events").json()
    assert any(e["kind"] == "transition" for e in events)
    w = client.get(f"/api/twin/{TWIN}/wellness").json()
    assert w["recovery_index"]["heuristic"] is True


def test_ingest_validation(client):
    t = _last_time(client) + timedelta(seconds=15)
    good = {"timestamp": t.isoformat() + "Z", "heart_rate": 70, "spo2": 98, "temperature": 36.7, "respiratory_rate": 14}
    r = client.post(f"/api/twin/{TWIN}/observations", json=good)
    assert r.status_code == 200 and r.json()["accepted"]
    assert client.post(f"/api/twin/{TWIN}/observations", json=good).status_code == 409  # duplicate time
    bad = dict(good, timestamp=(t + timedelta(seconds=15)).isoformat(), heart_rate=500)
    assert client.post(f"/api/twin/{TWIN}/observations", json=bad).status_code == 422
    extra = dict(good, timestamp=(t + timedelta(seconds=30)).isoformat(), name="Alice")
    assert client.post(f"/api/twin/{TWIN}/observations", json=extra).status_code == 422  # no extra fields


def test_csv_ingestion(client):
    t = _last_time(client)
    lines = ["timestamp,bpm,spo2,temp_f,rr"]
    for i in range(1, 4):
        lines.append(f"{(t + timedelta(seconds=15 * i)).isoformat()},70,0.98,98.2,14")
    lines.append(f"{(t + timedelta(seconds=60)).isoformat()},999,98,98,14")
    r = client.post(
        f"/api/twin/{TWIN}/observations/csv", files={"file": ("export.csv", "\n".join(lines), "text/csv")}
    )
    body = r.json()
    assert body["accepted"] == 3 and body["rejected"] == 1


def test_simulation_endpoint(client):
    r = client.post("/api/simulation", json={"scenario": "exercise", "twin_id": TWIN, "intensity": 0.6, "duration_min": 10})
    assert r.status_code == 200 and r.json()["run_id"] >= 1
    r = client.post("/api/simulation", json={"scenario": "sleep_restriction", "twin_id": TWIN, "sleep_hours": 5, "nights": 2})
    assert r.status_code == 200
    assert client.post("/api/simulation", json={"scenario": "exercise", "twin_id": TWIN, "intensity": 3}).status_code == 422
    assert len(client.get("/api/simulation/runs", params={"twin_id": TWIN}).json()) == 2


def test_live_simulator_control_and_websocket(client):
    start = client.post("/api/simulator/start", json={"twin_id": TWIN, "scenario": "EXERCISE"}).json()
    assert start["running"] and start["scenario"] == "EXERCISE"
    with client.websocket_connect(f"/ws/twin/{TWIN}") as ws:
        kinds = {ws.receive_json()["type"] for _ in range(4)}
        assert "state" in kinds
    assert client.post("/api/simulator/scenario", json={"scenario": "BOGUS"}).status_code == 400
    assert client.post("/api/simulator/scenario", json={"scenario": "RECOVERY"}).json()["scenario"] == "RECOVERY"
    time.sleep(0.3)
    assert client.get("/api/simulator/status").json()["ticks"] > 0
    assert client.post("/api/simulator/stop").json()["running"] is False
    assert client.post("/api/simulator/scenario", json={"scenario": "NORMAL"}).status_code == 409


def test_system_info(client):
    info = client.get("/api/system/info").json()
    assert info["counts"]["observations"] > 100
    assert "not a medical device" in info["disclaimer"].lower()
    assert client.get("/openapi.json").status_code == 200


def test_replay_backup_demo(settings):
    """The pre-recorded session replays through the normal ingestion path."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from backend.app.factory import create_app

    settings.replay_file = str(Path(__file__).resolve().parents[1] / "data" / "demo_recording.jsonl")
    with TestClient(create_app(settings)) as c:
        status = c.post("/api/simulator/start", json={"twin_id": TWIN, "mode": "replay"}).json()
        assert status["mode"] == "replay" and status["running"]
        time.sleep(0.4)
        state = c.get(f"/api/twin/{TWIN}/state").json()
        assert state["observation"]["source"] == "replay"
        c.post("/api/simulator/stop")


def test_fhir_search_timeout_is_reported_as_service_unavailable(client, monkeypatch):
    from unittest.mock import AsyncMock

    import httpx

    service = client.app.state.container.clinical
    monkeypatch.setattr(service.hapi, "search_observations", AsyncMock(side_effect=httpx.ReadTimeout("")))
    response = client.get("/api/clinical/hapi/observations/synthetic-patient")

    assert response.status_code == 503
    assert "HAPI FHIR is unavailable or timed out" in response.json()["detail"]
