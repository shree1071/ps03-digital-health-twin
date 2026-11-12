"""FHIR R4 Vital Signs Observations (PS-03 §3.1).

Builds and parses Observations conforming to the HL7 FHIR R4 Vital Signs
profile (http://hl7.org/fhir/R4/observation-vitalsigns.html), with LOINC
coding and UCUM units.

A missing reading is represented with ``dataAbsentReason`` rather than a zero
value. This matters for probe removal: a SpO2 of 0% is a sensor state, and
encoding it as a real quantity would pollute the clinical record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
ABSENT_REASON_SYSTEM = "http://terminology.hl7.org/CodeSystem/data-absent-reason"
VITALSIGNS_PROFILE = "http://hl7.org/fhir/StructureDefinition/vitalsigns"


@dataclass(frozen=True)
class VitalCoding:
    metric: str
    loinc: str
    display: str
    ucum: str
    unit: str


# The four vitals mandated by PS-03 §3.1, plus blood pressure which NEWS2 also
# uses. BIDMC provides no temperature and no BP; both come from the simulator.
VITAL_CODINGS: dict[str, VitalCoding] = {
    "heart_rate": VitalCoding("heart_rate", "8867-4", "Heart rate", "/min", "beats/minute"),
    "spo2": VitalCoding("spo2", "2708-6", "Oxygen saturation in Arterial blood", "%", "%"),
    "respiratory_rate": VitalCoding("respiratory_rate", "9279-1", "Respiratory rate", "/min", "breaths/minute"),
    "temperature": VitalCoding("temperature", "8310-5", "Body temperature", "Cel", "°C"),
    "systolic_bp": VitalCoding("systolic_bp", "8480-6", "Systolic blood pressure", "mm[Hg]", "mmHg"),
    "diastolic_bp": VitalCoding("diastolic_bp", "8462-4", "Diastolic blood pressure", "mm[Hg]", "mmHg"),
}

LOINC_TO_METRIC: dict[str, str] = {c.loinc: m for m, c in VITAL_CODINGS.items()}

MANDATORY_METRICS: tuple[str, ...] = ("heart_rate", "spo2", "respiratory_rate", "temperature")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_vital_observation(
    patient_id: str,
    metric: str,
    value: float | None,
    effective: datetime,
    *,
    device_id: str | None = None,
    absent_reason: str = "not-performed",
    observation_id: str | None = None,
) -> dict[str, Any]:
    """Build one FHIR R4 Vital Signs Observation.

    ``value=None`` produces a ``dataAbsentReason`` instead of a quantity.
    """
    coding = VITAL_CODINGS[metric]
    observation: dict[str, Any] = {
        "resourceType": "Observation",
        "status": "final",
        "meta": {"profile": [VITALSIGNS_PROFILE]},
        "category": [
            {
                "coding": [
                    {
                        "system": CATEGORY_SYSTEM,
                        "code": "vital-signs",
                        "display": "Vital Signs",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {"system": LOINC_SYSTEM, "code": coding.loinc, "display": coding.display}
            ],
            "text": coding.display,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": _iso(effective),
    }
    if observation_id:
        observation["id"] = observation_id
    if value is None:
        observation["dataAbsentReason"] = {
            "coding": [
                {
                    "system": ABSENT_REASON_SYSTEM,
                    "code": absent_reason,
                    "display": absent_reason.replace("-", " ").title(),
                }
            ]
        }
    else:
        observation["valueQuantity"] = {
            "value": round(float(value), 2),
            "unit": coding.unit,
            "system": UCUM_SYSTEM,
            "code": coding.ucum,
        }
    if device_id:
        observation["device"] = {"reference": f"Device/{device_id}"}
    return observation


SOURCE_EXTENSION = "urn:minearena:ps03:source"
ACTIVITY_EXTENSION = "urn:minearena:ps03:activity-intensity"


def build_reading_bundle(patient_id: str, obs: Any) -> dict[str, Any]:
    observations = []
    for metric in VITAL_CODINGS:
        value = getattr(obs, metric, None)
        if value is None and metric not in MANDATORY_METRICS:
            continue
        observation = build_vital_observation(patient_id, metric, value, obs.timestamp)
        if metric == "heart_rate":
            observation["extension"] = [
                {"url": SOURCE_EXTENSION, "valueString": obs.source},
                {"url": ACTIVITY_EXTENSION, "valueDecimal": obs.activity_intensity},
            ]
        observations.append(observation)
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": observation, "request": {"method": "POST", "url": "Observation"}}
            for observation in observations
        ],
    }


def build_patient(patient_id: str, profile: Any) -> dict[str, Any]:
    """Minimal FHIR Patient so Observation.subject references resolve."""
    return {
        "resourceType": "Patient",
        "id": patient_id,
        "identifier": [
            {"system": "urn:minearena:ps03:patient", "value": patient_id}
        ],
        "name": [{"text": profile.display_name}],
        "gender": "male" if profile.sex.lower().startswith("m") else "female",
        "birthDate": _birth_date(profile),
    }


def _birth_date(profile: Any) -> str:
    year = datetime.now(timezone.utc).year - int(profile.age_years)
    return f"{year}-01-01"


def metric_from_observation(observation: dict[str, Any]) -> str | None:
    for coding in observation.get("code", {}).get("coding", []):
        if coding.get("system") == LOINC_SYSTEM:
            metric = LOINC_TO_METRIC.get(coding.get("code", ""))
            if metric:
                return metric
    return None


def value_from_observation(observation: dict[str, Any]) -> float | None:
    quantity = observation.get("valueQuantity")
    if not quantity:
        return None
    value = quantity.get("value")
    return float(value) if value is not None else None


def effective_from_observation(observation: dict[str, Any]) -> datetime | None:
    raw = observation.get("effectiveDateTime") or observation.get("effectivePeriod", {}).get("start")
    if not raw:
        return None
    return naive_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))


def naive_utc(value: datetime) -> datetime:
    """The engine keeps naive UTC everywhere; FHIR carries an explicit offset."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def patient_from_observation(observation: dict[str, Any]) -> str | None:
    reference = observation.get("subject", {}).get("reference", "")
    if reference.startswith("Patient/"):
        return reference.split("/", 1)[1]
    return None


