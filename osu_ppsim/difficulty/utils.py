"""Математика расчёта сложности.

Источник: osu.Game/Rulesets/Difficulty/Utils/DiffUtils.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

ВАЖНО про степень. В C# у Pow две перегрузки, и выбор между ними делает
компилятор по типу литерала:

    Pow(x, 0.3)  -> Pow(double, double) -> Math.Pow
    Pow(x, 5)    -> Pow(double, int)    -> x*x*x*x*x повторным умножением

Результаты отличаются в младших разрядах, поэтому здесь две отдельные функции,
и на каждом месте вызова надо смотреть в исходник, какая перегрузка сработала.
"""

from __future__ import annotations

import math

__all__ = [
    "SQRT2",
    "clamp",
    "pow_double",
    "pow_int",
    "lerp",
    "reverse_lerp",
    "smoothstep",
    "smootherstep",
    "logistic",
    "logistic_exp",
    "norm",
    "smoothstep_bell_curve_unit",
    "erf",
    "erf_inv",
    "sequential_sum",
    "c_div",
    "c_int_div",
    "bpm_to_milliseconds",
    "milliseconds_to_bpm",
]

#: DiffUtils.SQRT2. Записан константой, как в C#, а не посчитан — младшие разряды важны.
SQRT2 = 1.4142135623730950


def clamp(value: float, low: float, high: float) -> float:
    """Math.Clamp.

    Записано условиями, а не через min/max: в Python вызов встроенной функции
    дороже самого сравнения, а этих зажимов на карту сотни тысяч. На NaN обе
    записи ведут себя одинаково — оба сравнения ложны, и значение проходит
    насквозь, как и требует .NET. Проверено перебором по краевым значениям.
    """
    return low if value < low else (high if value > high else value)


def c_div(numerator: float, denominator: float) -> float:
    """Деление в семантике C#: на ноль даёт бесконечность или NaN.

    Python на этом падает, а C# спокойно продолжает — и дальше по цепочке
    бесконечность обычно превращается в NaN, а NaN в первую точку пути.
    """
    if denominator != 0:
        return numerator / denominator
    if numerator == 0 or numerator != numerator:
        return math.nan
    return math.inf if numerator > 0 else -math.inf


def c_int_div(numerator: int, denominator: int) -> int:
    """Целочисленное деление в семантике C#: усечение К НУЛЮ.

    Python `//` округляет вниз, поэтому на отрицательных числах расходится:
    `-1261 // 2` даёт -631, а C# `-1261 / 2` — ровно -630.

    Делимое уходит в минус в одном месте: оценка промахов по сумме очков
    получает комбо НЕзажатым, и на комбо выше максимума карты разность
    отрицательна. На pp это не влияет — отрицательная оценка всё равно
    зажимается в ноль, — но сама величина отдаётся наружу и обязана совпадать
    с оракулом.
    """
    quotient = abs(numerator) // abs(denominator)
    return quotient if (numerator >= 0) == (denominator >= 0) else -quotient


def bpm_to_milliseconds(bpm: float, delimiter: int = 4) -> float:
    """DiffUtils.BPMToMilliseconds."""
    return 60000.0 / delimiter / bpm


def milliseconds_to_bpm(ms: float, delimiter: int = 4) -> float:
    """DiffUtils.MillisecondsToBPM."""
    return 60000.0 / (ms * delimiter)


def pow_double(x: float, exponent: float) -> float:
    """DiffUtils.Pow(double, double)."""
    return math.pow(x, exponent)


def pow_int(x: float, exponent: int) -> float:
    """DiffUtils.Pow(double, int): до пятой степени повторным умножением.

    Это не то же самое, что Math.Pow, в младших разрядах.
    """
    match exponent:
        case 0:
            return 1.0
        case 1:
            return x
        case 2:
            return x * x
        case 3:
            return x * x * x
        case 4:
            return x * x * x * x
        case 5:
            return x * x * x * x * x
        case _:
            return math.pow(x, exponent)


