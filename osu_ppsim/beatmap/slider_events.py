"""Вложенные объекты слайдера: голова, тики, реверсы, хвост.

Источник: osu.Game/Rulesets/Objects/SliderEventGenerator.cs,
          osu.Game.Rulesets.Osu/Objects/Slider.cs (CreateNestedHitObjects)
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

from typing import Final

from .objects import NestedObject, NestedType, Slider

__all__ = ["generate_nested_objects", "TAIL_LENIENCY"]

#: SliderEventGenerator.TAIL_LENIENCY. Хвост слайдера исторически сдвинут на -36 мс,
#: чтобы можно было раньше уйти к следующему объекту.
TAIL_LENIENCY: Final[float] = -36.0

#: Предохранитель для карт, отредактированных вручную: длиннее этого тики не считаются.
_MAX_LENGTH: Final[float] = 100000.0


def generate_nested_objects(slider: Slider) -> list[NestedObject]:
    """Собирает вложенные объекты слайдера в порядке, задаваемом генератором событий.

    Событие LegacyLastTick генератор выдаёт, но osu!standard объекта для него не
    создаёт — оно нужно только конвертации в osu!catch. Здесь оно тоже пропущено.
    """
    path = slider.path
    if path is None:
        return []

    start_time = slider.start_time
    span_duration = slider.span_duration
    span_count = slider.span_count
    velocity = slider.velocity

    length = min(_MAX_LENGTH, slider.path_distance)
    # Бесконечный шаг (тики отключены) зажимается длиной пути и обнуляет цикл тиков.
    tick_distance = min(max(slider.tick_distance, 0.0), length)
    min_distance_from_end = velocity * 10

    nested: list[NestedObject] = []

    def add(kind: NestedType, time: float, progress: float | None) -> None:
        position = slider.position if progress is None else slider.position + path.position_at(progress)
        nested.append(
            NestedObject(
                type=kind,
                start_time=time,
                position=position,
                stack_height=slider.stack_height,
                scale=slider.scale,
            )
        )

    add(NestedType.HEAD, start_time, None)

    for span in range(span_count):
        span_start_time = start_time + span * span_duration
        reversed_span = span % 2 == 1

        if tick_distance != 0:
            ticks = _generate_ticks(
                span_start_time, span_duration, reversed_span, length, tick_distance, min_distance_from_end
            )
            # В обратном пролёте тики выходят в порядке убывания времени.
            if reversed_span:
                ticks = list(reversed(ticks))
            for tick_time, tick_progress in ticks:
                add(NestedType.TICK, tick_time, tick_progress)

        if span < span_count - 1:
            add(NestedType.REPEAT, start_time + (span + 1) * span_duration, (span + 1) % 2)

    total_duration = span_count * span_duration

    # Хвост: позиция берётся из EndPosition, то есть Path.PositionAt(spanCount % 2).
    add(NestedType.TAIL, start_time + total_duration, span_count % 2)

    return nested


def _generate_ticks(
    span_start_time: float,
    span_duration: float,
    reversed_span: bool,
    length: float,
    tick_distance: float,
    min_distance_from_end: float,
) -> list[tuple[float, float]]:
    """Тики одного пролёта: пары (время, прогресс по пути).

    Отсчёт всегда идёт от начала пути, а не от начала пролёта, — иначе тики
    в обратных пролётах встали бы не на те же места, что в прямых.
    """
    result: list[tuple[float, float]] = []

    d = tick_distance
    while d <= length:
        if d >= length - min_distance_from_end:
            break

        path_progress = d / length
        time_progress = 1 - path_progress if reversed_span else path_progress
        result.append((span_start_time + time_progress * span_duration, path_progress))

        d += tick_distance

    return result
