"""Одинарная точность там, где она есть в osu!.

Прямого аналога в C# нет: там float32 обеспечивает система типов, а в Python всё
по умолчанию double. Правило — **float32 только там, где в C# написано `float`**:
это геометрия (osuTK.Vector2) и несколько скаляров вроде Scale. Вся
difficulty-математика идёт в double и трогать её не нужно.
"""

from __future__ import annotations

import math
from ctypes import c_float

__all__ = ["f32", "Vec2"]


def f32(x: float) -> float:
    """Округляет до ближайшего float32 — как C# при присваивании double во float."""
    return c_float(x).value


class Vec2:
    """Аналог osuTK.Vector2: две компоненты float32.

    Округление на каждой операции, а не только в конце: промежуточные значения
    в C# тоже усечены, и схлопывать их нельзя.
    """

    __slots__ = ("x", "y")

    def __init__(self, x: float = 0.0, y: float = 0.0) -> None:
        self.x = f32(x)
        self.y = f32(y)

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> Vec2:
        return Vec2(self.x * scalar, self.y * scalar)

    __rmul__ = __mul__

    def __truediv__(self, scalar: float) -> Vec2:
        return Vec2(self.x / scalar, self.y / scalar)

    def __neg__(self) -> Vec2:
        return Vec2(-self.x, -self.y)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Vec2) and self.x == other.x and self.y == other.y

    def __hash__(self) -> int:
        return hash((self.x, self.y))

    def __repr__(self) -> str:
        return f"Vec2({self.x!r}, {self.y!r})"

    @property
    def length(self) -> float:
        """osuTK: `(float)Math.Sqrt(X * X + Y * Y)`.

        Три отдельных округления: произведения и сумма во float32, корень
        в double и снова во float32.
        """
        return f32(math.sqrt(f32(f32(self.x * self.x) + f32(self.y * self.y))))

    @property
    def length_squared(self) -> float:
        return f32(f32(self.x * self.x) + f32(self.y * self.y))

    def distance(self, other: Vec2) -> float:
        return (self - other).length

    def dot(self, other: Vec2) -> float:
        return f32(f32(self.x * other.x) + f32(self.y * other.y))

    def normalised(self) -> Vec2:
        """osuTK.Vector2.Normalize.

        Умножение на обратную длину, а НЕ деление: `x * (1/L)` в плавающей точке
        не равно `x / L`, и разница переносится в последнюю точку пути при
        подгонке под ExpectedDistance. При нулевой длине C# получает
        scale = +Infinity, поэтому она подставляется явно.
        """
        length = self.length
        scale = f32(1.0 / length) if length != 0.0 else math.inf
        return Vec2(self.x * scale, self.y * scale)
