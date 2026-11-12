"""Build PS-03 discharge profiles from a Synthea population.

Synthea supplies the *clinical context* (conditions, medications, devices);
the wearable vitals come from the simulator and the BIDMC replay. This script
reads the FHIR bundles Synthea emits, derives the personalization flags and
writes one profile per selected patient into ``data/profiles/``.

Five archetypes are chosen so every personalization rule can be shown changing
a score or suppressing an alert:

    scale2_copd      COPD on home oxygen   -> SpO2 Scale 2 (target 88-92%)
    beta_blocker     beta-blockade         -> blunted pulse, corrected
    heart_failure    chronic heart failure -> stable comparator
    diabetes         type 2 diabetes       -> sensor-artifact suppression
    stable_recovery  no chronic disease    -> BIDMC real-data replay

Usage (from the repository root):

    python scripts/generate_profiles.py            # write data/profiles/*.json
    python scripts/generate_profiles.py --survey   # list candidates only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clinical.profiles import (  # noqa: E402
    BETA_BLOCKER_GENERICS,
    COPD_MARKERS,
    Condition,
    DischargeProfile,
    Medication,
    derive_flags,
)

FHIR_DIR = ROOT / "data" / "synthea" / "output" / "fhir"
PROFILE_DIR = ROOT / "data" / "profiles"
REFERENCE_DATE = date(2026, 11, 1)
DISCHARGE_DATE = "2026-11-05"

# Synthea records a lot of social and dental history. Only clinically relevant
# problems belong on a post-discharge monitoring profile.
CLINICAL_MARKERS = (
    "copd", "obstructive pulmonary", "emphysema", "chronic bronchitis", "asthma",
    "respiratory failure", "hypercapn", "pneumonia", "sepsis", "sleep apnea",
    "heart failure", "cardiac failure", "ischemic heart", "myocardial",
    "atrial fibrillation", "hypertension", "hyperlipidemia", "metabolic syndrome",
    "obesity", "diabetes mellitus", "chronic kidney disease", "anemia",
    "neuropathy", "retinopathy", "proteinuria", "microalbuminuria",
    "stroke", "cerebrovascular", "osteoporosis", "osteoarthritis", "carcinoma",
)

GENERIC_VOCAB = sorted(
    BETA_BLOCKER_GENERICS
    | {
        "nitroglycerin", "simvastatin", "atorvastatin", "clopidogrel", "prasugrel",
        "aspirin", "lisinopril", "losartan", "hydrochlorothiazide", "amlodipine",
        "furosemide", "digoxin", "warfarin", "insulin", "metformin", "albuterol",
        "fluticasone", "salmeterol", "budesonide", "naproxen", "ibuprofen",
        "acetaminophen", "hydrocodone", "codeine", "ferrous sulfate",
        "alendronic acid", "sertraline", "donepezil", "memantine", "tacrolimus",
        "verapamil", "alteplase", "diphenhydramine", "hydrocortisone", "levothyroxine",
    },
    key=len,
    reverse=True,
)

ARCHETYPE_SCENARIO = {
    "scale2_copd": "slow_deterioration",
    "beta_blocker": "slow_deterioration",
    "heart_failure": "stable_recovery",
    "diabetes": "sensor_artifact",
    "stable_recovery": "bidmc_replay",
}

PRIORITY_MARKERS = COPD_MARKERS + (
    "heart failure", "cardiac failure", "diabetes mellitus",
    "respiratory failure", "hypercapn",
)


@dataclass
class Candidate:
    patient_id: str
    name: str
    age_years: int
    sex: str
    deceased: bool
    conditions: tuple[Condition, ...]
    medications: tuple[Medication, ...]
    home_oxygen: bool
    flags: dict[str, Any]
    bundle: str

    @property
    def texts(self) -> list[str]:
        return [c.display.lower() for c in self.conditions]

    def has(self, *markers: str) -> bool:
        return any(any(m in t for m in markers) for t in self.texts)

    @property
    def richness(self) -> tuple[int, int, str]:
        return (len(self.conditions), len(self.medications), self.patient_id)

    def display_conditions(self, limit: int = 6) -> tuple[Condition, ...]:
        """Flag-driving problems first, so truncation never hides the evidence."""
        def priority_key(condition: Condition) -> int:
            text = condition.display.lower()
            return next(
                (i for i, m in enumerate(PRIORITY_MARKERS) if m in text), len(PRIORITY_MARKERS)
            )

        ordered = sorted(self.conditions, key=priority_key)
        return tuple(ordered[:limit])

    def display_medications(self, limit: int = 6) -> tuple[Medication, ...]:
        priority = [m for m in self.medications if m.generic.lower() in BETA_BLOCKER_GENERICS]
        rest = [m for m in self.medications if m not in priority]
        return tuple((priority + rest)[:limit])


def _clean_name(patient: dict[str, Any]) -> str:
    name = (patient.get("name") or [{}])[0]
    given = " ".join(name.get("given", []))
    family = name.get("family", "")
    # Synthea appends numeric disambiguators to every name token.
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", f"{given} {family}")).strip()


def _generic(display: str) -> str:
    text = display.lower()
    found = [g for g in GENERIC_VOCAB if re.search(rf"\b{re.escape(g)}", text)]
    if found:
        return "/".join(dict.fromkeys(found))
    return re.sub(r"[^a-z ]", "", text.split()[0]).strip() or text


def _coding(concept: dict[str, Any]) -> dict[str, str]:
    return (concept.get("coding") or [{}])[0]


def read_candidate(path: Path) -> Candidate | None:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    resources = [e["resource"] for e in bundle.get("entry", []) if "resource" in e]
    patient = next((r for r in resources if r["resourceType"] == "Patient"), None)
    if patient is None:
        return None

    conditions: dict[str, Condition] = {}
    for resource in resources:
        if resource["resourceType"] != "Condition":
            continue
        coding = _coding(resource.get("code", {}))
        display = coding.get("display", "")
        if not display or not any(m in display.lower() for m in CLINICAL_MARKERS):
            continue
        conditions[coding.get("code", display)] = Condition(
            code=coding.get("code", ""), display=display, system=coding.get("system", "")
        )

    medications: dict[str, Medication] = {}
    home_oxygen = False
    for resource in resources:
        kind = resource["resourceType"]
        if kind == "Device":
            # An oxygen concentrator is long-term support; inpatient "oxygen
            # administration by mask" is not evidence of chronic hypercapnia.
            if "oxygen" in _coding(resource.get("type", {})).get("display", "").lower():
                home_oxygen = True
        if kind != "MedicationRequest" or resource.get("status") != "active":
            continue
        concept = resource.get("medicationCodeableConcept")
        if not concept:
            continue
        coding = _coding(concept)
        display = coding.get("display", "")
        generic = _generic(display)
        medications.setdefault(
            generic,
            Medication(
                code=coding.get("code", ""),
                display=display,
                generic=generic,
                system=coding.get("system", ""),
            ),
        )

    ordered_conditions = tuple(sorted(conditions.values(), key=lambda c: c.display))
    ordered_medications = tuple(sorted(medications.values(), key=lambda m: m.generic))
    flags = derive_flags(ordered_conditions, ordered_medications, home_oxygen=home_oxygen)

    birth = patient.get("birthDate")
    age = 0
    if birth:
        born = date.fromisoformat(birth)
        age = REFERENCE_DATE.year - born.year - (
            (REFERENCE_DATE.month, REFERENCE_DATE.day) < (born.month, born.day)
        )

    return Candidate(
        patient_id=patient["id"],
        name=_clean_name(patient),
        age_years=age,
        sex=patient.get("gender", "unknown"),
        deceased=bool(patient.get("deceasedBoolean") or patient.get("deceasedDateTime")),
        conditions=ordered_conditions,
        medications=ordered_medications,
        home_oxygen=home_oxygen,
        flags=flags,
        bundle=path.name,
    )


def load_candidates() -> list[Candidate]:
    out = []
    for path in sorted(FHIR_DIR.glob("*.json")):
        candidate = read_candidate(path)
        if candidate is not None and not candidate.deceased:
            out.append(candidate)
    return out


def qualifies(candidate: Candidate, archetype: str) -> bool:
    if archetype == "scale2_copd":
        return bool(candidate.flags["hypercapnic_respiratory_failure"])
    if archetype == "beta_blocker":
        return candidate.flags["pulse_correction_bpm"] > 0
    if archetype == "heart_failure":
        return candidate.has("heart failure", "cardiac failure")
    if archetype == "diabetes":
        return candidate.has("diabetes mellitus")
    if archetype == "stable_recovery":
        return (
            not candidate.flags["hypercapnic_respiratory_failure"]
            and candidate.flags["pulse_correction_bpm"] == 0
            and not candidate.has("heart failure", "cardiac failure", "diabetes mellitus")
            and len(candidate.medications) <= 2
        )
    raise ValueError(f"unknown archetype {archetype!r}")


def rank(candidate: Candidate, archetype: str) -> tuple:
    """Richer clinical context makes a more convincing demo; ties are deterministic.

    Each archetype prefers a patient where *only* its own rule is active, so
    the effect shown on screen cannot be confounded by a second rule.
    """
    bonus = 0
    if archetype == "scale2_copd":
        bonus += 2 if candidate.home_oxygen else 0
        bonus += 1 if candidate.flags["pulse_correction_bpm"] == 0 else 0
    if archetype == "beta_blocker":
        bonus += 1 if candidate.has("heart failure", "ischemic heart") else 0
        bonus += 1 if not candidate.flags["hypercapnic_respiratory_failure"] else 0
    return (bonus, *candidate.richness)


def select(candidates: list[Candidate]) -> dict[str, Candidate]:
    chosen: dict[str, Candidate] = {}
    used: set[str] = set()
    for archetype in ARCHETYPE_SCENARIO:
        pool = [c for c in candidates if c.patient_id not in used and qualifies(c, archetype)]
        if not pool:
            continue
        best = max(pool, key=lambda c: rank(c, archetype))
        chosen[archetype] = best
        used.add(best.patient_id)
    return chosen


def build_profile(archetype: str, candidate: Candidate) -> DischargeProfile:
    flags = candidate.flags
    notes = [f"Synthea bundle {candidate.bundle} (population seed 42)."]
    if flags["hypercapnic_respiratory_failure"]:
        evidence = (
            "home oxygen concentrator on record"
            if candidate.home_oxygen
            else "documented chronic respiratory failure"
        )
        notes.append(
            f"SpO₂ Scale 2 (target 88–92%): {evidence} alongside "
            f"{next((c.display for c in candidate.conditions if any(m in c.display.lower() for m in COPD_MARKERS)), 'COPD')}."
        )
    if flags["beta_blocker_medication"]:
        notes.append(
            f"Pulse correction +{flags['pulse_correction_bpm']:g} bpm: "
            f"{flags['beta_blocker_medication']} blunts the heart-rate response, "
            "so a normal pulse is not reassurance."
        )
    if archetype == "stable_recovery":
        notes.append("No chronic cardiorespiratory disease; used for the BIDMC real-data replay.")

    return DischargeProfile(
        patient_id=candidate.patient_id,
        display_name=candidate.name,
        age_years=candidate.age_years,
        sex=candidate.sex,
        discharged_on=DISCHARGE_DATE,
        conditions=candidate.display_conditions(),
        medications=candidate.display_medications(),
        hypercapnic_respiratory_failure=flags["hypercapnic_respiratory_failure"],
        spo2_target=flags["spo2_target"],
        pulse_correction_bpm=flags["pulse_correction_bpm"],
        notes=" ".join(notes),
        source="synthea",
        scenario=ARCHETYPE_SCENARIO[archetype],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey", action="store_true", help="list candidates without writing profiles")
    args = parser.parse_args()

    if not FHIR_DIR.is_dir():
        print(f"no Synthea output at {FHIR_DIR}; run Synthea first (see README).")
        return 1
    candidates = load_candidates()
    print(f"{len(candidates)} living candidates from {FHIR_DIR}")

    if args.survey:
        for archetype in ARCHETYPE_SCENARIO:
            pool = sorted(
                (c for c in candidates if qualifies(c, archetype)),
                key=lambda c: rank(c, archetype),
                reverse=True,
            )
            print(f"\n{archetype}: {len(pool)} candidates")
            for c in pool[:5]:
                print(f"  {c.name} ({c.age_years}{c.sex[0].upper()}) oxygen={c.home_oxygen}")
                print(f"    conditions: {', '.join(x.display for x in c.display_conditions())}")
                print(f"    meds: {', '.join(x.generic for x in c.display_medications())}")
        return 0

    chosen = select(candidates)
    missing = [a for a in ARCHETYPE_SCENARIO if a not in chosen]
    if missing:
        print(f"WARNING: no candidate for {', '.join(missing)}; generate a larger population.")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for stale in PROFILE_DIR.glob("p[0-9]_*.json"):
        stale.unlink()

    for index, (archetype, candidate) in enumerate(chosen.items(), start=1):
        profile = build_profile(archetype, candidate)
        path = PROFILE_DIR / f"p{index}_{archetype}.json"
        path.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\n{path.name}  {profile.display_name}  {profile.age_years}{profile.sex[0].upper()}")
        print(f"  scenario={profile.scenario}  spo2_scale={profile.spo2_scale}  pulse_correction={profile.pulse_correction_bpm:g}")
        print(f"  conditions: {', '.join(c.display for c in profile.conditions)}")
        print(f"  medications: {', '.join(m.display for m in profile.medications)}")
        for rule in profile.rules():
            print(f"  rule {rule.rule_id}: {rule.profile_fact}")

    print(f"\nwrote {len(chosen)} profiles to {PROFILE_DIR}")
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())