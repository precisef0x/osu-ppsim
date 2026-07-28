"""Аппроксимация кривых ломаной.

Источник: osu.Framework/Utils/PathApproximator.cs,
          osu.Framework/Utils/CircularArcProperties.cs
Пин: ppy.osu.Framework 2026.724.0 (версия из osu.Game.csproj)

Отдельной ветки для безье здесь нет: в osu! безье — это B-сплайн степени,
равной числу контрольных точек, и обе кривые считает bspline_to_piecewise_linear.

Вся арифметика идёт через Vec2, то есть во float32 — как в C#, где Vector2
хранит компоненты как float.
"""

from __future__ import annotations

import math
from typing import Final

from ..f32 import Vec2, f32

__all__ = [
    "BEZIER_TOLERANCE",
    "CATMULL_DETAIL",
    "CIRCULAR_ARC_TOLERANCE",
    "CircularArcProperties",
    "bezier_to_piecewise_linear",
    "bspline_to_piecewise_linear",
    "catmull_to_piecewise_linear",
    "circular_arc_to_piecewise_linear",
    "linear_to_piecewise_linear",
]

BEZIER_TOLERANCE: Final[float] = f32(0.25)
CATMULL_DETAIL: Final[int] = 50
CIRCULAR_ARC_TOLERANCE: Final[float] = f32(0.1)

#: Precision.FLOAT_EPSILON.
_FLOAT_EPSILON: Final[float] = f32(1e-3)

#: int.MaxValue. Приведение double к int в .NET Core насыщающее, а не
#: неопределённое: бесконечность превращается именно в это число.
_INT32_MAX: Final[int] = 2147483647


def linear_to_piecewise_linear(control_points: list[Vec2]) -> list[Vec2]:
    """Прямая: точки возвращаются как есть."""
    return list(control_points)


def bezier_to_piecewise_linear(control_points: list[Vec2]) -> list[Vec2]:
    """PathApproximator.BezierToPiecewiseLinear."""
    return bspline_to_piecewise_linear(control_points, max(1, len(control_points) - 1))


def _bspline_to_bezier_internal(control_points: list[Vec2], degree: int) -> tuple[list[list[Vec2]], int]:
    """Разбивает B-сплайн на сегменты безье алгоритмом Бёма.

    Возвращает стек (список, вершина — последний элемент) и уточнённую степень.
    В C# степень передаётся по ссылке и меняется внутри, поэтому возвращаем её.
    """
    degree = min(degree, len(control_points) - 1)
    point_count = len(control_points) - 1
    points = list(control_points)

    if degree == point_count:
        # Разбиение не нужно: сплайн вырождается в одну кривую безье.
        return [points], degree

    stack: list[list[Vec2]] = []

    for i in range(point_count - degree):
        sub_bezier: list[Vec2] = [Vec2(0.0, 0.0)] * (degree + 1)
        sub_bezier[0] = points[i]

        # Узел вставляется degree-1 раз, разрушая исходный список точек.
        for j in range(degree - 1):
            sub_bezier[j + 1] = points[i + 1]

            for k in range(1, degree - j):
                weight = min(k, point_count - degree - i)
                points[i + k] = (points[i + k] * weight + points[i + k + 1]) / (weight + 1)

        sub_bezier[degree] = points[i + 1]
        stack.append(sub_bezier)

    stack.append(points[point_count - degree:])

    # В C# здесь `new Stack<>(result)`, что переворачивает стек: перебор стека
    # идёт от вершины к дну, а конструктор кладёт элементы в этом же порядке.
    stack.reverse()
    return stack, degree


def bspline_to_piecewise_linear(control_points: list[Vec2], degree: int) -> list[Vec2]:
    """PathApproximator.BSplineToPiecewiseLinear.

    Адаптивно дробит кривую, пока она не станет достаточно плоской. Рекурсия
    развёрнута в стек — как и в оригинале.
    """
    if degree < 1:
        raise ValueError(f"степень должна быть не меньше 1, получено {degree}")

    if len(control_points) < 2:
        return [] if not control_points else [control_points[0]]

    degree = min(degree, len(control_points) - 1)

    output: list[Vec2] = []
    point_count = len(control_points) - 1

    to_flatten, degree = _bspline_to_bezier_internal(control_points, degree)
    free_buffers: list[list[Vec2]] = []

    subdivision_buffer1: list[Vec2] = [Vec2(0.0, 0.0)] * (degree + 1)
    subdivision_buffer2: list[Vec2] = [Vec2(0.0, 0.0)] * (degree * 2 + 1)
    left_child = subdivision_buffer2

    while to_flatten:
        parent = to_flatten.pop()

        if _bezier_is_flat_enough(parent):
            _bezier_approximate(parent, output, subdivision_buffer1, subdivision_buffer2, degree + 1)
            free_buffers.append(parent)
            continue

        right_child = free_buffers.pop() if free_buffers else [Vec2(0.0, 0.0)] * (degree + 1)
        _bezier_subdivide(parent, left_child, right_child, subdivision_buffer1, degree + 1)

        # Буфер родителя переиспользуется под левого потомка.
        for i in range(degree + 1):
            parent[i] = left_child[i]

        to_flatten.append(right_child)
        to_flatten.append(parent)

    output.append(control_points[point_count])
    return output


