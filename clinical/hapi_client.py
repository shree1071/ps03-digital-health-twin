"""Async client for the HAPI FHIR R4 server (PS-03 §3.1 storage)."""

from __future__ import annotations

from typing import Any

import httpx

from clinical.fhir import build_patient, build_vital_observation, validate_vital_observation
from clinical.profiles import DischargeProfile

DEFAULT_BASE_URL = "http://localhost:8080/fhir"
FHIR_JSON = "application/fhir+json"


class HapiError(RuntimeError):
    """Raised when the FHIR server rejects a request."""


class HapiClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Content-Type": FHIR_JSON, "Accept": FHIR_JSON},
            # Local server: ignore env proxies (httpx mis-parses NO_PROXY "[::1]").
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def metadata(self) -> dict[str, Any]:
        response = await self._client.get("/metadata")
        response.raise_for_status()
        return response.json()

    async def healthy(self) -> bool:
        try:
            await self.metadata()
            return True
        except (httpx.HTTPError, ValueError):
            return False

    async def ensure_patient(self, profile: DischargeProfile) -> dict[str, Any]:
        response = await self._client.put(
            f"/Patient/{profile.patient_id}", json=build_patient(profile.patient_id, profile)
        )
        if response.status_code >= 400:
            raise HapiError(f"Patient upsert failed: {response.status_code} {response.text[:400]}")
        return response.json()

    async def store_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        problems = validate_vital_observation(observation)
        if problems:
            raise HapiError("Invalid FHIR Observation: " + "; ".join(problems))
        response = await self._client.post("/Observation", json=observation)
        if response.status_code >= 400:
            raise HapiError(f"Observation store failed: {response.status_code} {response.text[:400]}")
        return response.json()

    async def store_reading(
        self,
        patient_id: str,
        metric: str,
        value: float | None,
        effective,
        *,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        observation = build_vital_observation(
            patient_id, metric, value, effective, device_id=device_id
        )
        return await self.store_observation(observation)

    async def store_bundle(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        """Store many Observations in one FHIR transaction bundle."""
        for observation in observations:
            problems = validate_vital_observation(observation)
            if problems:
                raise HapiError("Invalid FHIR Observation: " + "; ".join(problems))
        bundle = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {
                    "resource": observation,
                    "request": {"method": "POST", "url": "Observation"},
                }
                for observation in observations
            ],
        }
        response = await self._client.post("/", json=bundle)
        if response.status_code >= 400:
            raise HapiError(f"Bundle store failed: {response.status_code} {response.text[:400]}")
        return response.json()

    async def search_observations(
        self,
        patient_id: str,
        *,
        loinc: str | None = None,
        count: int = 100,
        sort: str = "-date",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"patient": patient_id, "_count": count, "_sort": sort}
        if loinc:
            params["code"] = f"http://loinc.org|{loinc}"
        response = await self._client.get("/Observation", params=params)
        if response.status_code >= 400:
            raise HapiError(f"Search failed: {response.status_code} {response.text[:400]}")
        return response.json()

    async def count_observations(self, patient_id: str) -> int:
        bundle = await self.search_observations(patient_id, count=0)
        return int(bundle.get("total", 0))