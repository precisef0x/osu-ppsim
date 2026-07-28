"""Оценка сложности прицеливания: snap, flow и agility.

Источник: osu.Game.Rulesets.Osu/Difficulty/Evaluators/Aim/{SnapAimEvaluator,
          FlowAimEvaluator,AgilityEvaluator}.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Snap — прицеливание с остановкой курсора на каждом объекте, flow — «протекание»
через них без остановок. Скилл Aim смешивает их по вероятности, а agility
отражает частоту смен скорости курсора при снапе.
"""

from __future__ import annotations

import math
from typing import Final

from ...beatmap.objects import Slider, Spinner
from ...f32 import f32
from ..hitobject import NORMALISED_DIAMETER, NORMALISED_RADIUS, OsuDifficultyHitObject
from ..utils import (
    clamp,
    milliseconds_to_bpm,
    pow_double,
    pow_int,
    reverse_lerp,
    smootherstep,
    smoothstep,
)

__all__ = ["evaluate_snap", "evaluate_flow", "evaluate_agility", "calc_angle_acuteness"]

_WIDE_ANGLE_MULTIPLIER: Final[float] = 9.67
_ACUTE_ANGLE_MULTIPLIER: Final[float] = 2.41
_SNAP_SLIDER_MULTIPLIER: Final[float] = 1.5
_SNAP_VELOCITY_CHANGE_MULTIPLIER: Final[float] = 0.9
#: Выше 1.02 бонус начинает УМЕНЬШАТЬ сложность с ростом дистанции.
_WIGGLE_MULTIPLIER: Final[float] = 1.02
_WIDE_ANGLE_TIME_SCALE: Final[float] = 1.45

_FLOW_VELOCITY_CHANGE_MULTIPLIER: Final[float] = 0.52

_AGILITY_DISTANCE_CAP: Final[float] = NORMALISED_DIAMETER * 1.2


def calc_angle_acuteness(angle: float) -> float:
    return smoothstep(angle, math.radians(140), math.radians(40))


def _calc_angle_wideness(angle: float) -> float:
    return smoothstep(angle, math.radians(40), math.radians(140))


# --- agility -----------------------------------------------------------------


def evaluate_agility(current: OsuDifficultyHitObject) -> float:
    if isinstance(current.base_object, Spinner):
        return 0.0

    previous = current.previous(0) if current.index > 0 else None
    travel_distance = previous.lazy_travel_distance if previous is not None else 0.0
    distance = travel_distance + current.lazy_jump_distance

    distance_scaled = min(distance, _AGILITY_DISTANCE_CAP) / _AGILITY_DISTANCE_CAP

    difficulty = distance_scaled * 1000 / current.adjusted_delta_time
    difficulty *= pow_double(current.small_circle_bonus, 1.5)
    difficulty *= 1 / (1 - pow_double(0.2, current.adjusted_delta_time / 1000))

    return difficulty


# --- snap --------------------------------------------------------------------


def _snap_high_bpm_bonus(ms: float) -> float:
    return 1 / (1 - pow_double(0.03, pow_double(ms / 1000, 0.65)))