def _bezier_is_flat_enough(control_points: list[Vec2]) -> bool:
    """Вторая производная (через конечные разности) в пределах допуска."""
    # BEZIER_TOLERANCE * BEZIER_TOLERANCE * 4 == 0.25 ровно.
    threshold = f32(f32(BEZIER_TOLERANCE * BEZIER_TOLERANCE) * 4)

    for i in range(1, len(control_points) - 1):
        delta = control_points[i - 1] - control_points[i] * 2 + control_points[i + 1]
        if delta.length_squared > threshold:
            return False

    return True


def _bezier_subdivide(
    control_points: list[Vec2],
    left: list[Vec2],
    right: list[Vec2],
    subdivision_buffer: list[Vec2],
    count: int,
) -> None:
    """Делит кривую безье пополам алгоритмом де Кастельжо.

    ВНИМАНИЕ: вызывающий код в _bezier_approximate передаёт один и тот же список
    как right и как subdivision_buffer. Это не ошибка, а намеренное совмещение
    буферов из оригинала: присваивание right[...] = midpoints[...] становится
    самоприсваиванием, а нужные значения хвоста к этому моменту уже на месте.
    Логику ниже нельзя «упрощать» — она обязана работать и при совпадении списков.
    """
    midpoints = subdivision_buffer

    for i in range(count):
        midpoints[i] = control_points[i]

    for i in range(count):
        left[i] = midpoints[0]
        right[count - i - 1] = midpoints[count - i - 1]

        for j in range(count - i - 1):
            midpoints[j] = (midpoints[j] + midpoints[j + 1]) / 2


def _bezier_approximate(
    control_points: list[Vec2],
    output: list[Vec2],
    subdivision_buffer1: list[Vec2],
    subdivision_buffer2: list[Vec2],
    count: int,
) -> None:
    """Ломаная по кривой безье через де Кастельжо."""
    left = subdivision_buffer2
    right = subdivision_buffer1

    # right и subdivision_buffer — один и тот же список, см. _bezier_subdivide.
    _bezier_subdivide(control_points, left, right, subdivision_buffer1, count)

    for i in range(count - 1):
        left[count + i] = right[i + 1]

    output.append(control_points[0])

    for i in range(1, count - 1):
        index = 2 * i
        point = (left[index - 1] + left[index] * 2 + left[index + 1]) * f32(0.25)
        output.append(point)


def catmull_to_piecewise_linear(control_points: list[Vec2]) -> list[Vec2]:
    """PathApproximator.CatmullToPiecewiseLinear.

    Каждая пара точек даёт CATMULL_DETAIL сегментов, причём точки добавляются
    парами (начало и конец сегмента), из-за чего они дублируются. Так в оригинале.
    """
    result: list[Vec2] = []

    for i in range(len(control_points) - 1):
        v1 = control_points[i - 1] if i > 0 else control_points[i]
        v2 = control_points[i]
        v3 = control_points[i + 1] if i < len(control_points) - 1 else v2 + v2 - v1
        v4 = control_points[i + 2] if i < len(control_points) - 2 else v3 + v3 - v2

        for c in range(CATMULL_DETAIL):
            result.append(_catmull_find_point(v1, v2, v3, v4, f32(c / CATMULL_DETAIL)))
            result.append(_catmull_find_point(v1, v2, v3, v4, f32((c + 1) / CATMULL_DETAIL)))

    return result


def _catmull_component(a: float, b: float, c: float, d: float, t: float, t2: float, t3: float) -> float:
    """Одна координата точки Catmull-сплайна.

    В C# это выражение целиком состоит из float-операндов, поэтому округление
    до float32 происходит на КАЖДОЙ бинарной операции, а не один раз в конце.
    Считать скобки в double и округлить результат — не то же самое: разница
    смещает внутренние точки ломаной, а итоговая длина при этом сохраняется,
    потому что подгоняется под ExpectedDistance. Ловится только на реальных
    картах с Catmull-слайдерами.
    """
    # 2f * b
    term0 = f32(2.0 * b)
    # (-a + c) * t
    term1 = f32(f32(-a + c) * t)
    # (2f*a - 5f*b + 4f*c - d) * t2
    inner2 = f32(f32(f32(f32(2.0 * a) - f32(5.0 * b)) + f32(4.0 * c)) - d)
    term2 = f32(inner2 * t2)
    # (-a + 3f*b - 3f*c + d) * t3
    inner3 = f32(f32(f32(f32(-a) + f32(3.0 * b)) - f32(3.0 * c)) + d)
    term3 = f32(inner3 * t3)

    total = f32(f32(f32(term0 + term1) + term2) + term3)
    return f32(0.5 * total)


