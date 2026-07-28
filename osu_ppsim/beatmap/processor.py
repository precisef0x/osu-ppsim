"""Пост-обработка карты: стакинг и максимальное комбо.

Источник: osu.Game.Rulesets.Osu/Beatmaps/OsuBeatmapProcessor.cs,
          osu.Game/Beatmaps/IBeatmap.cs (GetMaxCombo)
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

from typing import Final

from ..f32 import Vec2, f32
from .decoder import Beatmap
from .objects import HitCircle, HitObject, Slider, Spinner

__all__ = ["post_process", "apply_stacking", "get_max_combo", "reflect_vertically", "STACK_DISTANCE", "PLAYFIELD_HEIGHT"]

#: OsuBeatmapProcessor.STACK_DISTANCE.
STACK_DISTANCE: Final[int] = 3

#: OsuPlayfield.BASE_SIZE.Y — высота игрового поля.
PLAYFIELD_HEIGHT: Final[float] = 384.0


def reflect_vertically(beatmap: Beatmap) -> None:
    """OsuHitObjectGenerationUtils.ReflectVerticallyAlongPlayfield.

    HardRock не только повышает CS/AR/OD, но и переворачивает карту по вертикали.
    В C# это происходит после ApplyDefaults, но до стакинга; здесь — раньше,
    до расчёта геометрии. Эквивалентно: отражение изометрично, длина пути
    от него не меняется, а на времена не влияет вовсе.
    """
    for hit_object in beatmap.hit_objects:
        hit_object.position = Vec2(hit_object.position.x, PLAYFIELD_HEIGHT - hit_object.position.y)

        if isinstance(hit_object, Slider):
            for point in hit_object.control_points:
                point.position = Vec2(point.position.x, -point.position.y)


def post_process(beatmap: Beatmap) -> None:
    """OsuBeatmapProcessor.PostProcess.

    Правку комбо из PreProcess воспроизводить не нужно: ни расчёт сложности,
    ни формула pp номера комбо не читают — комбо влияет только на цвета.
    """
    apply_stacking(beatmap)
    _propagate_stack_to_nested(beatmap)


def _propagate_stack_to_nested(beatmap: Beatmap) -> None:
    """Вложенные объекты наследуют стак слайдера (OsuHitObject привязывает их к StackHeight)."""
    for obj in beatmap.hit_objects:
        if isinstance(obj, Slider):
            for nested in obj.nested:
                nested.stack_height = obj.stack_height


def _path_end_position(slider: Slider) -> Vec2:
    """Конец пути слайдера без учёта чётности пролётов.

    Старый стакинг берёт буквально `Path.PositionAt(1)`, а не EndPosition.
    У слайдера с чётным числом пролётов это противоположные концы пути, поэтому
    подставить сюда end_position нельзя.
    """
    if slider.path is None:
        return slider.position
    return slider.position + slider.path.position_at(1)


def _stack_threshold(beatmap: Beatmap, obj: HitObject) -> float:
    """OsuBeatmapProcessor.calculateStackThreshold.

    Усечение preempt до int и хранение результата во float — ради совместимости
    со stable, а не случайность.
    """
    return f32(int(obj.time_preempt) * beatmap.stack_leniency)


def apply_stacking(beatmap: Beatmap) -> None:
    hit_objects = beatmap.hit_objects
    if not hit_objects:
        return

    for obj in hit_objects:
        obj.stack_height = 0

    if beatmap.format_version >= 6:
        _apply_stacking(beatmap, hit_objects, 0, len(hit_objects) - 1)
    else:
        _apply_stacking_old(beatmap, hit_objects)


def _apply_stacking(beatmap: Beatmap, hit_objects: list[HitObject], start_index: int, end_index: int) -> None:
    extended_end_index = end_index

    if end_index < len(hit_objects) - 1:
        # Расширяем диапазон, чтобы захватить объекты, на которые опирается стек.
        for i in range(end_index, start_index - 1, -1):
            stack_base_index = i

            for n in range(stack_base_index + 1, len(hit_objects)):
                stack_base_object = hit_objects[stack_base_index]
                if isinstance(stack_base_object, Spinner):
                    break

                object_n = hit_objects[n]
                if isinstance(object_n, Spinner):
                    continue

                if object_n.start_time - stack_base_object.end_time > _stack_threshold(beatmap, object_n):
                    break

                if (
                    stack_base_object.position.distance(object_n.position) < STACK_DISTANCE
                    or (
                        isinstance(stack_base_object, Slider)
                        and stack_base_object.end_position.distance(object_n.position) < STACK_DISTANCE
                    )
                ):
                    stack_base_index = n
                    object_n.stack_height = 0

            if stack_base_index > extended_end_index:
                extended_end_index = stack_base_index
                if extended_end_index == len(hit_objects) - 1:
                    break

    # Обратный проход: стеки считаются от конца к началу.
    extended_start_index = start_index

    for i in range(extended_end_index, start_index, -1):
        n = i
        object_i = hit_objects[i]
        if object_i.stack_height != 0 or isinstance(object_i, Spinner):
            continue

        threshold = _stack_threshold(beatmap, object_i)

        if isinstance(object_i, HitCircle):
            while n - 1 >= 0:
                n -= 1
                object_n = hit_objects[n]
                if isinstance(object_n, Spinner):
                    continue

                # Усечение до int обязательно: в stable вычитались целые.
                if int(object_i.start_time) - int(object_n.end_time) > threshold:
                    break

                if n < extended_start_index:
                    object_n.stack_height = 0
                    extended_start_index = n

                # Круги под последним слайдером стека смещаются вниз-вправо,
                # то есть получают отрицательный стек.
                if isinstance(object_n, Slider) and object_n.end_position.distance(object_i.position) < STACK_DISTANCE:
                    offset = object_i.stack_height - object_n.stack_height + 1

                    for j in range(n + 1, i + 1):
                        object_j = hit_objects[j]
                        if object_n.end_position.distance(object_j.position) < STACK_DISTANCE:
                            object_j.stack_height -= offset

                    # Наткнулись на слайдер: он останется с нулевым стеком
                    # и будет обработан внешним циклом.
                    break

                if object_n.position.distance(object_i.position) < STACK_DISTANCE:
                    object_n.stack_height = object_i.stack_height + 1
                    object_i = object_n

        elif isinstance(object_i, Slider):
            # Начиная с первого слайдера стек всегда положительный.
            while n - 1 >= start_index:
                n -= 1
                object_n = hit_objects[n]
                if isinstance(object_n, Spinner):
                    continue

                if object_i.start_time - object_n.start_time > threshold:
                    break

                if object_n.end_position.distance(object_i.position) < STACK_DISTANCE:
                    object_n.stack_height = object_i.stack_height + 1
                    object_i = object_n


def _apply_stacking_old(beatmap: Beatmap, hit_objects: list[HitObject]) -> None:
    """Стакинг карт формата ниже v6."""
    for i, curr in enumerate(hit_objects):
        if curr.stack_height != 0 and not isinstance(curr, Slider):
            continue

        start_time = curr.end_time
        slider_stack = 0

        for j in range(i + 1, len(hit_objects)):
            threshold = _stack_threshold(beatmap, hit_objects[i])

            if hit_objects[j].start_time - threshold > start_time:
                break

            position2 = _path_end_position(curr) if isinstance(curr, Slider) else curr.position

            if hit_objects[j].position.distance(curr.position) < STACK_DISTANCE:
                curr.stack_height += 1
                start_time = hit_objects[j].start_time
            elif hit_objects[j].position.distance(position2) < STACK_DISTANCE:
                # Для слайдеров объекты смещаются вниз-вправо.
                slider_stack += 1
                hit_objects[j].stack_height -= slider_stack
                start_time = hit_objects[j].start_time


def get_max_combo(beatmap: Beatmap) -> int:
    """IBeatmap.GetMaxCombo.

    Комбо дают круги, спиннеры и все вложенные объекты слайдера: голова, тики,
    реверсы и хвост. Сам слайдер как объект комбо не даёт — его судейство
    игнорируемое.
    """
    combo = 0

    for obj in beatmap.hit_objects:
        if isinstance(obj, Slider):
            combo += len(obj.nested)
        else:
            combo += 1

    return combo
