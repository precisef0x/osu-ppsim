"""Препроцессинг объектов для расчёта сложности.

Источник: osu.Game.Rulesets.Osu/Difficulty/Preprocessing/OsuDifficultyHitObject.cs,
          osu.Game/Rulesets/Difficulty/Preprocessing/DifficultyHitObject.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

import math
from typing import Final

from ..beatmap.difficulty_info import GREAT_WINDOW_RANGE, PREEMPT_MIN, hit_window
from ..beatmap.objects import HitObject, NestedType, Slider, Spinner
from ..beatmap.slider_events import TAIL_LENIENCY
from ..f32 import Vec2, f32
from .utils import c_div, clamp, pow_double, pow_int, reverse_lerp

__all__ = [
    "OsuDifficultyHitObject",
    "create_difficulty_hit_objects",
    "NORMALISED_RADIUS",
    "NORMALISED_DIAMETER",
    "MIN_DELTA_TIME",
]

#: Все дистанции нормируются к этому радиусу, чтобы CS не влиял на масштаб.
NORMALISED_RADIUS: Final[int] = 50
NORMALISED_DIAMETER: Final[int] = NORMALISED_RADIUS * 2
MIN_DELTA_TIME: Final[int] = 25

# В C# это `NORMALISED_RADIUS * 2.4f`, где множитель УЖЕ float32. Посчитать
# 50 * 2.4 в double и округлить потом — не то же самое: получится ровно 120,
# тогда как на самом деле 120.00000762939453. Разница в 7.6e-06 переезжает
# прямо в MinimumJumpDistance.
_MAXIMUM_SLIDER_RADIUS: Final[float] = f32(NORMALISED_RADIUS * f32(2.4))
_ASSUMED_SLIDER_RADIUS: Final[float] = f32(NORMALISED_RADIUS * f32(1.8))

#: Разность двух float-констант тоже считается во float32.
_SLIDER_RADIUS_DELTA: Final[float] = f32(_MAXIMUM_SLIDER_RADIUS - _ASSUMED_SLIDER_RADIUS)

#: OsuModHidden.FADE_OUT_DURATION_MULTIPLIER.
_HIDDEN_FADE_OUT_DURATION_MULTIPLIER: Final[float] = 0.3


class OsuDifficultyHitObject:
    """Объект, подготовленный для скиллов: дистанции, углы, времена."""

    __slots__ = (
        "index",
        "base_object",
        "last_object",
        "clock_rate",
        "objects",
        "delta_time",
        "start_time",
        "end_time",
        "hit_window_great",
        "adjusted_delta_time",
        "last_object_end_delta_time",
        "jump_distance",
        "lazy_jump_distance",
        "minimum_jump_distance",
        "minimum_jump_time",
        "travel_distance",
        "travel_time",
        "lazy_end_position",
        "lazy_travel_distance",
        "lazy_travel_time",
        "angle",
        "normalised_vector_angle",
    )

    def __init__(
        self,
        hit_object: HitObject,
        last_object: HitObject,
        clock_rate: float,
        objects: list[OsuDifficultyHitObject],
        index: int,
        overall_difficulty: float,
    ) -> None:
        self.objects = objects
        self.index = index
        self.base_object = hit_object
        self.last_object = last_object
        self.clock_rate = clock_rate

        self.delta_time = (hit_object.start_time - last_object.start_time) / clock_rate
        self.start_time = hit_object.start_time / clock_rate
        self.end_time = hit_object.end_time / clock_rate
        self.hit_window_great = _hit_window_great(hit_object, overall_difficulty, clock_rate)

        # Ограничение снизу защищает расчёт от одновременных объектов.
        self.adjusted_delta_time = max(self.delta_time, MIN_DELTA_TIME)
        previous = self.previous()
        self.last_object_end_delta_time = (
            max(self.start_time - previous.end_time, MIN_DELTA_TIME)
            if previous is not None
            else self.adjusted_delta_time
        )

        self.jump_distance = 0.0
        self.lazy_jump_distance = 0.0
        self.minimum_jump_distance = 0.0
        self.minimum_jump_time = 0.0
        self.travel_distance = 0.0
        self.travel_time = 0.0
        self.lazy_end_position: Vec2 | None = None
        self.lazy_travel_distance = 0.0
        self.lazy_travel_time = 0.0
        self.angle: float | None = None
        self.normalised_vector_angle: float | None = None

        self._compute_slider_cursor_position()
        self._set_distances(clock_rate)

    # --- навигация -------------------------------------------------------

    def previous(self, skip_count: int = 0) -> OsuDifficultyHitObject | None:
        index = self.index - (skip_count + 1)
        return self.objects[index] if 0 <= index < len(self.objects) else None

    def next(self, skip_count: int = 0) -> OsuDifficultyHitObject | None:
        index = self.index + (skip_count + 1)
        return self.objects[index] if 0 <= index < len(self.objects) else None

    # --- производные величины --------------------------------------------

    @property
    def preempt(self) -> float:
        return self.base_object.time_preempt / self.clock_rate

    @property
    def small_circle_bonus(self) -> float:
        return max(1.0, 1.0 + (30 - self.base_object.radius) / 70)

    @property
    def overall_difficulty(self) -> float:
        return (79.5 - self.hit_window_great / 2) / 6

    def opacity_at(self, time: float, hidden: bool) -> float:
        """Видимость объекта в заданный момент."""
        if time > self.base_object.start_time:
            # После времени клика объект считается невидимым — приближение,
            # достаточное для мест, где эта функция используется.
            return 0.0

        fade_in_start_time = self.base_object.start_time - self.base_object.time_preempt
        # Равно TimeFadeIn без поправок мода HD.
        fade_in_duration = 400 * min(1.0, self.base_object.time_preempt / PREEMPT_MIN)

        if hidden:
            fade_out_start_time = (
                self.base_object.start_time - self.base_object.time_preempt + self.base_object.time_fade_in
            )
            fade_out_duration = self.base_object.time_preempt * _HIDDEN_FADE_OUT_DURATION_MULTIPLIER

            return min(
                clamp((time - fade_in_start_time) / fade_in_duration, 0.0, 1.0),
                1.0 - clamp((time - fade_out_start_time) / fade_out_duration, 0.0, 1.0),
            )

        return clamp((time - fade_in_start_time) / fade_in_duration, 0.0, 1.0)

    def calculate_doubletap_feasibility(self, next_obj: OsuDifficultyHitObject | None) -> float:
        """Насколько реально дабл-тапнуть этот объект со следующим, от 0 до 1."""
        if next_obj is None:
            return 0.0

        curr_delta_time = max(1.0, self.delta_time)
        next_delta_time = max(1.0, next_obj.delta_time)
        delta_difference = abs(next_delta_time - curr_delta_time)

        speed_ratio = curr_delta_time / max(curr_delta_time, delta_difference)
        window_ratio = pow_int(min(1.0, curr_delta_time / self.hit_window_great), 5)

        # Дабл-тап невозможен, если круги не пересекаются.
        distance_factor = pow_int(reverse_lerp(self.lazy_jump_distance, NORMALISED_DIAMETER, NORMALISED_RADIUS), 2)

        return 1.0 - pow_double(speed_ratio, distance_factor * (1 - window_ratio))

    # --- расчёт ----------------------------------------------------------

    def _set_distances(self, clock_rate: float) -> None:
        base = self.base_object

        if isinstance(base, Slider):
            # Бонус за повторы, пока нет системы страйнов по вложенным объектам.
            self.travel_distance = self.lazy_travel_distance * max(1.0, pow_double(base.repeat_count, 0.3))
            self.travel_time = max(self.lazy_travel_time / clock_rate, MIN_DELTA_TIME)

        self.minimum_jump_time = self.adjusted_delta_time

        # На спиннерах ни угол, ни дистанция не имеют смысла.
        if isinstance(base, Spinner) or isinstance(self.last_object, Spinner):
            return

        # Приведение к единому CS: в C# это float-деление, а не double.
        scaling_factor = f32(NORMALISED_RADIUS / f32(base.radius))

        last_difficulty_object = self.previous()
        last_last_difficulty_object = self.previous(1)

        last_cursor_position = (
            _end_cursor_position(last_difficulty_object)
            if last_difficulty_object is not None
            else self.last_object.stacked_position
        )

        self.jump_distance = f32((self.last_object.stacked_position - base.stacked_position).length * scaling_factor)
        self.lazy_jump_distance = f32((base.stacked_position - last_cursor_position).length * scaling_factor)
        self.minimum_jump_distance = self.lazy_jump_distance

        if isinstance(self.last_object, Slider) and last_difficulty_object is not None:
            last_travel_time = max(last_difficulty_object.lazy_travel_time / clock_rate, MIN_DELTA_TIME)
            self.minimum_jump_time = max(self.adjusted_delta_time - last_travel_time, MIN_DELTA_TIME)

            # Игрок выбирает меньшее из двух: срезать слайдер (lazy) либо
            # доследовать до его хвоста и прыгать уже оттуда.
            tail = _nested_of_type(self.last_object, NestedType.TAIL)
            tail_position = tail.stacked_position if tail is not None else self.last_object.stacked_position
            tail_jump_distance = f32((tail_position - base.stacked_position).length * scaling_factor)

            # Слева LazyJumpDistance объявлен как double, и скобка из двух
            # float-констант просто расширяется. Справа же tailJumpDistance —
            # float, поэтому вычитание идёт во float32 и округляется.
            self.minimum_jump_distance = max(
                0.0,
                min(
                    self.lazy_jump_distance - _SLIDER_RADIUS_DELTA,
                    f32(tail_jump_distance - _MAXIMUM_SLIDER_RADIUS),
                ),
            )

        # Проверка last_difficulty_object избыточна по существу (если есть объект
        # на два назад, то есть и предыдущий), но избавляет _calculate_slider_angle
        # от необязательного аргумента.
        if (
            last_difficulty_object is not None
            and last_last_difficulty_object is not None
            and not isinstance(last_last_difficulty_object.base_object, Spinner)
        ):
            if isinstance(last_difficulty_object.base_object, Slider) and last_difficulty_object.travel_distance > 0:
                head = _nested_of_type(last_difficulty_object.base_object, NestedType.HEAD)
                if head is not None:
                    last_cursor_position = head.stacked_position

            last_last_cursor_position = _end_cursor_position(last_last_difficulty_object)

            angle = _calculate_angle(base.stacked_position, last_cursor_position, last_last_cursor_position)
            slider_angle = self._calculate_slider_angle(last_difficulty_object, last_last_cursor_position)

            v = base.stacked_position - last_cursor_position
            self.normalised_vector_angle = math.atan2(abs(v.y), abs(v.x))

            self.angle = min(angle, slider_angle)

    def _calculate_slider_angle(
        self, last_difficulty_object: OsuDifficultyHitObject, last_last_cursor_position: Vec2
    ) -> float:
        last_cursor_position = _end_cursor_position(last_difficulty_object)

        prev = last_difficulty_object.base_object
        if isinstance(prev, Slider) and last_difficulty_object.travel_distance > 0 and len(prev.nested) >= 2:
            # Предпоследний вложенный объект слайдера.
            last_last_cursor_position = prev.nested[-2].stacked_position

        return _calculate_angle(self.base_object.stacked_position, last_cursor_position, last_last_cursor_position)

    def _compute_slider_cursor_position(self) -> None:
        slider = self.base_object
        if not isinstance(slider, Slider) or slider.path is None:
            return
        if self.lazy_end_position is not None:
            return

        tracking_end_time = max(
            slider.start_time + slider.duration + TAIL_LENIENCY,
            slider.start_time + slider.duration / 2,
        )

        nested = slider.nested
        last_real_tick = None
        for obj in nested:
            if obj.type is NestedType.TICK:
                last_real_tick = obj

        if last_real_tick is not None and last_real_tick.start_time > tracking_end_time:
            tracking_end_time = last_real_tick.start_time

            # Когда последний тик оказывается позже конца отслеживания, порядок
            # вложенных объектов пересобирается. С точки зрения смысла слайдера
            # это странно, но даёт нулевое расхождение с эталонным диффкалком,
            # и ppy сохраняет такое поведение намеренно.
            reordered = [obj for obj in nested if obj is not last_real_tick]
            reordered.append(last_real_tick)
            nested = reordered

        self.lazy_travel_time = tracking_end_time - slider.start_time

        # Слайдер нулевой длительности даёт деление на ноль. В C# это Infinity,
        # дальше NaN, а Path.PositionAt(NaN) возвращает первую точку пути.
        end_time_min = c_div(self.lazy_travel_time, slider.span_duration)
        if math.isfinite(end_time_min):
            if end_time_min % 2 >= 1:
                end_time_min = 1 - end_time_min % 1
            else:
                end_time_min %= 1
        else:
            # inf % 2 и NaN % 2 в C# оба дают NaN, а NaN >= 1 ложно,
            # то есть выполняется вторая ветка и результат остаётся NaN.
            end_time_min = math.nan

        # Временная оценка, пока не найдено настоящее положение курсора.
        self.lazy_end_position = slider.stacked_position + slider.path.position_at(end_time_min)

        curr_cursor_position = slider.stacked_position
        # Здесь масштаб считается в double, в отличие от float в _set_distances.
        scaling_factor = NORMALISED_RADIUS / slider.radius

        for i in range(1, len(nested)):
            curr_movement_obj = nested[i]

            curr_movement = curr_movement_obj.stacked_position - curr_cursor_position
            curr_movement_length = scaling_factor * curr_movement.length

            required_movement = _ASSUMED_SLIDER_RADIUS

            if i == len(nested) - 1:
                # У конца слайдера послабление по времени, поэтому берётся
                # меньшее из двух движений: до ленивого конца или до настоящего.
                lazy_movement = self.lazy_end_position - curr_cursor_position
                if lazy_movement.length < curr_movement.length:
                    curr_movement = lazy_movement
                curr_movement_length = scaling_factor * curr_movement.length
            elif curr_movement_obj.type is NestedType.REPEAT:
                # Для реверса порог движения жёстче.
                required_movement = float(NORMALISED_RADIUS)

            if curr_movement_length > required_movement:
                curr_cursor_position = curr_cursor_position + curr_movement * f32(
                    (curr_movement_length - required_movement) / curr_movement_length
                )
                curr_movement_length *= (curr_movement_length - required_movement) / curr_movement_length
                self.lazy_travel_distance += curr_movement_length

            if i == len(nested) - 1:
                self.lazy_end_position = curr_cursor_position


def _nested_of_type(slider: Slider, kind: NestedType):
    for obj in slider.nested:
        if obj.type is kind:
            return obj
    return None


def _end_cursor_position(difficulty_hit_object: OsuDifficultyHitObject) -> Vec2:
    return (
        difficulty_hit_object.lazy_end_position
        if difficulty_hit_object.lazy_end_position is not None
        else difficulty_hit_object.base_object.stacked_position
    )


def _calculate_angle(current: Vec2, last: Vec2, last_last: Vec2) -> float:
    v1 = last_last - last
    v2 = current - last

    dot = v1.dot(v2)
    det = f32(f32(v1.x * v2.y) - f32(v1.y * v2.x))

    return abs(math.atan2(det, dot))


def _hit_window_great(hit_object: HitObject, overall_difficulty: float, clock_rate: float) -> float:
    """DifficultyHitObject.HitWindowGreat = 2 * сырое окно / clockRate.

    У слайдера собственное окно пустое, и берётся окно вложенной головы —
    то есть то же самое. У спиннера окна нет вовсе, поэтому ноль.
    """
    if isinstance(hit_object, Spinner):
        return 0.0
    return 2 * hit_window(overall_difficulty, GREAT_WINDOW_RANGE) / clock_rate


def create_difficulty_hit_objects(
    hit_objects: list[HitObject], clock_rate: float, overall_difficulty: float
) -> list[OsuDifficultyHitObject]:
    """OsuDifficultyCalculator.CreateDifficultyHitObjects.

    Первый прыжок образуется парой первых объектов, поэтому счёт идёт с индекса 1
    и объектов получается на один меньше, чем на карте.
    """
    objects: list[OsuDifficultyHitObject] = []

    for i in range(1, len(hit_objects)):
        objects.append(
            OsuDifficultyHitObject(
                hit_objects[i], hit_objects[i - 1], clock_rate, objects, len(objects), overall_difficulty
            )
        )

    return objects
