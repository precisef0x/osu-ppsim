"""Оценка сложности запоминания под модом Flashlight.

Источник: osu.Game.Rulesets.Osu/Difficulty/Evaluators/FlashlightEvaluator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

from typing import Final

from ...beatmap.objects import Slider, Spinner
from ...mods import Mods
from ..hitobject import OsuDifficultyHitObject
from ..utils import pow_double, pow_int

__all__ = ["evaluate_difficulty_of"]

_MAX_OPACITY_BONUS: Final[float] = 0.4
_HIDDEN_BONUS: Final[float] = 0.2
_MIN_VELOCITY: Final[float] = 0.5
_SLIDER_MULTIPLIER: Final[float] = 1.3
_MIN_ANGLE_MULTIPLIER: Final[float] = 0.2

#: Назад по времени просматривается не больше стольких объектов.
_LOOKBACK: Final[int] = 10


def evaluate_difficulty_of(current: OsuDifficultyHitObject, mods: Mods) -> float:
    if isinstance(current.base_object, Spinner):
        return 0.0

    hit_object = current.base_object
    scaling_factor = 52.0 / hit_object.radius
    small_dist_nerf = 1.0
    cumulative_strain_time = 0.0
    flashlight_difficulty = 0.0
    angle_repeat_count = 0.0

    last_obj = current

    # Проход назад по времени от текущего объекта.
    for i in range(min(current.index, _LOOKBACK)):
        current_obj = current.previous(i)
        if current_obj is None:
            break
        current_hit_object = current_obj.base_object

        cumulative_strain_time += last_obj.adjusted_delta_time

        if not isinstance(current_hit_object, Spinner):
            jump_distance = (hit_object.stacked_position - current_hit_object.stacked_end_position).length

            # Объекты, легко попадающие в круг фонарика, ослабляются.
            if i == 0:
                small_dist_nerf = min(1.0, jump_distance / 75.0)

            # Из стека учитывается по существу только первый объект.
            stack_nerf = min(1.0, (current_obj.lazy_jump_distance / scaling_factor) / 25.0)

            opacity_bonus = 1.0 + _MAX_OPACITY_BONUS * (
                1.0 - current.opacity_at(current_hit_object.start_time, mods.hidden)
            )

            flashlight_difficulty += stack_nerf * opacity_bonus * scaling_factor * jump_distance / cumulative_strain_time

            # Внешний if — проверка на None вместо C#-nullable, внутренний уже само
            # условие; слитые в одно, они читались бы как одно правило.
            if current_obj.angle is not None and current.angle is not None:  # noqa: SIM102
                # Чем дальше объект по времени, тем слабее он влияет на нерф.
                if abs(current_obj.angle - current.angle) < 0.02:
                    angle_repeat_count += max(1.0 - 0.1 * i, 0.0)

        last_obj = current_obj

    flashlight_difficulty = pow_int(small_dist_nerf * flashlight_difficulty, 2)

    # Под HD нет approach-кругов, поэтому запоминать сложнее.
    if mods.hidden:
        flashlight_difficulty *= 1.0 + _HIDDEN_BONUS

    # Повторяющиеся углы ослабляются.
    flashlight_difficulty *= _MIN_ANGLE_MULTIPLIER + (1.0 - _MIN_ANGLE_MULTIPLIER) / (angle_repeat_count + 1.0)

    slider_bonus = 0.0

    if isinstance(current.base_object, Slider):
        # Обратный масштаб даёт истинную длину прохода, не зависящую от CS.
        pixel_travel_distance = current.lazy_travel_distance / scaling_factor

        slider_bonus = pow_double(max(0.0, pixel_travel_distance / current.travel_time - _MIN_VELOCITY), 0.5)
        # Длинные слайдеры запоминать труднее.
        slider_bonus *= pixel_travel_distance

        # Повторы, наоборот, запоминать проще.
        if current.base_object.repeat_count > 0:
            slider_bonus /= current.base_object.repeat_count + 1

    flashlight_difficulty += slider_bonus * _SLIDER_MULTIPLIER

    return flashlight_difficulty
