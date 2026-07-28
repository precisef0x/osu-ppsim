"""Производные от настроек сложности: масштаб, радиус, preempt.

Источник: osu.Game/Beatmaps/IBeatmapDifficultyInfo.cs,
          osu.Game/Rulesets/Objects/Legacy/LegacyRulesetExtensions.cs,
          osu.Game.Rulesets.Osu/Objects/OsuHitObject.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

import math
from typing import Final

from ..f32 import f32

__all__ = [
    "PREEMPT_MAX",
    "PREEMPT_MID",
    "PREEMPT_MIN",
    "OBJECT_RADIUS",
    "difficulty_range",
    "difficulty_range_mapped",
    "difficulty_range_int",
    "calculate_scale_from_circle_size",
    "radius_from_scale",
    "time_preempt",
    "time_fade_in",
    "hit_window",
    "GREAT_WINDOW_RANGE",
    "OK_WINDOW_RANGE",
    "MEH_WINDOW_RANGE",
]

#: OsuHitObject.OBJECT_RADIUS
OBJECT_RADIUS: Final[float] = 64.0

#: OsuHitObject: preempt при AR=0 / AR=5 / AR=10.
PREEMPT_MAX: Final[float] = 1800.0
PREEMPT_MID: Final[float] = 1200.0
PREEMPT_MIN: Final[float] = 450.0

#: 0.7f, поднятый в double. Не 0.7! В C# `0.7f * <double>` продвигает float
#: до double, и получается 0.699999988079071. Разница вылезает в младших разрядах Scale.
_ZERO_POINT_SEVEN_F: Final[float] = f32(0.7)

#: LegacyRulesetExtensions.broken_gamefield_rounding_allowance.
#: Историческая компенсация округления игрового поля в старых сборках osu!.
_BROKEN_GAMEFIELD_ROUNDING_ALLOWANCE: Final[float] = f32(1.00041)


def difficulty_range(difficulty: float) -> float:
    """Линейно отображает сложность [0, 10] в [-1, 1]."""
    return (difficulty - 5) / 5


def difficulty_range_mapped(difficulty: float, min_: float, mid: float, max_: float) -> float:
    """Кусочно-линейная кривая: min_ при 0, mid при 5, max_ при 10.

    Имена соответствуют C#: min_ — значение при сложности 0, а не наименьшее
    из трёх. Для preempt, например, min_=1800 больше, чем max_=450.
    """
    if difficulty > 5:
        return mid + (max_ - mid) * difficulty_range(difficulty)
    if difficulty < 5:
        return mid + (mid - min_) * difficulty_range(difficulty)
    return mid


def difficulty_range_int(difficulty: float, min_: float, mid: float, max_: float) -> int:
    """Целочисленный вариант. В C# это приведение `(int)` — усечение к нулю.

    Именно усечение, а не округление: при AR 9.8 точное значение равно
    479.9999999999999, и preempt получается 479, а не 480.
    """
    value = difficulty_range_mapped(difficulty, min_, mid, max_)
    return int(value)  # int() в Python тоже усекает к нулю


def calculate_scale_from_circle_size(circle_size: float, apply_fudge: bool = True) -> float:
    """Масштаб объекта из CS. Возвращает величину, усечённую до float32.

    Порядок операций взят из C# буквально:

        (float)(1.0f - 0.7f * DifficultyRange(circleSize)) / 2 * fudge

    Здесь три отдельных округления до float32: после приведения, после деления
    на 2 и после умножения на fudge. Схлопывать их нельзя — результат разойдётся.
    """
    # CircleSize в BeatmapDifficulty объявлен как float, поэтому усекаем на входе.
    cs = f32(circle_size)

    # 0.7f * DifficultyRange(cs) считается в double: float продвигается до double.
    inner = 1.0 - _ZERO_POINT_SEVEN_F * difficulty_range(cs)

    scale = f32(inner)
    scale = f32(scale / 2)
    if apply_fudge:
        scale = f32(scale * _BROKEN_GAMEFIELD_ROUNDING_ALLOWANCE)
    return scale


def radius_from_scale(scale: float) -> float:
    """OsuHitObject.Radius => OBJECT_RADIUS * Scale.

    Свойство объявлено как double, а Scale — float, поэтому произведение
    считается в double и до float32 уже не усекается.
    """
    return OBJECT_RADIUS * scale


def time_preempt(approach_rate: float) -> int:
    """OsuHitObject.TimePreempt."""
    return difficulty_range_int(f32(approach_rate), PREEMPT_MAX, PREEMPT_MID, PREEMPT_MIN)


def time_fade_in(preempt: float) -> float:
    """OsuHitObject.TimeFadeIn."""
    return 400 * min(1.0, preempt / PREEMPT_MIN)


#: OsuHitWindows: значения окна попадания при сложности 0 / 5 / 10.
GREAT_WINDOW_RANGE: Final[tuple[float, float, float]] = (80.0, 50.0, 20.0)
OK_WINDOW_RANGE: Final[tuple[float, float, float]] = (140.0, 100.0, 60.0)
MEH_WINDOW_RANGE: Final[tuple[float, float, float]] = (200.0, 150.0, 100.0)


def hit_window(overall_difficulty: float, window_range: tuple[float, float, float]) -> float:
    """OsuHitWindows.SetDifficulty: пол значения минус 0.5.

    Смещение на пол-миллисекунды — не опечатка: окно задаётся полуоткрытым,
    чтобы граничное значение попадало в следующую категорию.
    """
    return math.floor(difficulty_range_mapped(overall_difficulty, *window_range)) - 0.5
