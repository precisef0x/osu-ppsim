"""Оценка сложности чтения — новый скилл ребаланса 2026.

Источник: osu.Game.Rulesets.Osu/Difficulty/Evaluators/ReadingEvaluator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Заменил прежние бонусы за AR и HD: вместо них считается плотность объектов
на экране плюс поправки за повторяющиеся углы.
"""

from __future__ import annotations

import math
from typing import Final

from ...beatmap.objects import Spinner
from ..hitobject import NORMALISED_DIAMETER, NORMALISED_RADIUS, OsuDifficultyHitObject
from ..utils import clamp, lerp, norm, pow_double, reverse_lerp, smootherstep

__all__ = ["evaluate_difficulty_of"]

_READING_WINDOW_SIZE: Final[float] = 3000.0
#: Полтора диаметра круга между центрами.
_DISTANCE_INFLUENCE_THRESHOLD: Final[float] = NORMALISED_DIAMETER * 1.5

_MINIMUM_ANGLE_RELEVANCY_TIME: Final[float] = 2000.0
_MAXIMUM_ANGLE_RELEVANCY_TIME: Final[float] = 200.0


def evaluate_difficulty_of(current: OsuDifficultyHitObject, hidden: bool) -> float:
    if isinstance(current.base_object, Spinner) or current.index == 0:
        return 0.0

    next_obj = current.next(0)

    # Скорость только усиливает сложность, ослаблять не может.
    velocity = max(1.0, current.lazy_jump_distance / current.adjusted_delta_time)

    current_visible_density = _retrieve_current_visible_object_density(current)
    past_influence = _get_past_object_difficulty_influence(current)
    constant_angle_nerf = _get_constant_angle_nerf_factor(current)

    note_density_difficulty = _calculate_density_difficulty(
        next_obj, velocity, constant_angle_nerf, past_influence, current_visible_density
    )

    hidden_difficulty = (
        _calculate_hidden_difficulty(current, past_influence, current_visible_density, velocity, constant_angle_nerf)
        if hidden
        else 0.0
    )

    preempt_difficulty = _calculate_preempt_difficulty(velocity, constant_angle_nerf, current.preempt)

    reading_difficulty = norm(1.5, preempt_difficulty, hidden_difficulty, note_density_difficulty)

    # Меньше времени на осмысление — сложнее.
    reading_difficulty *= _high_bpm_bonus(current.adjusted_delta_time)

    return reading_difficulty


def _calculate_density_difficulty(
    next_obj: OsuDifficultyHitObject | None,
    velocity: float,
    constant_angle_nerf: float,
    past_influence: float,
    current_visible_density: float,
) -> float:
    density_multiplier = 2.4
    density_difficulty_base = 2.5

    # Будущая плотность тоже учитывается: она размывает траекторию курсора.
    future_influence = math.sqrt(current_visible_density)

    if next_obj is not None:
        future_influence *= smootherstep(next_obj.lazy_jump_distance, 15, _DISTANCE_INFLUENCE_THRESHOLD)

    difficulty = pow_double(past_influence + future_influence, 1.7) * 0.4 * constant_angle_nerf * velocity

    # Награждаются только карты плотнее средней.
    difficulty = max(0.0, difficulty - density_difficulty_base)

    # Мягкий потолок — часть плотности игрок просто запоминает.
    return pow_double(difficulty, 0.45) * density_multiplier


def _calculate_preempt_difficulty(velocity: float, constant_angle_nerf: float, preempt: float) -> float:
    preempt_balancing_factor = 140000
    #: AR 9.66 в миллисекундах.
    preempt_starting_point = 500

    # Выражение с модулем — это плавный max(0, starting_point - preempt).
    difficulty = (
        pow_double((preempt_starting_point - preempt + abs(preempt - preempt_starting_point)) / 2, 2.5)
        / preempt_balancing_factor
    )
    return difficulty * constant_angle_nerf * velocity


def _calculate_hidden_difficulty(
    current: OsuDifficultyHitObject,
    past_influence: float,
    current_visible_density: float,
    velocity: float,
    constant_angle_nerf: float,
) -> float:
    hidden_multiplier = 0.28

    # Больший preempt означает больше времени в невидимости — это награждается.
    preempt_factor = pow_double(current.preempt, 2.2) * 0.01
    density_factor = pow_double(current_visible_density + past_influence, 3.3) * 3

    difficulty = (preempt_factor + density_factor) * constant_angle_nerf * velocity * 0.01
    difficulty = pow_double(difficulty, 0.4) * hidden_multiplier

    previous = current.previous(0)
    if previous is None:
        return difficulty

    # Идеальные стеки усиливаются, только если объект полностью невидим
    # в момент клика по предыдущему.
    if (
        current.lazy_jump_distance == 0
        and current.opacity_at(previous.base_object.start_time, True) == 0
        and previous.start_time > current.start_time - current.preempt
    ):
        difficulty += hidden_multiplier * 2500 / pow_double(current.adjusted_delta_time, 1.5)

    return difficulty


