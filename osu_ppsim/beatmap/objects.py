"""Модель объектов карты.

Источник: osu.Game/Rulesets/Objects/Types/PathType.cs,
          osu.Game/Rulesets/Objects/PathControlPoint.cs,
          osu.Game.Rulesets.Osu/Objects/{HitCircle,Slider,Spinner}.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

На этом этапе слайдер хранит только разобранный путь: скорость, тики и вложенные
объекты появляются после расчёта геометрии (slider_path, slider_events).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ..f32 import Vec2, f32

__all__ = [
    "SplineType",
    "PathControlPoint",
    "HitObject",
    "HitCircle",
    "Slider",
    "Spinner",
    "NestedType",
    "NestedObject",
]


class SplineType(enum.Enum):
    """PathType.SplineType. Значения совпадают с именами в дампе оракула.

    Члена Bezier здесь нет и не должно быть: в osu! безье — это B-сплайн
    с неуказанной степенью, то есть равной числу контрольных точек.
    """

    CATMULL = "Catmull"
    BSPLINE = "BSpline"
    LINEAR = "Linear"
    PERFECT_CURVE = "PerfectCurve"


@dataclass(frozen=True)
class PathType:
    """Тип сегмента пути: вид сплайна плюс степень для B-сплайна."""

    spline_type: SplineType
    #: None означает степень, равную числу контрольных точек, то есть безье.
    degree: int | None = None


#: OsuHitObject.StackOffset: коэффициент -6.4f, приведённый к float32 заранее.
_STACK_OFFSET_RATIO = f32(-6.4)


#: PathType.BEZIER — B-сплайн без указанной степени.
BEZIER = PathType(SplineType.BSPLINE)
CATMULL = PathType(SplineType.CATMULL)
LINEAR = PathType(SplineType.LINEAR)
PERFECT_CURVE = PathType(SplineType.PERFECT_CURVE)


@dataclass
class PathControlPoint:
    """Точка пути. type != None означает начало нового сегмента."""

    position: Vec2
    type: PathType | None = None


@dataclass
class HitObject:
    """Общая часть. Поля со значениями по умолчанию проставляются позже:
    scale/radius/preempt — из настроек сложности, stack_height — стакингом."""

    start_time: float
    position: Vec2

    scale: float = 1.0
    radius: float = 64.0
    time_preempt: float = 600.0
    time_fade_in: float = 400.0
    stack_height: int = 0

    @property
    def end_time(self) -> float:
        return self.start_time

    @property
    def stack_offset(self) -> Vec2:
        """OsuHitObject.StackOffset => new Vector2(StackHeight * Scale * -6.4f).

        Оба умножения идут во float32: StackHeight целый, Scale и -6.4f — float,
        поэтому округление происходит дважды, а не один раз в конце. Разница
        вылезает при крупном масштабе, то есть под EZ.
        """
        offset = f32(f32(self.stack_height * self.scale) * _STACK_OFFSET_RATIO)
        return Vec2(offset, offset)

    @property
    def stacked_position(self) -> Vec2:
        return self.position + self.stack_offset

    @property
    def end_position(self) -> Vec2:
        """OsuHitObject.EndPosition. У слайдера переопределяется концом пути."""
        return self.position

    @property
    def stacked_end_position(self) -> Vec2:
        return self.end_position + self.stack_offset


@dataclass
class HitCircle(HitObject):
    pass


class NestedType(enum.Enum):
    """Типы вложенных объектов слайдера — как их называет дамп оракула."""

    HEAD = "SliderHeadCircle"
    TICK = "SliderTick"
    REPEAT = "SliderRepeat"
    TAIL = "SliderTailCircle"


@dataclass
class NestedObject:
    """Вложенный объект слайдера. Позиция абсолютная, стак наследуется от слайдера."""

    type: NestedType
    start_time: float
    position: Vec2
    stack_height: int = 0
    scale: float = 1.0

    @property
    def stacked_position(self) -> Vec2:
        offset = f32(f32(self.stack_height * self.scale) * _STACK_OFFSET_RATIO)
        return self.position + Vec2(offset, offset)


@dataclass
class Slider(HitObject):
    control_points: list[PathControlPoint] = field(default_factory=list)
    repeat_count: int = 0
    #: Длина из .osu. None означает, что её нужно вывести из геометрии пути.
    expected_distance: float | None = None

    # Заполняется после расчёта геометрии (см. beatmap.defaults.apply_defaults).
    path: object | None = None
    path_distance: float = 0.0
    velocity: float = 1.0
    tick_distance: float = 0.0
    nested: list[NestedObject] = field(default_factory=list)

    @property
    def span_count(self) -> int:
        """IHasRepeats.SpanCount() => RepeatCount + 1."""
        return self.repeat_count + 1

    @property
    def end_time(self) -> float:
        """Slider.EndTime => StartTime + SpanCount * Path.Distance / Velocity.

        Порядок операций важен: сначала произведение, потом деление.

        Ветки на нулевую скорость здесь нет и не нужно: декодер зажимает
        beatLength в [6, 60000], множитель скорости в [0.1, 10], а
        SliderMultiplier в [0.4, 3.6], поэтому velocity строго положительна.
        """
        return self.start_time + self.span_count * self.path_distance / self.velocity

    @property
    def duration(self) -> float:
        """Slider.Duration => EndTime - StartTime.

        Именно разность, а не SpanCount * Distance / Velocity напрямую. На картах,
        где start_time измеряется десятками тысяч миллисекунд, вычитание съедает
        младшие разряды, и результат отличается от «математически того же» выражения.
        Считать иначе — значит разойтись с эталоном на каждом слайдере.
        """
        return self.end_time - self.start_time

    @property
    def span_duration(self) -> float:
        """Slider.SpanDuration => Duration / SpanCount."""
        return self.duration / self.span_count

    @property
    def end_position(self) -> Vec2:
        """Slider.EndPosition => Position + CurvePositionAt(1).

        Прогресс на конце равен spanCount % 2: у нечётного числа пролётов
        курсор оказывается в конце пути, у чётного возвращается к началу.
        """
        if self.path is None:
            return self.position
        return self.position + self.path.position_at(self.span_count % 2)


@dataclass
class Spinner(HitObject):
    spinner_end_time: float = 0.0

    @property
    def stack_offset(self) -> Vec2:
        """Spinner.StackOffset => Vector2.Zero.

        Спиннер стоит в центре поля и стеком не сдвигается. Отличие видно только
        на картах формата ниже v6: новый стакинг спиннеры пропускает, а старый
        может выдать спиннеру ненулевой StackHeight, сдвигая его вслед за
        слайдером. Позиция спиннера читается эвалуаторами Aim как позиция
        соседа, поэтому расхождение уезжает прямо в звёзды.
        """
        return Vec2(0.0, 0.0)

    @property
    def duration(self) -> float:
        return self.spinner_end_time - self.start_time

    @property
    def end_time(self) -> float:
        return self.spinner_end_time
