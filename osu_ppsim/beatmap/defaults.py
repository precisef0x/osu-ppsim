"""Применение настроек сложности и тайминга к объектам.

Источник: osu.Game.Rulesets.Osu/Objects/{OsuHitObject,Slider}.cs (ApplyDefaultsToSelf),
          osu.Game/Rulesets/Objects/Legacy/LegacyRulesetExtensions.cs,
          osu.Game.Rulesets.Osu/Beatmaps/OsuBeatmapConverter.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

В C# это происходит в HitObject.ApplyDefaults, вызываемом при конвертации карты.
Здесь — отдельный проход по объектам после разбора.
"""

from __future__ import annotations

import math
from typing import Final

from ..f32 import f32
from ..mods import Mods
from .decoder import Beatmap
from .difficulty_info import (
    calculate_scale_from_circle_size,
    radius_from_scale,
    time_fade_in,
    time_preempt,
)
from .objects import Slider
from .slider_events import generate_nested_objects
from .slider_path import DOUBLE_EPSILON, SliderPath

__all__ = ["apply_defaults", "BASE_SCORING_DISTANCE"]

#: OsuHitObject.BASE_SCORING_DISTANCE.
BASE_SCORING_DISTANCE: Final[float] = 100.0


#: OsuModHidden.FADE_IN_DURATION_MULTIPLIER.
HIDDEN_FADE_IN_DURATION_MULTIPLIER: Final[float] = 0.4


def apply_defaults(beatmap: Beatmap, mods: Mods | None = None) -> None:
    """Проставляет объектам масштаб, радиус, preempt, а слайдерам — геометрию.

    Меняет объекты на месте, как и ApplyDefaults в C#.
    """
    scale = calculate_scale_from_circle_size(beatmap.circle_size)
    radius = radius_from_scale(scale)
    preempt = time_preempt(beatmap.approach_rate)
    fade_in = time_fade_in(preempt)

    for hit_object in beatmap.hit_objects:
        hit_object.scale = scale
        hit_object.radius = radius
        hit_object.time_preempt = preempt
        hit_object.time_fade_in = fade_in

        if isinstance(hit_object, Slider):
            _apply_slider_defaults(beatmap, hit_object)

    if mods is not None and mods.hidden:
        _apply_hidden_fade_in(beatmap)


def _apply_hidden_fade_in(beatmap: Beatmap) -> None:
    """OsuModHidden.ApplyToBeatmap.

    Мод укорачивает время появления объекта, и это влияет не только на картинку:
    OpacityAt берёт TimeFadeIn для начала затухания, а значит меняется вся оценка
    скилла Reading. Слайдеры при этом сохраняют исходное значение — ради
    совместимости со stable.
    """
    for hit_object in beatmap.hit_objects:
        if not isinstance(hit_object, Slider):
            hit_object.time_fade_in = hit_object.time_preempt * HIDDEN_FADE_IN_DURATION_MULTIPLIER


def _apply_slider_defaults(beatmap: Beatmap, slider: Slider) -> None:
    timing_point = beatmap.timing_point_at(slider.start_time)
    difficulty_point = beatmap.difficulty_point_at(slider.start_time)

    path = SliderPath(slider.control_points, slider.expected_distance)
    slider.path = path
    slider.path_distance = path.distance

    if abs(path.distance) <= DOUBLE_EPSILON:
        # ConvertHitObjectParser.createSlider: у слайдера нулевой длины повторы
        # сбрасываются — защита от ранкед-карт, где такой слайдер накручивал комбо.
        # В C# это делается при разборе, но там же строится и путь; у нас путь
        # появляется только здесь.
        slider.repeat_count = 0

    beat_length = _precision_adjusted_beat_length(timing_point.beat_length, difficulty_point.slider_velocity)
    slider.velocity = BASE_SCORING_DISTANCE * beatmap.slider_multiplier / beat_length

    # Внимание: scoringDistance намеренно НЕ считается как BASE_SCORING_DISTANCE * multiplier.
    # Ошибка округления здесь воспроизводит поведение stable, и убирать её нельзя.
    scoring_distance = slider.velocity * timing_point.beat_length

    if difficulty_point.generate_ticks:
        slider.tick_distance = (
            scoring_distance / beatmap.slider_tick_rate * _tick_distance_multiplier(beatmap, difficulty_point)
        )
    else:
        # Тайминг-поинт с NaN отключает тики. В C# это просто double.PositiveInfinity;
        # в Python деление на ноль подняло бы исключение, поэтому значение задаётся явно.
        slider.tick_distance = math.inf

    # Вложенные объекты создаются здесь же, как CreateNestedHitObjects в C#.
    # Стак им проставится позже, после post_process.
    slider.nested = generate_nested_objects(slider)


def _precision_adjusted_beat_length(beat_length: float, slider_velocity: float) -> float:
    """LegacyRulesetExtensions.GetPrecisionAdjustedBeatLength для osu!standard.

    Множитель зажимается во float32 — в stable деление шло на float, и это
    заметно в младших разрядах скорости.
    """
    slider_velocity_as_beat_length = -100 / slider_velocity

    if slider_velocity_as_beat_length < 0:
        clamped = min(max(f32(-slider_velocity_as_beat_length), 10.0), 1000.0)
        bpm_multiplier = clamped / 100.0
    else:
        bpm_multiplier = 1.0

    return beat_length * bpm_multiplier


def _tick_distance_multiplier(beatmap: Beatmap, difficulty_point) -> float:
    """OsuBeatmapConverter: у карт формата ниже v8 шаг тиков не масштабируется скоростью."""
    if beatmap.format_version < 8:
        # В C# написано `1f / SliderVelocity`, но SliderVelocity — double,
        # поэтому единица расширяется до double и деление идёт в double.
        # Округлять до float32 нельзя: на SV вроде 100/70 это сдвигает шаг тиков.
        return 1.0 / difficulty_point.slider_velocity
    return 1.0
