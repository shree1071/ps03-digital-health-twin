"""Profile flags require documented diagnoses, not COPD or oxygen shortcuts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clinical.profiles import Condition, DischargeProfile, Medication, derive_flags
from scripts.generate_profiles import (
    ARCHETYPE_SCENARIO,
    Candidate,
    build_profile,
    qualifies,
    read_candidate,
    select,
)

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "profiles"


def make_candidate(displays, *, home_oxygen=False, medications=()):
    conditions = tuple(Condition(str(i), display) for i, display in enumerate(displays))
    return Candidate(
        patient_id="test-patient",
        name="Test Patient",
        age_years=62,
        sex="male",
        deceased=False,
        conditions=conditions,
        medications=medications,
        home_oxygen=home_oxygen,
        flags=derive_flags(conditions, medications),
        bundle="test-patient.json",
    )


@pytest.mark.parametrize("displays", [
    (),
    ("Pulmonary emphysema (disorder)",),
    ("Chronic respiratory failure (disorder)",),
    ("Respiratory failure (disorder)",),
    ("Hypercapnia (finding)",),
    ("Chronic obstructive pulmonary disease", "Long-term oxygen therapy"),
    ("COPD", "Chronic respiratory failure (disorder)"),
    ("COPD", "Acute respiratory failure with hypoxia"),
])
def test_nonspecific_conditions_do_not_enable_scale2(displays):
    candidate = make_candidate(displays)
    assert candidate.flags["hypercapnic_respiratory_failure"] is False
    assert candidate.flags["spo2_target"] == (94.0, 98.0)
    profile = build_profile("scale2_copd", candidate)
    assert profile.spo2_scale == 1
    assert not any(rule.rule_id == "spo2_scale_2" for rule in profile.rules())
    assert profile.conditions == candidate.conditions


@pytest.mark.parametrize("diagnosis", [
    "Chronic hypercapnic respiratory failure (disorder)",
    "Acute and chronic respiratory failure with hypercapnia",
    "Type 2 respiratory failure (disorder)",
    "Type II respiratory failure (disorder)",
])
@pytest.mark.parametrize("home_oxygen", [False, True])
def test_explicit_failure_enables_scale2_and_supplies_the_fact(diagnosis, home_oxygen):
    candidate = make_candidate(("COPD", diagnosis), home_oxygen=home_oxygen)
    assert candidate.flags["hypercapnic_respiratory_failure"] is True
    assert candidate.flags["spo2_target"] == (88.0, 92.0)
    profile = build_profile("scale2_copd", candidate)
    rule = next(rule for rule in profile.rules() if rule.rule_id == "spo2_scale_2")
    assert profile.spo2_scale == 2
    assert rule.profile_fact == f"Condition: {diagnosis} (SNOMED 1)"
    assert diagnosis in profile.notes
    assert "alongside COPD" not in profile.notes
    assert profile.conditions == candidate.conditions


def test_hypercapnic_fact_does_not_use_copd_as_evidence():
    profile = build_profile("scale2_copd", make_candidate(("COPD",)))
    profile.hypercapnic_respiratory_failure = True
    rule = next(rule for rule in profile.rules() if rule.rule_id == "spo2_scale_2")
    assert "no supporting condition documented" in rule.profile_fact
    assert "Condition: COPD" not in rule.profile_fact


@pytest.mark.parametrize("home_oxygen", [False, True])
def test_legacy_copd_archetype_does_not_require_or_claim_scale2(home_oxygen):
    candidate = make_candidate(("Pulmonary emphysema (disorder)",), home_oxygen=home_oxygen)
    assert qualifies(candidate, "scale2_copd")
    assert select([candidate])["scale2_copd"] is candidate
    profile = build_profile("scale2_copd", candidate)
    assert profile.spo2_scale == 1
    assert profile.scenario == "slow_deterioration"
    assert "no documented hypercapnic respiratory failure" in profile.notes.lower()
    assert "Scale 1" in profile.notes
    assert "population seed 42" not in profile.notes
    assert "seed not recorded" in profile.notes


def test_legacy_copd_archetype_rejects_unrelated_conditions():
    assert not qualifies(make_candidate(("Essential hypertension",)), "scale2_copd")


def test_read_candidate_does_not_infer_failure_from_concentrator(tmp_path):
    path = tmp_path / "synthetic-test-bundle.json"
    path.write_text(json.dumps({"entry": [
        {"resource": {
            "resourceType": "Patient", "id": "test-patient",
            "name": [{"given": ["Test123"], "family": "Patient456"}],
            "birthDate": "1964-01-01", "gender": "male",
        }},
        {"resource": {
            "resourceType": "Condition",
            "code": {"coding": [{"code": "87433001", "display": "Pulmonary emphysema (disorder)"}]},
        }},
        {"resource": {
            "resourceType": "Device",
            "type": {"coding": [{"display": "Oxygen concentrator"}]},
        }},
    ]}), encoding="utf-8")
    candidate = read_candidate(path)
    assert candidate is not None
    assert candidate.home_oxygen is True
    assert candidate.flags["hypercapnic_respiratory_failure"] is False
    assert build_profile("scale2_copd", candidate).spo2_scale == 1
    assert len(candidate.conditions) == 1


def test_explicit_failure_evidence_survives_display_truncation():
    candidate = make_candidate((
        "COPD", "Pulmonary emphysema", "Chronic bronchitis",
        "Chronic obstructive pulmonary disease", "Chronic obstructive lung disease",
        "COPD exacerbation", "Type 2 respiratory failure",
    ))
    profile = build_profile("scale2_copd", candidate)
    assert len(profile.conditions) == 6
    assert "Type 2 respiratory failure" in [c.display for c in profile.conditions]
    assert "Condition: Type 2 respiratory failure" in profile._hypercapnic_fact()


def test_beta_blocker_flags_remain_unchanged():
    medication = Medication("866412", "metoprolol 100 MG Oral Tablet", "metoprolol")
    flags = derive_flags((), (medication,))
    assert flags["pulse_correction_bpm"] == 10.0
    assert flags["beta_blocker_medication"] == medication.display
    assert flags["hypercapnic_respiratory_failure"] is False


def test_stable_recovery_notes_do_not_deny_documented_chronic_disease():
    candidate = make_candidate(("Essential hypertension", "Obstructive sleep apnea syndrome"))
    assert qualifies(candidate, "stable_recovery")
    profile = build_profile("stable_recovery", candidate)
    assert "No chronic cardiorespiratory disease" not in profile.notes
    assert "chronic conditions retained as documented" in profile.notes
    assert profile.scenario == "bidmc_replay"


def test_existing_five_profiles_keep_patients_scenarios_and_consistent_rules():
    expected = {
        "p1_scale2_copd.json": ("Jarod Kraig Runte", "slow_deterioration"),
        "p2_beta_blocker.json": ("Evette Macejkovic", "slow_deterioration"),
        "p3_heart_failure.json": ("Todd Carlton Grant", "stable_recovery"),
        "p4_diabetes.json": ("Anibal Sidney Tromp", "sensor_artifact"),
        "p5_stable_recovery.json": ("Catrice Loma Nader", "bidmc_replay"),
    }
    paths = sorted(PROFILE_DIR.glob("*.json"))
    assert {path.name for path in paths} == set(expected)
    assert len(paths) == len(ARCHETYPE_SCENARIO) == 5
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = DischargeProfile.from_dict(data)
        assert (profile.display_name, profile.scenario) == expected[path.name]
        assert data["rules"] == [rule.to_dict() for rule in profile.rules()]
        assert data["spo2_scale"] == profile.spo2_scale
        assert "population seed 42" not in profile.notes
        flags = derive_flags(profile.conditions, profile.medications)
        assert profile.hypercapnic_respiratory_failure == flags["hypercapnic_respiratory_failure"]
        assert profile.spo2_target == flags["spo2_target"]


def test_jarod_profile_uses_scale1_without_an_invented_diagnosis():
    data = json.loads((PROFILE_DIR / "p1_scale2_copd.json").read_text(encoding="utf-8"))
    profile = DischargeProfile.from_dict(data)
    assert profile.hypercapnic_respiratory_failure is False
    assert profile.spo2_scale == 1
    assert profile.spo2_target == (94.0, 98.0)
    assert [c.code for c in profile.conditions] == ["87433001", "59621000", "201834006"]
    assert "no documented hypercapnic respiratory failure" in profile.notes.lower()
    assert "oxygen concentrator" in profile.notes
    assert [rule.rule_id for rule in profile.rules()] == ["rolling_baseline"]


def test_catrice_notes_acknowledge_hypertension_and_sleep_apnea():
    data = json.loads((PROFILE_DIR / "p5_stable_recovery.json").read_text(encoding="utf-8"))
    assert "No chronic cardiorespiratory disease" not in data["notes"]
    assert "hypertension" in data["notes"].lower()
    assert "sleep apnea" in data["notes"].lower()
