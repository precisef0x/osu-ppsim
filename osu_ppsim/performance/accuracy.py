"""Раскладка accuracy в попадания и обратный пересчёт.

Источник: osu-tools/PerformanceCalculator/Simulate/OsuSimulateCommand.cs
Пин: ppy/osu-tools @ f8bc6aa7

Единственное место в порте, где логика берётся не из самой игры: она считает
accuracy по реальному скору, а нам нужно обратное — подобрать расклад попаданий
под заданную точность.

Точностей две. «Сырая» считается по кругам, её принимает osu-tools в ключе -a;
видимая игроку учитывает ещё хвосты слайдеров (вес 3) и тики (вес 0.6):

    acc = (6*300 + 2*100 + 50 + 3*хвосты + 0.6*тики) / (6*n + 3*слайдеры + 0.6*тики)

При FC хвосты и тики собраны полностью и тянут точность вверх.

Под CL этого разделения нет: GenerateHitResults не кладёт в статистику ни
хвостов, ни тиков, поэтому GetAccuracy складывает одни круги — то есть обе
точности совпадают и равны «сырой». В score_accuracy это выражено нулевыми
счётчиками, ровно как отсутствие ключей в статистике у C#.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["HitCounts", "hit_counts_for_raw_accuracy", "score_accuracy", "raw_accuracy_for_display"]

#: Веса в формуле точности lazer.
_SLIDER_TAIL_WEIGHT: Final[float] = 3.0
_LARGE_TICK_WEIGHT: Final[float] = 0.6


@dataclass(frozen=True)
class HitCounts:
    """Расклад попаданий при FC: промахов нет, комбо максимальное."""

    great: int
    ok: int
    meh: int
    miss: int = 0

    @property
    def total(self) -> int:
        return self.great + self.ok + self.meh + self.miss


def hit_counts_for_raw_accuracy(total_objects: int, raw_accuracy: float) -> HitCounts:
    """OsuSimulateCommand.generateHitResults для случая без промахов.

    Точность здесь «сырая» — только по кругам, без хвостов и тиков. Именно её
    принимает osu-tools в ключе -a, поэтому функция нужна для сверки с оракулом.
    """
    relevant = total_objects
    if relevant == 0:
        return HitCounts(great=0, ok=0, meh=0, miss=0)

    # Круг умножения и деления НЕ лишний, хотя при FC знаменатель равен числителю:
    # `0.95 * 543 / 543` даёт 0.9500000000000001, и когда оценка числа соток
    # попадает ровно на половину, последнего бита хватает, чтобы округление ушло
    # в другую сторону. На pp это 0.4%.
    accuracy = min(max(raw_accuracy * total_objects / relevant, 0.0), 1.0)

    if accuracy >= 0.25:
        # Основная кривая: чем ближе точность к 25%, тем больше пятидесяток.
        # При 100% пятидесяток нет; при 75% — одна на девять соток.
        ratio_50_to_100 = pow(1 - (accuracy - 0.25) / 0.75, 2)

        # Выведено из acc = (6*c300 + 2*c100 + c50) / (6*n) при c50 = c100 * ratio.
        count_100 = 6 * relevant * (1 - accuracy) / (5 * ratio_50_to_100 + 4)
        count_50 = count_100 * ratio_50_to_100

        ok = round(count_100)
        meh = round(count_100 + count_50) - ok
    elif accuracy >= 1.0 / 6:
        # Между 16.67% и 25% считаем, что троек нет вовсе.
        count_100 = 6 * relevant * accuracy - relevant
        count_50 = relevant - count_100

        ok = round(count_100)
        meh = round(count_100 + count_50) - ok
    else:
        # Ниже 16.67% остаются только пятидесятки. Дополнять промахами мы не
        # можем — это перестало бы быть FC, поэтому просто отдаём максимум мимо.
        ok = 0
        meh = min(round(6 * relevant * accuracy), relevant)

    ok = max(0, ok)
    meh = max(0, meh)
    great = total_objects - ok - meh
    if great < 0:
        # Округления могли выдать больше промахов по кругам, чем есть объектов.
        meh = max(0, total_objects - ok)
        great = total_objects - ok - meh

    return HitCounts(great=great, ok=ok, meh=meh, miss=0)


def score_accuracy(counts: HitCounts, slider_count: int, large_tick_count: int) -> float:
    """OsuSimulateCommand.GetAccuracy при FC.

    Хвосты и тики собраны полностью, поэтому входят и в числитель, и в знаменатель.
    Под CL их в статистике нет вовсе — тогда оба счётчика нулевые.
    """
    total = 6 * counts.great + 2 * counts.ok + counts.meh
    maximum = 6 * (counts.great + counts.ok + counts.meh + counts.miss)

    total += _SLIDER_TAIL_WEIGHT * slider_count
    maximum += _SLIDER_TAIL_WEIGHT * slider_count

    total += _LARGE_TICK_WEIGHT * large_tick_count
    maximum += _LARGE_TICK_WEIGHT * large_tick_count

    return total / maximum if maximum else 0.0


def raw_accuracy_for_display(
    display_accuracy: float, total_objects: int, slider_count: int, large_tick_count: int
) -> float:
    """Переводит видимую игроку точность в «сырую» по кругам.

    Обращение формулы score_accuracy: хвосты и тики дают одинаковую добавку
    и в числитель, и в знаменатель, поэтому

        сырая = (видимая * (6n + C) - C) / (6n),  где C = 3*слайдеры + 0.6*тики
    """
    constant = _SLIDER_TAIL_WEIGHT * slider_count + _LARGE_TICK_WEIGHT * large_tick_count
    denominator = 6 * total_objects

    if denominator == 0:
        return 0.0

    return (display_accuracy * (denominator + constant) - constant) / denominator