def lerp(start: float, final: float, amount: float) -> float:
    """Interpolation.Lerp."""
    return start + (final - start) * amount


def reverse_lerp(x: float, start: float, end: float) -> float:
    """DiffUtils.ReverseLerp."""
    # clamp развёрнут вручную: эти функции дают большую часть из трёхсот тысяч
    # его вызовов на карту, а вызов в Python дороже самого сравнения.
    x = (x - start) / (end - start)
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def smoothstep(x: float, start: float, end: float) -> float:
    """DiffUtils.Smoothstep."""
    x = (x - start) / (end - start)
    x = 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)
    return x * x * (3.0 - 2.0 * x)


def smootherstep(x: float, start: float, end: float) -> float:
    """DiffUtils.Smootherstep."""
    x = (x - start) / (end - start)
    x = 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)
    return x * x * x * (x * (6.0 * x - 15.0) + 10.0)


def logistic(x: float, midpoint_offset: float, multiplier: float, max_value: float = 1.0) -> float:
    """DiffUtils.Logistic(x, midpointOffset, multiplier, maxValue)."""
    return max_value / (1 + math.exp(multiplier * (midpoint_offset - x)))


def logistic_exp(exponent: float, max_value: float = 1.0) -> float:
    """DiffUtils.Logistic(exponent, maxValue) — перегрузка с двумя аргументами."""
    return max_value / (1 + math.exp(exponent))


def norm(p: float, *values: float) -> float:
    """DiffUtils.Norm: p-норма вектора.

    Степень здесь double, поэтому обе Pow — через math.pow.
    """
    total = 0.0
    for x in values:
        total += pow_double(x, p)
    return pow_double(total, 1.0 / p)


def smoothstep_bell_curve_unit(x: float) -> float:
    """DiffUtils.SmoothstepBellCurve(x) — вариант с единичным носителем."""
    x = 0.5 - abs(x - 0.5)
    x = x * 2.0
    x = 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)
    return x * x * (3.0 - 2.0 * x)


def erf(x: float) -> float:
    """DiffUtils.Erf: приближение Абрамовица и Стигана, формула 7.1.26.

    Именно оно, а не math.erf: у стандартной реализации другие младшие разряды,
    а от них зависит оценка девиации в pp-формуле.
    """
    if x == 0:
        return 0.0
    if x == math.inf:
        return 1.0
    if x == -math.inf:
        return -1.0
    if math.isnan(x):
        return math.nan

    t = 1.0 / (1.0 + 0.3275911 * abs(x))
    tau = t * (
        0.254829592
        + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )

    value = 1.0 - tau * math.exp(-x * x)
    return value if x >= 0 else -value


def erf_inv(x: float) -> float:
    """DiffUtils.ErfInv."""
    if x <= -1:
        return -math.inf
    if x >= 1:
        return math.inf
    if x == 0:
        return 0.0

    a = 0.147
    sgn = 1.0 if x > 0 else -1.0
    x = abs(x)

    ln = math.log(1 - x * x)
    t1 = 2 / (math.pi * a) + ln / 2
    t2 = ln / a
    base_approx = math.sqrt(t1 * t1 - t2) - t1

    # Поправка снижает максимальную погрешность с -0.005 до -0.00045.
    # Степень 8 выходит за диапазон таблицы повторных умножений, поэтому math.pow.
    correction = pow_int((x - 0.85) / 0.293, 8) if x >= 0.85 else 0.0
    return sgn * (math.sqrt(base_approx) + correction)


def sequential_sum(values) -> float:
    """Последовательное сложение — как Enumerable.Sum в C#.

    Встроенную sum() использовать НЕЛЬЗЯ: с Python 3.12 она применяет
    компенсационное суммирование Ноймайера и даёт результат ТОЧНЕЕ, чем C#.
    """
    total = 0.0
    for value in values:
        total += value
    return total
