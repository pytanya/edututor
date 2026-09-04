"""Тесты адаптивной логики."""

import pytest

from src.student.adaptive import apply_delta, overall_level, pick_recommendation


def test_apply_delta_clamps():
    assert apply_delta(0.8, 0.5) == 1.0
    assert apply_delta(0.2, -0.5) == 0.0
    assert apply_delta(0.5, 0.3) == pytest.approx(0.8)


def test_overall_level_empty_and_mean():
    assert overall_level([]) == 0.5
    topics = [{"level": 0.6}, {"level": 1.0}, {"level": 0.2}]
    assert overall_level(topics) == pytest.approx(0.6)


def test_pick_recommendation_prefers_model():
    topics = [{"topic": "a", "level": 0.2}, {"topic": "b", "level": 0.9}]
    assert pick_recommendation(topics, next_from_model="теорема виета") == "теорема виета"


def test_pick_recommendation_weakest_topic():
    topics = [{"topic": "a", "level": 0.9}, {"topic": "b", "level": 0.3}]
    assert pick_recommendation(topics) == "b"


def test_pick_recommendation_empty():
    assert pick_recommendation([]) is None
