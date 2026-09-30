"""보정 지표 (P1-11, 06 §6.1). 손계산 fixture와 일치해야 한다."""

from __future__ import annotations

import pytest

from quantpilot.judgment.calibration import (
    brier,
    bucket_hit_rates,
    calibration,
    ece,
    is_monotonic,
    samples,
)

# (confidence, 24h 후 방향 적중) — 손계산 fixture
ROWS = [
    (0.55, True),
    (0.62, False),
    (0.68, True),
    (0.75, True),
    (0.80, False),
    (0.85, True),
    (0.92, True),
    (0.95, True),
    (0.30, False),
    (0.45, True),
]
# Brier: (.45²+.62²+.32²+.25²+.8²+.15²+.08²+.05²+.3²+.55²)/10
#      = (.2025+.3844+.1024+.0625+.64+.0225+.0064+.0025+.09+.3025)/10 = 1.8157/10
BRIER = 0.18157
# ECE(10구간): [.3,.4) .30 vs 0 → .30 | [.4,.5) .45 vs 1 → .55 | [.5,.6) .55 vs 1 → .45
#   [.6,.7) avg .65 vs .5 → .15×2 | [.7,.8) .75 vs 1 → .25 | [.8,.9) avg .825 vs .5 → .325×2
#   [.9,1] avg .935 vs 1 → .065×2   합 2.63 / 10
ECE = 0.263


def _judgments(rows=ROWS, provider="jev"):
    return [
        {
            "confidence": p,
            "direction_hit": hit,
            "realized_ret_24h": 0.01 if hit else -0.01,
            "provider": provider,
            "answers": {"signal_quality": p},
        }
        for p, hit in rows
    ]


def test_brier_matches_hand_computed_fixture():
    assert brier(_judgments()) == pytest.approx(BRIER, abs=1e-12)


def test_ece_matches_hand_computed_fixture():
    assert ece(_judgments(), bins=10) == pytest.approx(ECE, abs=1e-12)


def test_bucket_hit_rates_match_hand_computed_fixture():
    b = bucket_hit_rates(_judgments())
    assert [x.label for x in b] == ["0.5~0.7", "0.7~0.9", "0.9+"]
    assert [x.n for x in b] == [3, 3, 2]
    assert b[0].hit_rate == pytest.approx(2 / 3)
    assert b[0].avg_conf == pytest.approx((0.55 + 0.62 + 0.68) / 3)
    assert b[1].hit_rate == pytest.approx(2 / 3)
    assert b[2].hit_rate == 1.0 and b[2].avg_conf == pytest.approx(0.935)
    assert is_monotonic(b)


def test_non_monotonic_buckets_flag_bad_calibration():
    rows = [(0.6, True), (0.65, True), (0.8, False), (0.85, True), (0.95, False)]
    assert not is_monotonic(bucket_hit_rates(_judgments(rows)))


def test_rows_without_realized_return_are_excluded():
    js = _judgments() + [{"confidence": 0.99, "direction_hit": None, "realized_ret_24h": None}]
    assert len(samples(js)) == 10
    assert brier(js) == pytest.approx(BRIER)


def test_direction_hit_falls_back_to_return_sign():
    js = [
        {"confidence": 0.8, "realized_ret_24h": 0.02},
        {"confidence": 0.8, "realized_ret_24h": -0.01},
    ]
    assert brier(js) == pytest.approx((0.2**2 + 0.8**2) / 2)


def test_empty_input_gives_none_not_zero():
    assert brier([]) is None and ece([]) is None
    assert calibration([])["n"] == 0


def test_signal_quality_can_be_the_probability():
    js = _judgments()
    assert brier(js, p="signal_quality") == pytest.approx(BRIER)
    with pytest.raises(ValueError):
        brier(js, p="size")


def test_calibration_summary_has_api_shape_and_per_provider_split():
    js = _judgments() + _judgments([(0.9, False), (0.9, False)], provider="laya")
    out = calibration(js)
    assert out["n"] == 12
    assert set(out) == {"brier", "ece", "n", "buckets", "monotonic", "by_provider"}
    assert out["by_provider"]["jev"]["brier"] == pytest.approx(BRIER)
    assert out["by_provider"]["laya"] == {
        "brier": pytest.approx(0.81),
        "ece": pytest.approx(0.9),
        "n": 2,
    }
    assert out["buckets"][2] == {
        "range": "0.9+",
        "n": 4,
        "hit_rate": 0.5,
        "avg_conf": pytest.approx(0.9175),
    }
