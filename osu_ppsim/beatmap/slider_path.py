"""Путь слайдера: ломаная, длина, позиция по прогрессу.

Источник: osu.Game/Rulesets/Objects/SliderPath.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

import math

from ..f32 import Vec2, f32
from . import path_approx
from .objects import LINEAR, PathControlPoint, PathType, SplineType

__all__ = ["SliderPath", "DOUBLE_EPSILON"]

#: Precision.DOUBLE_EPSILON. Сравнение с ним НЕСТРОГОЕ: AlmostEquals — это
#: `Math.Abs(a - b) <= acceptableDifference`. Проверено на оракуле: слайдер
#: длиной ровно 1e-7 считается нулевым, длиной 1.1e-7 — уже нет.
DOUBLE_EPSILON = 1e-7

#: SliderPath.calculateSubPath: дуга с таким числом точек считается вырожденной
#: и откатывается на безье. Столько точек требует длина дуги порядка 120 тысяч.
_MAX_ARC_POINTS = 1000


class SliderPath:
    """Путь слайдера.

    В отличие от C# здесь нет ленивой инвалидации: путь считается один раз
    в конструкторе. Контрольные точки после разбора .osu не меняются.
    """

    def __init__(
        self,
        control_points: list[PathControlPoint],
        expected_distance: float | None = None,
        optimise_catmull: bool = True,
    ) -> None:
        self.control_points = control_points
        self.expected_distance = expected_distance
        # osu!std создаёт путь как `new SliderPath { OptimiseCatmull = true }`.
        self.optimise_catmull = optimise_catmull

        self.calculated_path: list[Vec2] = []
        self.cumulative_length: list[float] = []
        self.optimised_length: float = 0.0

        self._calculate_path()
        self._calculate_length()

    @property
    def distance(self) -> float:
        return self.cumulative_length[-1] if self.cumulative_length else 0.0

    def position_at(self, progress: float) -> Vec2:
        d = self._progress_to_distance(progress)
        return self._interpolate_vertices(self._index_of_distance(d), d)

    # --- расчёт пути -----------------------------------------------------

    def _calculate_path(self) -> None:
        self.calculated_path = []
        self.optimised_length = 0.0

        if not self.control_points:
            return

        vertices = [cp.position for cp in self.control_points]
        start = 0

        for i in range(len(self.control_points)):
            # Сегмент заканчивается на точке с заданным типом либо в конце списка.
            if self.control_points[i].type is None and i < len(self.control_points) - 1:
                continue

            segment_vertices = vertices[start : i + 1]
            segment_type = self.control_points[start].type or LINEAR

            if len(segment_vertices) == 1:
                self.calculated_path.append(segment_vertices[0])
            elif len(segment_vertices) > 1:
                sub_path = self._calculate_sub_path(segment_vertices, segment_type)

                # Первая точка пропускается, если совпадает с концом прошлого сегмента.
                skip_first = bool(self.calculated_path) and bool(sub_path) and self.calculated_path[-1] == sub_path[0]
                self.calculated_path.extend(sub_path[1:] if skip_first else sub_path)

            start = i

    def _calculate_sub_path(self, points: list[Vec2], path_type: PathType) -> list[Vec2]:
        spline = path_type.spline_type

        if spline is SplineType.LINEAR:
            return path_approx.linear_to_piecewise_linear(points)

        if spline is SplineType.PERFECT_CURVE:
            arc = self._try_circular_arc(points)
            if arc is not None:
                return arc
            # Не получилось — падаем в общую ветку B-сплайна ниже.

        elif spline is SplineType.CATMULL:
            sub_path = path_approx.catmull_to_piecewise_linear(points)
            if not self.optimise_catmull:
                return sub_path
            return self._optimise_catmull_path(sub_path)

        # Безье и B-сплайн. Степень None означает «равна числу точек».
        return path_approx.bspline_to_piecewise_linear(points, path_type.degree or len(points))

    def _try_circular_arc(self, points: list[Vec2]) -> list[Vec2] | None:
        """Возвращает дугу либо None, если её надо заменить безье.

        Проверки повторяют SliderPath.calculateSubPath: дуга строится только по
        трём точкам, должна быть невырожденной и не требовать тысячи точек.
        """
        if len(points) != 3:
            return None

        props = path_approx.CircularArcProperties(points)
        if not props.is_valid:
            return None

        if path_approx.arc_point_count(props) >= _MAX_ARC_POINTS:
            return None

        sub_path = path_approx.circular_arc_to_piecewise_linear(points)
        return sub_path or None

    def _optimise_catmull_path(self, sub_path: list[Vec2]) -> list[Vec2]:
        """Прореживание Catmull-пути.

        osu!stable оставлял только точки, отстоящие на 6px, что убирает «луковицы»
        вокруг совпадающих узлов. Снятая длина копится в optimised_length, иначе
        путь потом растянулся бы до ExpectedDistance.
        """
        optimised: list[Vec2] = []
        last_start: Vec2 | None = None
        length_removed_since_start = 0.0
        catmull_segment_length = path_approx.CATMULL_DETAIL * 2

        for i, point in enumerate(sub_path):
            if last_start is None:
                optimised.append(point)
                last_start = point
                continue

            dist_from_start = last_start.distance(point)
            length_removed_since_start += sub_path[i - 1].distance(point)

            # Либо 6px от начала, либо последняя точка узла, либо конец пути.
            if dist_from_start > 6 or (i + 1) % catmull_segment_length == 0 or i == len(sub_path) - 1:
                optimised.append(point)
                self.optimised_length += length_removed_since_start - dist_from_start
                last_start = None
                length_removed_since_start = 0.0

        return optimised

    # --- расчёт длины ----------------------------------------------------

    def _calculate_length(self) -> None:
        calculated_length = self.optimised_length
        self.cumulative_length = [0.0]

        for i in range(len(self.calculated_path) - 1):
            diff = self.calculated_path[i + 1] - self.calculated_path[i]
            calculated_length += diff.length
            self.cumulative_length.append(calculated_length)

        expected = self.expected_distance
        if expected is None or calculated_length == expected:
            return

        # В osu-stable путь не растягивается, если две последние точки совпадают.
        if (
            len(self.calculated_path) >= 2
            and self.calculated_path[-1] == self.calculated_path[-2]
            and expected > calculated_length
        ):
            self.cumulative_length.append(calculated_length)
            return

        # Последняя накопленная длина всегда неверна — её пересчитают ниже.
        self.cumulative_length.pop()

        path_end_index = len(self.calculated_path) - 1

        if calculated_length > expected:
            # Путь укорачивается: лишние точки и их длины отбрасываются.
            while self.cumulative_length and self.cumulative_length[-1] >= expected:
                self.cumulative_length.pop()
                self.calculated_path.pop(path_end_index)
                path_end_index -= 1

        if path_end_index <= 0:
            # Ожидаемая длина нулевая или отрицательная.
            self.cumulative_length.append(0.0)
            return

        direction = (self.calculated_path[path_end_index] - self.calculated_path[path_end_index - 1]).normalised()
        self.calculated_path[path_end_index] = self.calculated_path[path_end_index - 1] + direction * f32(
            expected - self.cumulative_length[-1]
        )
        self.cumulative_length.append(expected)

    # --- интерполяция ----------------------------------------------------

    def _index_of_distance(self, d: float) -> int:
        """List<double>.BinarySearch с последующим ~i.

        Именно алгоритм .NET, а не bisect: при точном попадании .NET возвращает
        индекс серединной точки, а bisect_left — крайний левый. Список не строго
        возрастающий (у совпадающих точек пути длина повторяется), и выбор индекса
        внутри такой серии меняет отрезок интерполяции.

        NaN обрабатывается сам собой: Comparer<double> считает его меньше любого
        числа, и поиск сходится к точке вставки 0.
        """
        lengths = self.cumulative_length
        low, high = 0, len(lengths) - 1

        while low <= high:
            i = low + ((high - low) >> 1)
            value = lengths[i]
            # Comparer<double>.Default.Compare(lengths[i], d); NaN меньше всего.
            order = (value > d) - (value < d) if d == d else 1

            if order == 0:
                return i
            if order < 0:
                low = i + 1
            else:
                high = i - 1

        return low

    def _progress_to_distance(self, progress: float) -> float:
        # NaN проходит насквозь: Math.Clamp в .NET на NaN возвращает NaN,
        # и min/max в Python ведут себя так же.
        return min(max(progress, 0.0), 1.0) * self.distance

    def _interpolate_vertices(self, i: int, d: float) -> Vec2:
        if not self.calculated_path:
            return Vec2(0.0, 0.0)

        if i <= 0:
            return self.calculated_path[0]
        if i >= len(self.calculated_path):
            return self.calculated_path[-1]

        p0 = self.calculated_path[i - 1]
        p1 = self.calculated_path[i]

        d0 = self.cumulative_length[i - 1]
        d1 = self.cumulative_length[i]

        # Защита от деления на почти ноль, когда точки практически совпадают.
        if math.fabs(d0 - d1) <= DOUBLE_EPSILON:
            return p0

        w = (d - d0) / (d1 - d0)
        return p0 + (p1 - p0) * f32(w)
