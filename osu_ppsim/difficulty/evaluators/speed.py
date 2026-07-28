"""Оценка сложности нажатия.

Источник: osu.Game.Rulesets.Osu/Difficulty/Evaluators/Speed/SpeedEvaluator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

from typing import Final

from ...beatmap.objects import Spinner
from ..hitobject import OsuDifficultyHitObject
from ..utils import bpm_to_milliseconds, clamp, milliseconds_to_bpm, pow_double, pow_int

__all__ = ["evaluate_difficulty_of"]

#: 200 BPM в 1/4 — порог, с которого начинается бонус за скорость.
_MIN_SPEED_BONUS: Final[float] = 200.0
_SPEED_BALANCING_FACTOR: Final[float] = 40.0


def evaluate_difficulty_of(current: OsuDifficultyHitObject) -> float:
    if isinstance(current.base_object, Spinner):
        return 0.0

    strain_time = current.adjusted_delta_time
    doubletap_feasibility = 1.0 - current.calculate_doubletap_feasibility(current.next(0))

    # Дельта зажимается окном 300. Константа 0.93 подобрана так, чтобы стримы
    # на 260 BPM с OD8 не получали резкого нерфа, а 0.92 ограничивает эффект зажима.
    strain_time /= clamp((strain_time / current.hit_window_great) / 0.93, 0.92, 1.0)

    speed_bonus = 0.0
    if milliseconds_to_bpm(strain_time) > _MIN_SPEED_BONUS:
        speed_bonus = 0.75 * pow_int(
            (bpm_to_milliseconds(_MIN_SPEED_BONUS) - strain_time) / _SPEED_BALANCING_FACTOR, 2
        )

    speed_difficulty = (1 + speed_bonus) * 1000 / strain_time
    speed_difficulty *= _high_bpm_bonus(current.adjusted_delta_time)

    return speed_difficulty * doubletap_feasibility


def _high_bpm_bonus(ms: float) -> float:
    return 1 / (1 - pow_double(0.3, ms / 1000))