def _vector_angle_repetition(current: OsuDifficultyHitObject, previous: OsuDifficultyHitObject) -> float:
    if current.angle is None or previous.angle is None:
        return 1.0

    note_limit = 6
    maximum_repetition_nerf = 0.15
    maximum_vector_influence = 0.5

    constant_angle_count = 0.0

    for index in range(note_limit):
        prev_obj = current.previous(index)
        if prev_obj is None:
            break

        # Векторы учитываются только внутри одной серии прыжков: смена ритма
        # сбивает инерцию движения.
        if max(current.adjusted_delta_time, prev_obj.adjusted_delta_time) > 1.1 * min(
            current.adjusted_delta_time, prev_obj.adjusted_delta_time
        ):
            break

        if prev_obj.normalised_vector_angle is not None and current.normalised_vector_angle is not None:
            angle_difference = abs(current.normalised_vector_angle - prev_obj.normalised_vector_angle)
            constant_angle_count += math.cos(8 * min(math.radians(11.25), angle_difference))

    # При нулевом счётчике C# делит на ноль, получает +Infinity и зажимает его в 1.
    ratio = 1.0 if constant_angle_count == 0 else min(0.5 / constant_angle_count, 1.0)
    vector_repetition = pow_int(ratio, 2)

    stack_factor = smootherstep(current.lazy_jump_distance, 0, NORMALISED_DIAMETER)

    angle_difference_adjusted = math.cos(
        2 * min(math.radians(45), abs(current.angle - previous.angle) * stack_factor)
    )
    base_nerf = 1 - maximum_repetition_nerf * calc_angle_acuteness(previous.angle) * angle_difference_adjusted

    return pow_int(base_nerf + (1 - base_nerf) * vector_repetition * maximum_vector_influence * stack_factor, 2)


