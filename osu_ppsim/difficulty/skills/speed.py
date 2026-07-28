"""Скилл Speed.

Источник: osu.Game.Rulesets.Osu/Difficulty/Skills/Speed.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

from typing import Final

from ...beatmap.objects import Slider
from ...mods import Mods
from ..evaluators import rhythm as rhythm_evaluator
from ..evaluators import speed as speed_evaluator
from ..hitobject import OsuDifficultyHitObject
from ..utils import logistic, pow_double, sequential_sum
from .base import HarmonicSkill

__all__ = ["Speed"]

_SKILL_MULTIPLIER: Final[float] = 1.16


class Speed(HarmonicSkill):
    """Скорость нажатий с поправкой на ритм."""

    harmonic_scale = 20.0
    decay_exponent = 0.9

    def __init__(self, mods: Mods) -> None:
        super().__init__(mods)
        self._current_strain = 0.0
        self._slider_strains: list[float] = []

    @staticmethod
    def _strain_decay(ms: float) -> float:
        return pow_double(0.3, ms / 1000)

    def _object_difficulty_of(self, current: OsuDifficultyHitObject) -> float:
        decay = self._strain_decay(current.adjusted_delta_time)

        self._current_strain *= decay
        self._current_strain += speed_evaluator.evaluate_difficulty_of(current) * (1 - decay) * _SKILL_MULTIPLIER

        current_rhythm = rhythm_evaluator.evaluate_difficulty_of(current)
        total_strain = self._current_strain * current_rhythm

        if isinstance(current.base_object, Slider):
            self._slider_strains.append(total_strain)

        return total_strain

    def relevant_object_count(self) -> float:
        if not self.object_difficulties:
            return 0.0

        max_strain = max(self.object_difficulties)
        if max_strain == 0:
            return 0.0

        return sequential_sum(logistic(s / max_strain, 0.5, 12.0) for s in self.object_difficulties)

    def count_top_weighted_sliders(self, difficulty_value: float) -> float:
        if not self._slider_strains:
            return 0.0
        if self.object_weight_sum == 0:
            return 0.0

        consistent_top_object = difficulty_value / self.object_weight_sum
        if consistent_top_object == 0:
            return 0.0

        return sequential_sum(logistic(s / consistent_top_object, 0.88, 10, 1.1) for s in self._slider_strains)