def validate_vital_observation(observation: dict[str, Any]) -> list[str]:
    """Return a list of structural problems. Empty means valid.

    Checks the parts PS-03 §3.1 and the FHIR Compliance criterion care about:
    resource type, vital-signs category, LOINC coding, subject, effective time
    and a quantity or an explicit absent reason.
    """
    problems: list[str] = []
    if observation.get("resourceType") != "Observation":
        problems.append("resourceType must be Observation")
    if not observation.get("status"):
        problems.append("status is required")

    categories = [
        coding.get("code")
        for category in observation.get("category", [])
        for coding in category.get("coding", [])
    ]
    if "vital-signs" not in categories:
        problems.append("category must include vital-signs")

    loinc_codes = [
        coding.get("code")
        for coding in observation.get("code", {}).get("coding", [])
        if coding.get("system") == LOINC_SYSTEM
    ]
    if not loinc_codes:
        problems.append("code must carry a LOINC coding")
    elif not any(code in LOINC_TO_METRIC for code in loinc_codes):
        problems.append(f"LOINC code {loinc_codes} is not a supported vital sign")

    if not observation.get("subject", {}).get("reference"):
        problems.append("subject.reference is required")
    if not observation.get("effectiveDateTime"):
        problems.append("effectiveDateTime is required")

    has_value = "valueQuantity" in observation
    has_absent = "dataAbsentReason" in observation
    if not has_value and not has_absent:
        problems.append("either valueQuantity or dataAbsentReason is required")
    if has_value and has_absent:
        problems.append("valueQuantity and dataAbsentReason are mutually exclusive")
    if has_value:
        quantity = observation["valueQuantity"]
        if quantity.get("value") is None:
            problems.append("valueQuantity.value is required")
        if quantity.get("system") != UCUM_SYSTEM:
            problems.append("valueQuantity.system must be UCUM")
        if not quantity.get("code"):
            problems.append("valueQuantity.code (UCUM) is required")

    profiles = observation.get("meta", {}).get("profile", [])
    if VITALSIGNS_PROFILE not in profiles:
        problems.append("meta.profile should declare the vitalsigns profile")
    return problems