def evaluate_snap(current: OsuDifficultyHitObject, with_slider_travel_distance: bool) -> float:
    last_obj = current.previous()
    if isinstance(current.base_object, Spinner) or current.index <= 1 or isinstance(last_obj.base_object, Spinner):
        return 0.0

    last2_obj = current.previous(2)

    curr_distance = current.lazy_jump_distance if with_slider_travel_distance else current.jump_distance
    curr_velocity = curr_distance / current.adjusted_delta_time

    # Если предыдущий объект — слайдер, скорость его прохода продлевается
    # в текущий объект.
    if isinstance(last_obj.base_object, Slider) and with_slider_travel_distance:
        slider_distance = last_obj.lazy_travel_distance + current.lazy_jump_distance
        curr_velocity = max(curr_velocity, slider_distance / current.adjusted_delta_time)

    prev_distance = last_obj.lazy_jump_distance if with_slider_travel_distance else last_obj.jump_distance
    prev_velocity = prev_distance / last_obj.adjusted_delta_time

    snap_difficulty = curr_velocity
    snap_difficulty *= _vector_angle_repetition(current, last_obj)

    if current.angle is not None and last_obj.angle is not None:
        curr_angle = current.angle
        last_angle = last_obj.angle

        velocity_influence = min(curr_velocity, prev_velocity)
        acute_angle_bonus = 0.0

        # Только когда ритм одинаковый.
        if max(current.adjusted_delta_time, last_obj.adjusted_delta_time) < 1.25 * min(
            current.adjusted_delta_time, last_obj.adjusted_delta_time
        ):
            acute_angle_bonus = calc_angle_acuteness(curr_angle)

            # Повтор угла наказывается ДО умножения на что-либо: сравнивается
            # именно сырая острота.
            acute_angle_bonus *= 0.08 + 0.92 * (
                1 - min(acute_angle_bonus, pow_int(calc_angle_acuteness(last_angle), 3))
            )

            acute_angle_bonus *= (
                velocity_influence
                * smootherstep(milliseconds_to_bpm(current.adjusted_delta_time, 2), 300, 400)
                * smootherstep(curr_distance, 0, NORMALISED_DIAMETER * 2)
            )

        wide_angle_bonus = _calc_angle_wideness(curr_angle)
        wide_angle_bonus *= 0.25 + 0.75 * (1 - min(wide_angle_bonus, pow_int(_calc_angle_wideness(last_angle), 3)))

        wide_angle_curr_velocity = curr_distance / pow_double(current.adjusted_delta_time, _WIDE_ANGLE_TIME_SCALE)
        wide_angle_prev_velocity = prev_distance / pow_double(last_obj.adjusted_delta_time, _WIDE_ANGLE_TIME_SCALE)

        if isinstance(last_obj.base_object, Slider) and with_slider_travel_distance:
            slider_distance = last_obj.lazy_travel_distance + current.lazy_jump_distance
            wide_angle_curr_velocity = max(
                wide_angle_curr_velocity,
                slider_distance / pow_double(current.adjusted_delta_time, _WIDE_ANGLE_TIME_SCALE),
            )

        wide_angle_bonus *= min(wide_angle_curr_velocity, wide_angle_prev_velocity)

        if last2_obj is not None:
            # Движение «туда-обратно» через одну точку не заслуживает полного
            # бонуса за широкий угол. Берутся Previous(2) и Previous(0), потому
            # что угол считается по тройке prevprev-prev-curr и его вершина —
            # всегда предыдущий объект.
            distance = (last2_obj.base_object.stacked_position - last_obj.base_object.stacked_position).length
            if distance < 1:
                # distance объявлен в C# как float, поэтому скобка (1 - distance)
                # считается во float32, и только умножение на 0.55 уходит в double.
                wide_angle_bonus *= 1 - 0.55 * f32(1 - distance)

        snap_difficulty += max(
            acute_angle_bonus * _ACUTE_ANGLE_MULTIPLIER, wide_angle_bonus * _WIDE_ANGLE_MULTIPLIER
        )

        # Бонус за «виляние»: прыжки длиной [радиус, 3 диаметра] с углом меньше 110.
        wiggle_bonus = (
            velocity_influence
            * smootherstep(curr_distance, NORMALISED_RADIUS, NORMALISED_DIAMETER)
            * pow_double(reverse_lerp(curr_distance, NORMALISED_DIAMETER * 3, NORMALISED_DIAMETER), 1.8)
            * smootherstep(curr_angle, math.radians(110), math.radians(60))
            * smootherstep(prev_distance, NORMALISED_RADIUS, NORMALISED_DIAMETER)
            * pow_double(reverse_lerp(prev_distance, NORMALISED_DIAMETER * 3, NORMALISED_DIAMETER), 1.8)
            * smootherstep(last_angle, math.radians(110), math.radians(60))
        )

        snap_difficulty += wiggle_bonus * _WIGGLE_MULTIPLIER

    if max(prev_velocity, curr_velocity) != 0:
        if with_slider_travel_distance:
            # При награждении за разницу скоростей берётся чистый прыжок,
            # без скорости слайдера.
            curr_velocity = curr_distance / current.adjusted_delta_time

        dist_ratio = smoothstep(abs(prev_velocity - curr_velocity) / max(prev_velocity, curr_velocity), 0, 1)

        overlap_velocity_buff = min(
            NORMALISED_DIAMETER * 1.25 / min(current.adjusted_delta_time, last_obj.adjusted_delta_time),
            abs(prev_velocity - curr_velocity),
        )

        velocity_change_bonus = overlap_velocity_buff * dist_ratio
        velocity_change_bonus *= pow_int(
            min(current.adjusted_delta_time, last_obj.adjusted_delta_time)
            / max(current.adjusted_delta_time, last_obj.adjusted_delta_time),
            2,
        )

        snap_difficulty += velocity_change_bonus * _SNAP_VELOCITY_CHANGE_MULTIPLIER

    if isinstance(current.base_object, Slider) and with_slider_travel_distance:
        slider_bonus = current.travel_distance / current.travel_time
        snap_difficulty += (
            slider_bonus if slider_bonus < 1 else pow_double(slider_bonus, 0.75)
        ) * _SNAP_SLIDER_MULTIPLIER

    snap_difficulty *= current.small_circle_bonus
    snap_difficulty *= _snap_high_bpm_bonus(current.adjusted_delta_time)

    return snap_difficulty


# --- flow --------------------------------------------------------------------


def _calculate_overlap_factor(first: OsuDifficultyHitObject, second: OsuDifficultyHitObject) -> float:
    object_radius = first.base_object.radius
    distance = first.base_object.stacked_position.distance(second.base_object.stacked_position)
    return clamp(1 - pow_int(max(distance - object_radius, 0) / object_radius, 2), 0, 1)


