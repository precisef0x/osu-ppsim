"""Скилл Reading — новый в ребалансе 2026.

Источник: osu.Game.Rulesets.Osu/Difficulty/Skills/Reading.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Именно этого скилла нет в rosu-pp: он заменил прежние бонусы за AR и HD.
"""

from __future__ import annotations

import math
from typing import Final

from ...mods import Mods
from ..evaluators import reading as reading_evaluator
from ..hitobject import OsuDifficultyHitObject
from ..utils import lerp, logistic, pow_double, sequential_sum
from .base import HarmonicSkill

__all__ = ["Reading"]

_SKILL_MULTIPLIER: Final[float] = 2.5
#: Первую минуту карты игрок успевает запомнить, поэтому её вклад занижается.
_REDUCED_DIFFICULTY_DURATION: Final[float] = 60 * 1000


class Reading(HarmonicSkill):
    def __init__(self, mods: Mods) -> None:
        super().__init__(mods)
        self._has_hidden_mod = mods.hidden
        self._current_strain = 0.0
        self._reduced_note_count = 0.0
        self._reduced_duration: float | None = None

    @staticmethod
    def _strain_decay(ms: float) -> float:
        return pow_double(0.8, ms / 1000)

    def _object_difficulty_of(self, current: OsuDifficultyHitObject) -> float:
        decay = self._strain_decay(current.delta_time)

        self._current_strain *= decay
        self._current_strain += self._calculate_adjusted_difficulty(current) * (1 - decay) * _SKILL_MULTIPLIER

        # Расчёт опирается на то, что объекты приходят по одному и по порядку:
        # тогда первый вызов задаёт границу, а счётчик растёт до первого объекта
        # за её пределами.
        if self._reduced_duration is None:
            self._reduced_duration = current.start_time + _REDUCED_DIFFICULTY_DURATION

        if current.start_time <= self._reduced_duration:
            self._reduced_note_count += 1

        return self._current_strain

    def _calculate_adjusted_difficulty(self, current: OsuDifficultyHitObject) -> float:
        difficulty = reading_evaluator.evaluate_difficulty_of(current, self._has_hidden_mod)

        # Ветки TouchDevice, Magnetised, Relax и Autopilot опущены: эти моды
        # вне охвата и отсекаются на входе (см. osu_ppsim.mods).

        difficulty *= 0.825 + pow_double(max(0.0, current.overall_difficulty), 2.2) / 1125.0

        return difficulty

    def _get_transformed_difficulties(self, difficulties: list[float]) -> list[float]:
        """Занижает вклад первых объектов: их игрок успевает запомнить.

        Возвращается НОВЫЙ список — исходные object_difficulties не меняются,
        как и в C#, где Where(...).ToList() создаёт копию.
        """
        difficulties = [v for v in difficulties if v > 0]

        # Первые секунды считаются выученными полностью.
        reduced_difficulty_base_line = 0.0

        i = 0
        while i < len(difficulties) and i < self._reduced_note_count:
            scale = math.log10(lerp(1, 10, min(max(i / self._reduced_note_count, 0.0), 1.0)))
            difficulties[i] *= lerp(reduced_difficulty_base_line, 1.0, scale)
            i += 1

        return difficulties

    def count_top_weighted_object_difficulties(self, difficulty_value: float) -> float:
        """Переопределено: у Reading другие константы логистической кривой."""
        if not self.object_difficulties:
            return 0.0
        if self.object_weight_sum == 0:
            return 0.0

        consistent_top_note = difficulty_value / self.object_weight_sum
        if consistent_top_note == 0:
            return 0.0

        return sequential_sum(logistic(d / consistent_top_note, 1.15, 5, 1.1) for d in self.object_difficulties)
