"""Легаси-скоринг: величины, нужные для оценки промахов по сумме очков.

Источник: osu.Game/Rulesets/Objects/Legacy/LegacyRulesetExtensions.cs,
          osu.Game.Rulesets.Osu/Difficulty/Utils/LegacyScoreUtils.cs,
          osu.Game.Rulesets.Osu/Difficulty/OsuLegacyScoreSimulator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Всё здесь нужно ровно для одного: у классического скора слайдербрейк
неотличим от потерянного хвоста, и различить их можно только по сумме очков.
Для этого нужно знать, сколько очков карта даёт в принципе.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING

from ..beatmap.objects import NestedType, Slider, Spinner
from .utils import c_int_div

if TYPE_CHECKING:
    from ..beatmap.decoder import Beatmap

__all__ = [
    "difficulty_peppy_stars",
    "drain_length",
    "maximum_legacy_combo_score",
    "nested_score_per_object",
]

#: Точность decimal в .NET — 96-битная мантисса, то есть 28-29 значащих цифр.
_DECIMAL_PRECISION = 28

#: Приведение double -> decimal в C# оставляет 15 значащих цифр
#: (конструктор decimal(double)), а не все разряды double.
_DOUBLE_TO_DECIMAL_DIGITS = 15


def _to_decimal(value: float) -> Decimal:
    """`(decimal)(double)x` из C#.

    Приведения к double в оригинале стоят не случайно: они мешают компилятору
    «поправить» значение одинарной точности при переходе в decimal. Само же
    приведение double -> decimal оставляет 15 значащих цифр, поэтому
    float32-значение вроде 4.2 приходит сюда как 4.19999980926514.
    """
    return Decimal(f"{value:.{_DOUBLE_TO_DECIMAL_DIGITS}g}")


def drain_length(beatmap: Beatmap) -> int:
    """Длительность карты за вычетом брейков, в секундах.

    Все округления банковские, деление на 1000 целочисленное — как в C#,
    где это `(int)Math.Round(...)` и целочисленное `/ 1000`.

    ВНИМАНИЕ: деление именно c_int_div. Брейки могут быть длиннее пролёта карты,
    и тогда разность отрицательна: `-700 / 1000` в C# даёт 0, а Python `//` — -1.
    Ноль и минус единица расходятся дальше по всей цепочке — в peppy stars
    ratio уходит с 16 на 0, множитель ScoreV1 меняется вдвое, и оценка промахов
    по сумме очков даёт другое pp (проба legacy-drain-underflow).
    """
    if not beatmap.hit_objects:
        return 0

    break_length = sum(round(b.end_time) - round(b.start_time) for b in beatmap.breaks)
    span = round(beatmap.hit_objects[-1].start_time) - round(beatmap.hit_objects[0].start_time)
    return c_int_div(span - break_length, 1000)


def difficulty_peppy_stars(beatmap: Beatmap, object_count: int, length: int) -> int:
    """LegacyRulesetExtensions.CalculateDifficultyPeppyStars.

    ВНИМАНИЕ: считается в decimal, а не в double, и это не оптимизация.
    Stable выполнял эту арифметику на регистрах x87 шириной 80 бит — шире
    и float, и double. .NET считает на SSE, поэтому ppy эмулирует ширину
    через decimal. Комментарий в оригинале начинается со слов
    «DO NOT TOUCH IF YOU DO NOT KNOW WHAT YOU ARE DOING».
    """
    with localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION

        if length != 0:
            ratio = Decimal(object_count) / Decimal(length) * 8
            ratio = min(max(ratio, Decimal(0)), Decimal(16))
        else:
            ratio = Decimal(16)

        total = (
            _to_decimal(beatmap.drain_rate)
            + _to_decimal(beatmap.overall_difficulty)
            + _to_decimal(beatmap.circle_size)
            + ratio
        ) / 38 * 5

        # Math.Round у decimal округляет половину к чётному.
        return int(total.quantize(Decimal(1)))


def nested_score_per_object(beatmap: Beatmap, object_count: int) -> float:
    """LegacyScoreUtils.CalculateNestedScorePerObject.

    Средний счёт за вложенные объекты — тики, реверсы и вращения спиннеров.
    """
    big_tick_score = 30
    small_tick_score = 10

    big_ticks = 0
    small_ticks = 0
    spinner_score = 0.0

    for obj in beatmap.hit_objects:
        if isinstance(obj, Slider):
            # Голова, хвост и все повторы.
            big_ticks += 2 + obj.repeat_count
            small_ticks += sum(1 for nested in obj.nested if nested.type is NestedType.TICK)
        elif isinstance(obj, Spinner):
            spinner_score += _spinner_score(obj)

    slider_score = big_ticks * big_tick_score + small_ticks * small_tick_score
    return (slider_score + spinner_score) / object_count


def _spinner_score(spinner: Spinner) -> float:
    """Очки за спиннер по механике stable.

    Берётся худший случай по числу бонусных вращений: оригинал сознательно
    занижает бонус, чтобы не переоценить максимум.
    """
    spin_score = 100
    bonus_spin_score = 1000

    maximum_rotations_per_second = 477.0 / 60
    minimum_rotations_per_second = 3

    seconds_duration = spinner.duration / 1000

    total_half_spins = int(seconds_duration * maximum_rotations_per_second * 2)
    half_spins_for_completion = int(seconds_duration * minimum_rotations_per_second)
    half_spins_before_bonus = half_spins_for_completion + 3

    full_spins = total_half_spins // 2
    score = spin_score * full_spins

    bonus_spins = (total_half_spins - half_spins_before_bonus) // 2
    bonus_spins = max(0, bonus_spins - full_spins // 2)

    return float(score + bonus_spin_score * bonus_spins)


def maximum_legacy_combo_score(beatmap: Beatmap, score_multiplier: float) -> int:
    """OsuLegacyScoreSimulator.Simulate — только ComboScore.

    Комбо-часть ScoreV1: каждое попадание умножается на текущий множитель комбо,
    поэтому итог зависит не от того, сколько комбо потеряно, а от того, как оно
    раздроблено. Именно на этом держится оценка промахов по сумме очков.

    Остальное, что считает симулятор (бонусы спиннеров, MaxCombo), нам не нужно.
    """
    state = _ComboState(score_multiplier=score_multiplier)
    for hit_object in beatmap.hit_objects:
        _simulate_hit(hit_object, state)
    return state.combo_score


@dataclass
class _ComboState:
    score_multiplier: float
    combo: int = 0
    combo_score: int = 0


def _add_combo_score(state: _ComboState, score_increase: int) -> None:
    """`ComboScore += (int)(Max(0, combo - 1) * (scoreIncrease / 25 * scoreMultiplier))`.

    ВНИМАНИЕ: `scoreIncrease / 25` в C# — деление НАЦЕЛО, оба операнда int.
    В оригинале это помечено `ReSharper disable once PossibleLossOfFraction`
    с пометкой «intentional to match osu-stable». Тик слайдера даёт 10 // 25 = 0,
    то есть в комбо-часть счёта не попадает вовсе.
    """
    state.combo_score += int(max(0, state.combo - 1) * (score_increase // 25 * state.score_multiplier))


def _simulate_hit(hit_object, state: _ComboState) -> None:
    """OsuLegacyScoreSimulator.simulateHit."""
    if isinstance(hit_object, Slider):
        # Вложенные считаются первыми и наращивают комбо, поэтому сам слайдер
        # его уже не увеличивает — голова это сделала за него.
        for nested in hit_object.nested:
            _simulate_nested(nested, state)
        _add_combo_score(state, 300)
        return

    if isinstance(hit_object, Spinner):
        _simulate_spinner_ticks(hit_object, state)
        _add_combo_score(state, 300)
        state.combo += 1
        return

    # Круг.
    _add_combo_score(state, 300)
    state.combo += 1


def _simulate_nested(nested, state: _ComboState) -> None:
    """Голова, хвост, реверс дают 30 очков, тик — 10; комбо растёт у всех.

    В комбо-часть счёта они не попадают: addScoreComboMultiplier у них false.
    """
    state.combo += 1


def _simulate_spinner_ticks(spinner: Spinner, state: _ComboState) -> None:
    """Вращения спиннера комбо не наращивают и комбо-очков не дают.

    Цикл оставлен для наглядности соответствия оригиналу: там каждая половина
    оборота проходит через simulateHit, но с increaseCombo = false
    и addScoreComboMultiplier = false, то есть на ComboScore не влияет.
    """
    return
