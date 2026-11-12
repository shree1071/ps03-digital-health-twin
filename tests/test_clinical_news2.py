"""NEWS2 band and tier tests against the RCP chart values."""

from __future__ import annotations

import pytest

from clinical.news2 import (
    Consciousness,
    News2Input,
    Tier,
    score_news2,
    tier_for,
)


def normal(**overrides) -> News2Input:
    base = dict(
        respiratory_rate=16.0,
        spo2=97.0,
        temperature=36.8,
        heart_rate=70.0,
        systolic_bp=120.0,
        consciousness=Consciousness.ALERT,
        supplemental_oxygen=False,
        spo2_scale=1,
    )
    base.update(overrides)
    return News2Input(**base)


def test_all_normal_scores_zero():
    result = score_news2(normal())
    assert result.total == 0
    assert result.tier is Tier.NORMAL
    assert result.complete


@pytest.mark.parametrize(
    "rr,expected",
    [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3), (30, 3)],
)
def test_respiratory_rate_bands(rr, expected):
    result = score_news2(normal(respiratory_rate=rr))
    param = next(p for p in result.parameters if p.parameter == "respiratory_rate")
    assert param.score == expected


@pytest.mark.parametrize(
    "spo2,expected",
    [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (99, 0)],
)
def test_spo2_scale1_bands(spo2, expected):
    result = score_news2(normal(spo2=spo2))
    param = next(p for p in result.parameters if p.parameter == "spo2")
    assert param.score == expected
    assert param.scale == 1


@pytest.mark.parametrize("supplemental_oxygen", [False, True], ids=["air", "oxygen"])
@pytest.mark.parametrize(
    "spo2,expected",
    [(83, 3), (84, 2), (85, 2), (86, 1), (87, 1), (88, 0), (90, 0), (92, 0),
     (93, 1), (94, 1), (95, 2), (96, 2), (97, 3), (98, 3)],
)
def test_spo2_scale2_bands(spo2, expected, supplemental_oxygen):
    result = score_news2(normal(
        spo2=spo2, spo2_scale=2, supplemental_oxygen=supplemental_oxygen,
    ))
    param = next(p for p in result.parameters if p.parameter == "spo2")
    oxygen = next(p for p in result.parameters if p.parameter == "supplemental_oxygen")
    expected_spo2 = 0 if spo2 >= 93 and not supplemental_oxygen else expected
    assert param.score == expected_spo2
    assert param.scale == 2
    assert oxygen.score == (2 if supplemental_oxygen else 0)
    assert result.total == expected_spo2 + oxygen.score
    assert result.single_parameter_three == (expected_spo2 == 3)
    if spo2 >= 93 and not supplemental_oxygen:
        assert param.band == "≥93 on air (Scale 2)"


def test_scale2_target_range_is_normal_where_scale1_is_severe():
    """The core personalization win: 90% is a 3 on Scale 1 but a 0 on Scale 2."""
    scale1 = score_news2(normal(spo2=90.0, spo2_scale=1))
    scale2 = score_news2(normal(spo2=90.0, spo2_scale=2))
    s1 = next(p for p in scale1.parameters if p.parameter == "spo2")
    s2 = next(p for p in scale2.parameters if p.parameter == "spo2")
    assert s1.score == 3
    assert s2.score == 0
    assert scale1.tier is Tier.WATCH  # single-parameter-three rule
    assert scale2.tier is Tier.NORMAL


def test_scale2_above_target_scores_zero_on_air_not_oxygen():
    on_o2 = score_news2(normal(spo2=98.0, spo2_scale=2, supplemental_oxygen=True))
    param = next(p for p in on_o2.parameters if p.parameter == "spo2")
    assert param.score == 3
    assert on_o2.total == 5  # SpO2 3 plus supplemental oxygen 2.
    assert on_o2.tier is Tier.REVIEW
    on_air = score_news2(normal(spo2=98.0, spo2_scale=2, supplemental_oxygen=False))
    param_air = next(p for p in on_air.parameters if p.parameter == "spo2")
    assert param_air.score == 0
    assert on_air.total == 0
    assert on_air.tier is Tier.NORMAL


@pytest.mark.parametrize(
    "temp,expected",
    [(35.0, 3), (35.5, 1), (36.0, 1), (36.5, 0), (38.0, 0), (38.5, 1), (39.0, 1), (39.5, 2)],
)
def test_temperature_bands(temp, expected):
    result = score_news2(normal(temperature=temp))
    param = next(p for p in result.parameters if p.parameter == "temperature")
    assert param.score == expected


@pytest.mark.parametrize(
    "hr,expected",
    [(40, 3), (45, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2), (130, 2), (131, 3)],
)
def test_pulse_bands(hr, expected):
    result = score_news2(normal(heart_rate=hr))
    param = next(p for p in result.parameters if p.parameter == "heart_rate")
    assert param.score == expected


@pytest.mark.parametrize(
    "bp,expected",
    [(90, 3), (95, 2), (100, 2), (105, 1), (110, 1), (111, 0), (219, 0), (220, 3)],
)
def test_systolic_bp_bands(bp, expected):
    result = score_news2(normal(systolic_bp=bp))
    param = next(p for p in result.parameters if p.parameter == "systolic_bp")
    assert param.score == expected


def test_confusion_scores_three():
    result = score_news2(normal(consciousness=Consciousness.CONFUSION))
    param = next(p for p in result.parameters if p.parameter == "consciousness")
    assert param.score == 3


def test_supplemental_oxygen_scores_two():
    result = score_news2(normal(supplemental_oxygen=True))
    param = next(p for p in result.parameters if p.parameter == "supplemental_oxygen")
    assert param.score == 2


def test_single_parameter_three_escalates_to_watch():
    result = score_news2(normal(respiratory_rate=26.0))
    assert result.total == 3
    assert result.single_parameter_three
    assert result.tier is Tier.WATCH


def test_aggregate_five_is_review():
    result = score_news2(normal(respiratory_rate=22.0, spo2=93.0, temperature=38.5))
    assert result.total == 5
    assert result.tier is Tier.REVIEW


def test_aggregate_seven_is_urgent():
    result = score_news2(normal(respiratory_rate=26.0, spo2=90.0, heart_rate=135.0))
    assert result.total == 9
    assert result.tier is Tier.URGENT


@pytest.mark.parametrize(
    "total,single_three,tier",
    [
        (0, False, Tier.NORMAL),
        (0, True, Tier.WATCH),
        (1, False, Tier.WATCH),
        (4, False, Tier.WATCH),
        (5, False, Tier.REVIEW),
        (6, False, Tier.REVIEW),
        (7, False, Tier.URGENT),
        (12, False, Tier.URGENT),
    ],
)
def test_tier_mapping(total, single_three, tier):
    assert tier_for(total, single_three)[0] is tier


def test_missing_mandatory_vitals_are_reported_not_imputed():
    result = score_news2(News2Input(heart_rate=70.0))
    assert not result.complete
    assert set(result.missing) == {"respiratory_rate", "spo2", "temperature"}
    assert all(p.parameter != "spo2" for p in result.parameters)


def test_optional_bp_absence_does_not_make_score_incomplete():
    result = score_news2(normal(systolic_bp=None))
    assert result.complete
    assert "systolic_bp" not in result.missing


def test_scoring_is_deterministic():
    vitals = normal(respiratory_rate=22.0, spo2=93.0)
    assert score_news2(vitals).to_dict() == score_news2(vitals).to_dict()