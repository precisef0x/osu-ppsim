"""Скилл Flashlight.

Источник: osu.Game.Rulesets.Osu/Difficulty/Skills/Flashlight.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

from typing import Final

from ...mods import Mods
from ..evaluators import flashlight as flashlight_evaluator
from ..hitobject import OsuDifficultyHitObject
from ..utils import pow_double, pow_int, sequential_sum
from .base import StrainSkill

__all__ = ["Flashlight"]

_SKILL_MULTIPLIER: Final[float] = 0.058


class Flashlight(StrainSkill):
    """Единственный из четырёх скиллов, работающий на обычных страйнах."""

    def __init__(self, mods: Mods, total_objects: int) -> None:
        super().__init__(mods)
        self._total_objects = total_objects
        self._current_strain = 0.0

    @staticmethod
    def _strain_decay(ms: float) -> float:
        return pow_double(0.15, ms / 1000)

    def _calculate_initial_strain(self, time: float, current: OsuDifficultyHitObject) -> float:
        # Previous(0) здесь всегда есть: секция первого объекта выставляется так,
        # что цикл добивки для него не запускается.
        return self._current_strain * self._strain_decay(time - current.previous(0).start_time)

    def _strain_value_at(self, current: OsuDifficultyHitObject) -> float:
        if not self.mods.flashlight:
            return 0.0

        self._current_strain *= self._strain_decay(current.delta_time)
        self._current_strain += self._calculate_adjusted_difficulty(current) * _SKILL_MULTIPLIER

        return self._current_strain

    def _calculate_adjusted_difficulty(self, current: OsuDifficultyHitObject) -> float:
        difficulty = flashlight_evaluator.evaluate_difficulty_of(current, self.mods)

        # Ветки TouchDevice, Magnetised, Deflate, Relax и Autopilot опущены:
        # эти моды вне охвата и отсекаются на входе (см. osu_ppsim.mods).

        difficulty *= 0.985 + pow_int(max(0.0, current.overall_difficulty), 2) / 4000

        return difficulty

    def difficulty_value(self) -> float:
        total = sequential_sum(self.get_current_strain_peaks())

        # У коротких карт доля объектов с малым радиусом фонарика выше.
        total *= (
            0.7
            + 0.1 * min(1.0, self._total_objects / 200.0)
            + (0.2 * min(1.0, (self._total_objects - 200) / 200.0) if self._total_objects > 200 else 0.0)
        )

        return total

    @staticmethod
    def difficulty_to_performance(difficulty: float) -> float:
        """Flashlight.DifficultyToPerformance. Степень записана целым литералом."""
        return 25 * pow_int(difficulty, 2)