def _get_past_object_difficulty_influence(current: OsuDifficultyHitObject) -> float:
    influence = 0.0

    for loop_obj in _retrieve_past_visible_objects(current):
        loop_difficulty = current.opacity_at(loop_obj.base_object.start_time, False)

        # На малых дистанциях предыдущие объекты можно «срезать», и их
        # расположение перестаёт мешать.
        loop_difficulty *= smootherstep(loop_obj.lazy_jump_distance, 15, _DISTANCE_INFLUENCE_THRESHOLD)
        loop_difficulty *= _get_time_nerf_factor(current.start_time - loop_obj.start_time)

        influence += loop_difficulty

    return influence


def _retrieve_past_visible_objects(current: OsuDifficultyHitObject):
    """Объекты, видимые на момент появления текущего.

    Цикл идёт по всем предыдущим, но с ранним выходом по окну чтения и preempt,
    поэтому сложность не квадратичная, а линейная по числу видимых объектов.
    """
    for i in range(current.index):
        hit_object = current.previous(i)
        if (
            hit_object is None
            or current.start_time - hit_object.start_time > _READING_WINDOW_SIZE
            # Текущий объект ещё не виден к моменту клика по этому.
            or hit_object.start_time < current.start_time - current.preempt
        ):
            break
        yield hit_object


def _retrieve_current_visible_object_density(current: OsuDifficultyHitObject) -> float:
    """Плотность объектов, видимых в момент клика по текущему."""
    visible_count = 0.0
    hit_object = current.next(0)

    while hit_object is not None:
        if (
            hit_object.start_time - current.start_time > _READING_WINDOW_SIZE
            or current.start_time < hit_object.start_time - hit_object.preempt
        ):
            break

        time_nerf = _get_time_nerf_factor(hit_object.start_time - current.start_time)
        visible_count += hit_object.opacity_at(current.base_object.start_time, False) * time_nerf

        hit_object = hit_object.next(0)

    return visible_count


def _get_constant_angle_nerf_factor(current: OsuDifficultyHitObject) -> float:
    """Насколько часто угол текущего объекта повторялся за последние секунды."""
    constant_angle_count = 0.0
    index = 0
    current_time_gap = 0.0

    loop_prev0: OsuDifficultyHitObject | None = current
    loop_prev1: OsuDifficultyHitObject | None = None
    loop_prev2: OsuDifficultyHitObject | None = None

    while current_time_gap < _MINIMUM_ANGLE_RELEVANCY_TIME:
        loop_obj = current.previous(index)
        if loop_obj is None:
            break

        # Объекты у границы окна учитываются слабее.
        long_interval_factor = 1 - reverse_lerp(
            loop_obj.adjusted_delta_time, _MAXIMUM_ANGLE_RELEVANCY_TIME, _MINIMUM_ANGLE_RELEVANCY_TIME
        )

        if loop_obj.angle is not None and current.angle is not None:
            angle_difference = abs(current.angle - loop_obj.angle)
            angle_difference_alternating = math.pi

            if (
                loop_prev0 is not None
                and loop_prev0.angle is not None
                and loop_prev1 is not None
                and loop_prev1.angle is not None
                and loop_prev2 is not None
                and loop_prev2.angle is not None
            ):
                angle_difference_alternating = abs(loop_prev1.angle - loop_obj.angle)
                angle_difference_alternating += abs(loop_prev2.angle - loop_prev0.angle)

                weight = 1.0
                # Один из углов должен быть очень острым, а другой широким.
                weight *= reverse_lerp(min(loop_obj.angle, loop_prev0.angle) * 180 / math.pi, 20, 5)
                weight *= reverse_lerp(max(loop_obj.angle, loop_prev0.angle) * 180 / math.pi, 60, 120)

                angle_difference_alternating = lerp(math.pi, 0.1 * angle_difference_alternating, weight)

            stack_factor = smootherstep(loop_obj.lazy_jump_distance, 0, NORMALISED_RADIUS)

            constant_angle_count += (
                math.cos(3 * min(math.radians(30), min(angle_difference, angle_difference_alternating) * stack_factor))
                * long_interval_factor
            )

        current_time_gap = current.start_time - loop_obj.start_time
        index += 1

        loop_prev2 = loop_prev1
        loop_prev1 = loop_prev0
        loop_prev0 = loop_obj

    # При нулевом счётчике C# делит на ноль и получает +Infinity, который
    # затем зажимается в 1. В Python деление подняло бы ZeroDivisionError,
    # поэтому результат подставляется явно.
    if constant_angle_count == 0:
        return 1.0
    return clamp(2 / constant_angle_count, 0.2, 1)


def _get_time_nerf_factor(delta_time: float) -> float:
    """Далёкие по времени объекты влияют на чтение слабее."""
    return clamp(2 - delta_time / (_READING_WINDOW_SIZE / 2), 0.0, 1.0)


def _high_bpm_bonus(ms: float) -> float:
    return 1 / (1 - pow_double(0.8, ms / 1000))
