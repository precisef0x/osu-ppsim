"""Скилл Aim.

Источник: osu.Game.Rulesets.Osu/Difficulty/Skills/Aim.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Создаётся дважды — со слайдерами и без; отношение двух результатов даёт
SliderFactor в итоговых атрибутах.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Final

from ...beatmap.objects import Slider
from ...mods import Mods
from ..evaluators import aim as aim_evaluator
from ..hitobject import OsuDifficultyHitObject
from ..utils import c_div, lerp, logistic, logistic_exp, norm, pow_double, pow_int, sequential_sum
from .base import StrainPeak, VariableLengthStrainSkill

__all__ = ["Aim"]

_SKILL_MULTIPLIER_SNAP: Final[float] = 70.9
_SKILL_MULTIPLIER_AGILITY: Final[float] = 2.35
_SKILL_MULTIPLIER_FLOW: Final[float] = 242.0
_SKILL_MULTIPLIER_TOTAL: Final[float] = 1.12
_COMBINED_SNAP_NORM_EXPONENT: Final[float] = 1.2

_REDUCED_SECTION_TIME: Final[int] = 4000
_REDUCED_STRAIN_BASELINE: Final[float] = 0.727
_CHUNK_SIZE: Final[int] = 20


class Aim(VariableLengthStrainSkill):
    def __init__(self, mods: Mods, include_sliders: bool) -> None:
        super().__init__(mods)
        self.include_sliders = include_sliders
        self._current_strain = 0.0
        self._slider_strains: list[float] = []

    @staticmethod
    def _strain_decay(ms: float) -> float:
        return pow_double(0.2, ms / 1000)

    def _calculate_initial_strain(self, time: float, current: OsuDifficultyHitObject) -> float:
        # Previous(0) здесь всегда есть: метод вызывается только при добивке
        # пропущенных секций, а она возможна лишь начиная со второго объекта.
        return self._current_strain * self._strain_decay(time - current.previous(0).start_time)

    def _strain_value_at(self, current: OsuDifficultyHitObject) -> float:
        decay = self._strain_decay(current.adjusted_delta_time)

        self._current_strain *= decay
        self._current_strain += self._calculate_adjusted_difficulty(current) * (1 - decay)

        if isinstance(current.base_object, Slider):
            self._slider_strains.append(self._current_strain)

        return self._current_strain

    def _calculate_adjusted_difficulty(self, current: OsuDifficultyHitObject) -> float:
        snap = aim_evaluator.evaluate_snap(current, self.include_sliders) * _SKILL_MULTIPLIER_SNAP
        agility = aim_evaluator.evaluate_agility(current) * _SKILL_MULTIPLIER_AGILITY
        flow = aim_evaluator.evaluate_flow(current, self.include_sliders) * _SKILL_MULTIPLIER_FLOW

        total = self._calculate_total_value(snap, agility, flow)

        # Ветки Magnetised, TouchDevice и Relax опущены: моды вне охвата.

        total *= 0.985 + pow_int(max(0.0, current.overall_difficulty), 2) / 4000

        return total

    def _calculate_total_value(self, snap: float, agility: float, flow: float) -> float:
        """Смешивает snap и flow по вероятности того, что игрок выберет тот или иной.

        Snap сравнивается не сам по себе, а вместе с agility: на стримах чистый
        snap не набирает достаточной сложности, чтобы обойти flow, а agility как
        раз измеряет частоту смен скорости курсора.
        """
        combined_snap = norm(_COMBINED_SNAP_NORM_EXPONENT, snap, agility)

        p_snap = _calculate_snap_flow_probability(c_div(flow, combined_snap))
        p_flow = 1 - p_snap

        total = combined_snap * p_snap + flow * p_flow
        return total * _SKILL_MULTIPLIER_TOTAL

    def get_difficult_sliders(self) -> float:
        if not self._slider_strains:
            return 0.0

        max_slider_strain = max(self._slider_strains)
        if max_slider_strain == 0:
            return 0.0

        return sequential_sum(logistic(s / max_slider_strain, 0.5, 12.0) for s in self._slider_strains)

    def count_top_weighted_sliders(self, difficulty_value: float) -> float:
        if not self._slider_strains:
            return 0.0

        consistent_top_strain = difficulty_value * (1 - self.decay_weight)
        if consistent_top_strain == 0:
            return 0.0

        return sequential_sum(logistic(s / consistent_top_strain, 0.88, 10, 1.1) for s in self._slider_strains)

    def difficulty_value(self) -> float:
        """Непрерывная взвешенная сумма отсортированных страйнов.

        Вес — интеграл DecayWeight^x по длине секции. Деление в конце на
        (1 - DecayWeight), а не на log(1/DecayWeight), сделано намеренно: так
        карта из секций максимальной длины даёт ровно то же значение, что
        и в обычном StrainSkill.
        """
        difficulty = 0.0
        time = 0.0

        for strain in self._get_reduced_strain_peaks():
            start_time = time
            end_time = time + strain.section_length / self.max_section_length

            weight = pow_double(self.decay_weight, start_time) - pow_double(self.decay_weight, end_time)
            difficulty += strain.value * weight
            time = end_time

        return difficulty / (1 - self.decay_weight)

    def _get_reduced_strain_peaks(self) -> Iterable[StrainPeak]:
        """Понижает самые высокие страйны, разбивая их на куски по 20 мс.

        Разбиение сглаживает скачки, которые иначе возникали бы от резкого
        понижения целой секции.
        """
        strains = [p for p in self.get_current_strain_peaks() if p.value > 0]

        time = 0.0
        skip_count = 0

        while len(strains) > skip_count and time < _REDUCED_SECTION_TIME:
            strain = strains[skip_count]

            added_time = 0.0
            while added_time < strain.section_length:
                scale = math.log10(
                    lerp(1, 10, min(max((time + added_time) / _REDUCED_SECTION_TIME, 0.0), 1.0))
                )
                # Куски намеренно дописываются в конец, сортировка идёт потом.
                strains.append(
                    StrainPeak(
                        strain.value * lerp(_REDUCED_STRAIN_BASELINE, 1.0, scale),
                        min(_CHUNK_SIZE, strain.section_length - added_time),
                    )
                )
                added_time += _CHUNK_SIZE

            time += strain.section_length
            skip_count += 1

        return sorted(strains[skip_count:], key=lambda p: p.value, reverse=True)


def _calculate_snap_flow_probability(ratio: float) -> float:
    """Превращает отношение snap к flow в вероятность снапа.

    Логистическая функция выбрана как решение f(x) + f(1/x) = 1: снап и флоу
    симметричны и в сумме всегда дают единицу.
    """
    k = 7.27

    if ratio == 0:
        return 0.0
    if math.isnan(ratio):
        return 1.0

    return logistic_exp(-k * math.log(ratio))