def _catmull_find_point(v1: Vec2, v2: Vec2, v3: Vec2, v4: Vec2, t: float) -> Vec2:
    t2 = f32(t * t)
    t3 = f32(t * t2)

    return Vec2(
        _catmull_component(v1.x, v2.x, v3.x, v4.x, t, t2, t3),
        _catmull_component(v1.y, v2.y, v3.y, v4.y, t, t2, t3),
    )


class CircularArcProperties:
    """Параметры дуги, описанной вокруг трёх точек."""

    __slots__ = ("is_valid", "theta_start", "theta_range", "direction", "radius", "centre")

    def __init__(self, control_points: list[Vec2]) -> None:
        a, b, c = control_points[0], control_points[1], control_points[2]

        # Вырожденный треугольник: дуги нет, вызывающий код откатится на безье.
        cross = f32(f32(f32(b.y - a.y) * f32(c.x - a.x)) - f32(f32(b.x - a.x) * f32(c.y - a.y)))
        if math.fabs(cross) < _FLOAT_EPSILON:
            self.is_valid = False
            self.theta_start = 0.0
            self.theta_range = 0.0
            self.direction = 0.0
            self.radius = 0.0
            self.centre = Vec2(0.0, 0.0)
            return

        d = f32(2 * f32(f32(a.x * (b - c).y) + f32(b.x * (c - a).y) + f32(c.x * (a - b).y)))
        a_sq = a.length_squared
        b_sq = b.length_squared
        c_sq = c.length_squared

        self.centre = Vec2(
            f32(f32(a_sq * (b - c).y) + f32(b_sq * (c - a).y) + f32(c_sq * (a - b).y)),
            f32(f32(a_sq * (c - b).x) + f32(b_sq * (a - c).x) + f32(c_sq * (b - a).x)),
        ) / d

        d_a = a - self.centre
        d_c = c - self.centre

        self.radius = d_a.length

        # Углы считаются в double: Math.Atan2 принимает и возвращает double.
        self.theta_start = math.atan2(d_a.y, d_a.x)
        theta_end = math.atan2(d_c.y, d_c.x)

        while theta_end < self.theta_start:
            theta_end += 2 * math.pi

        self.direction = 1.0
        self.theta_range = theta_end - self.theta_start

        # Направление обхода зависит от того, с какой стороны от AC лежит B.
        ortho_a_to_c = c - a
        ortho_a_to_c = Vec2(ortho_a_to_c.y, -ortho_a_to_c.x)

        if ortho_a_to_c.dot(b - a) < 0:
            self.direction = -self.direction
            self.theta_range = 2 * math.pi - self.theta_range

        self.is_valid = True

    @property
    def theta_end(self) -> float:
        return self.theta_start + self.theta_range * self.direction


def circular_arc_to_piecewise_linear(control_points: list[Vec2]) -> list[Vec2]:
    """PathApproximator.CircularArcToPiecewiseLinear."""
    pr = CircularArcProperties(control_points)
    if not pr.is_valid:
        return bezier_to_piecewise_linear(control_points)

    amount_points = arc_point_count(pr)

    output: list[Vec2] = []
    for i in range(amount_points):
        fract = i / (amount_points - 1)
        theta = pr.theta_start + pr.direction * fract * pr.theta_range
        offset = Vec2(f32(math.cos(theta)), f32(math.sin(theta))) * pr.radius
        output.append(pr.centre + offset)

    return output


def arc_point_count(pr: CircularArcProperties) -> int:
    """Число точек аппроксимации дуги: столько, чтобы кривизна была в допуске.

    В C# написано `1f - (0.1f / Radius)` — и допуск, и радиус float, поэтому
    деление и вычитание идут во float32, и только результат расширяется для Acos.
    В double получается иногда на точку меньше, а с ней другая форма ломаной.

    Вынесено отдельно: SliderPath той же формулой отсекает вырожденные дуги.
    """
    if f32(2 * pr.radius) <= CIRCULAR_ARC_TOLERANCE:
        return 2

    inner = f32(1 - f32(CIRCULAR_ARC_TOLERANCE / pr.radius))
    denominator = 2 * math.acos(inner)

    if denominator == 0:
        # При радиусе от ~3.4 млн частное 0.1f/radius проваливается ниже ulp
        # единицы, inner становится ровно 1.0f и арккосинус даёт ноль. C# делит
        # на ноль: бесконечность насыщается `(int)` в int.MaxValue, и порог
        # в 1000 точек отправляет дугу в безье; NaN дал бы 0, то есть 2 точки.
        return 2 if pr.theta_range == 0 else _INT32_MAX

    return max(2, math.ceil(pr.theta_range / denominator))
