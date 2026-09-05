"""Юнит-тесты LinUCB contextual bandit (модуль src/student/linucb.py)."""

import json

import numpy as np

from src.student.linucb import (
    BANDIT_DIM,
    DIFFICULTY_ORDER,
    arm_difficulty,
    bandit_select,
    bandit_update,
    build_features,
    difficulty_arm,
    make_bandit,
)


def test_make_bandit_shape():
    bandit = make_bandit()
    assert bandit["d"] == BANDIT_DIM
    assert bandit["alpha"] == 0.6
    assert len(bandit["arms"]) == 3
    for arm in bandit["arms"]:
        assert arm["n"] == 0
        assert arm["b"] == [0.0] * BANDIT_DIM
        a = np.asarray(arm["A"])
        assert a.shape == (BANDIT_DIM, BANDIT_DIM)
        np.testing.assert_array_equal(a, np.eye(BANDIT_DIM))


def test_difficulty_helpers_round_trip():
    assert [difficulty_arm(d) for d in DIFFICULTY_ORDER] == [0, 1, 2]
    assert [arm_difficulty(i) for i in range(3)] == DIFFICULTY_ORDER
    assert difficulty_arm("bogus") == 1
    assert arm_difficulty(-5) == "easy"
    assert arm_difficulty(99) == "hard"


def test_build_features_order_and_clamps():
    f = build_features(accuracy=0.8, attempts=3, fatigue=0.5, overall=0.7)
    assert f == [0.8, 0.3, 0.5, 0.7]
    # клампы
    f2 = build_features(accuracy=-1.0, attempts=100, fatigue=3.0, overall=2.0)
    assert f2 == [0.0, 1.0, 1.0, 1.0]
    # без попыток -> точность 0.5
    assert build_features(accuracy=0.0, attempts=0, fatigue=0.0, overall=0.5)[0] == 0.5


def test_select_cold_start_prefers_current_arm():
    bandit = make_bandit()
    features = build_features(accuracy=0.5, attempts=0, fatigue=0.0, overall=0.5)
    assert bandit_select(bandit, features, current_arm=1) == 1
    assert bandit_select(bandit, features, current_arm=2) == 2


def test_update_then_high_reward_arm_is_preferred():
    bandit = make_bandit()
    features = build_features(accuracy=0.9, attempts=2, fatigue=0.0, overall=0.6)
    for _ in range(10):
        bandit = bandit_update(bandit, features, arm=0, reward=1.0)
        bandit = bandit_update(bandit, features, arm=1, reward=0.0)
    assert bandit["arms"][0]["n"] == 10
    assert bandit["arms"][1]["n"] == 10
    # после перекоса arm=0 должен выигрывать чаще (детерминированный контекст)
    picks = [bandit_select(bandit, features) for _ in range(20)]
    assert picks.count(0) > picks.count(1)


def test_bandit_survives_json_round_trip():
    bandit = make_bandit()
    features = build_features(accuracy=0.6, attempts=1, fatigue=0.1, overall=0.5)
    bandit = bandit_update(bandit, features, arm=1, reward=1.0)
    restored = json.loads(json.dumps(bandit))
    assert bandit_select(restored, features, current_arm=1) in {0, 1, 2}
    bandit_update(restored, features, arm=1, reward=0.0)
    assert restored["arms"][1]["n"] == 2