def evaluate_flow(current: OsuDifficultyHitObject, with_slider_travel_distance: bool) -> float:
    last_obj = current.previous()
    if isinstance(current.base_object, Spinner) or current.index <= 1 or isinstance(last_obj.base_object, Spinner):
        return 0.0

    last_last_obj = current.previous(1)

    curr_distance = current.lazy_jump_distance if with_slider_travel_distance else current.jump_distance
    prev_distance = last_obj.lazy_jump_distance if with_slider_travel_distance else last_obj.jump_distance

    curr_velocity = curr_distance / current.adjusted_delta_time

    if isinstance(last_obj.base_object, Slider) and with_slider_travel_distance:
        slider_distance = last_obj.lazy_travel_distance + current.lazy_jump_distance
        curr_velocity = max(curr_velocity, slider_distance / current.adjusted_delta_time)

    prev_velocity = prev_distance / last_obj.adjusted_delta_time

    flow_difficulty = curr_velocity

    # Бонус за высокий CS применяется к базовой скорости. Здесь он занижен:
    # исходный бонус рассчитывался под другое масштабирование d/t.
    flow_difficulty *= math.sqrt(current.small_circle_bonus)

    # Смены ритма протекать труднее.
    flow_difficulty *= 1 + min(
        0.25,
        pow_int(
            (
                max(current.adjusted_delta_time, last_obj.adjusted_delta_time)
                - min(current.adjusted_delta_time, last_obj.adjusted_delta_time)
            )
            / 50,
            4,
        ),
    )

    if current.angle is not None and last_obj.angle is not None:
        angle_difference = abs(current.angle - last_obj.angle)
        angle_difference_adjusted = math.sin(angle_difference / 2) * 180.0
        angular_velocity = angle_difference_adjusted / (current.adjusted_delta_time * 0.1)

        # Постоянные углы протекать легче, чем рваные.
        flow_difficulty *= 0.8 + math.sqrt(angular_velocity / 270.0)

    # Если все три ноты перекрываются, дополнительного движения не требуется.
    overlapped_notes_weight = 1.0
    if current.index > 2 and last_last_obj is not None:
        o1 = _calculate_overlap_factor(current, last_obj)
        o2 = _calculate_overlap_factor(current, last_last_obj)
        o3 = _calculate_overlap_factor(last_obj, last_last_obj)
        overlapped_notes_weight = 1 - o1 * o2 * o3

    if current.angle is not None:
        # Острые углы тоже плохо протекаются.
        flow_difficulty += curr_velocity * calc_angle_acuteness(current.angle) * overlapped_notes_weight

    if max(prev_velocity, curr_velocity) != 0:
        if with_slider_travel_distance:
            curr_velocity = curr_distance / current.adjusted_delta_time

        dist_ratio = smoothstep(abs(prev_velocity - curr_velocity) / max(prev_velocity, curr_velocity), 0, 1)

        overlap_velocity_buff = min(
            NORMALISED_DIAMETER * 1.25 / min(current.adjusted_delta_time, last_obj.adjusted_delta_time),
            abs(prev_velocity - curr_velocity),
        )

        flow_difficulty += (
            overlap_velocity_buff * dist_ratio * overlapped_notes_weight * _FLOW_VELOCITY_CHANGE_MULTIPLIER
        )

    if isinstance(current.base_object, Slider) and with_slider_travel_distance:
        # Скорость слайдера включается, чтобы величина была сопоставима со snap.
        flow_difficulty += current.travel_distance / current.travel_time

    # Итоговая скорость возводится в степень: flow растёт быстрее и от дистанции,
    # и от времени одновременно.
    flow_difficulty = pow_double(flow_difficulty, 1.45)

    # Расстояния меньше радиуса всегда протекаются, поэтому сложность снижается.
    return flow_difficulty * smootherstep(curr_distance, 0, NORMALISED_RADIUS)
