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

__all__ = [
    "HitCounts",
    "Score",
    "InvalidScoreError",
    "validate_score",
    "hit_counts_for_raw_accuracy",
    "score_accuracy",
    "raw_accuracy_for_display",
]

#: Веса в формуле точности lazer.
_SLIDER_TAIL_WEIGHT: Final[float] = 3.0
_LARGE_TICK_WEIGHT: Final[float] = 0.6


@dataclass(frozen=True)
class HitCounts:
    """Расклад попаданий по основным объектам карты, включая промахи."""

    great: int
    ok: int
    meh: int
    miss: int = 0

    @property
    def total(self) -> int:
        return self.great + self.ok + self.meh + self.miss


@dataclass(frozen=True)
class Score:
    """Что игрок сделал на карте — вход расчёта pp (аналог ScoreInfo).

    Всё, кроме попаданий, необязательно и по умолчанию описывает FC:
    комбо максимальное, хвосты собраны, тики не потеряны.

    `slider_tail_hits` и `large_tick_misses` участвуют только в лазерной
    механике. Под CL расчёт их игнорирует, но валидация проверяет границы по карте.
    """

    counts: HitCounts
    #: None означает максимальное комбо карты.
    max_combo: int | None = None
    #: None означает «собраны все хвосты».
    slider_tail_hits: int | None = None
    large_tick_misses: int = 0
    #: Сумма очков ScoreV1. Есть только у скоров из stable; вместе с модом CL
    #: включает оценку промахов по очкам вместо оценки по комбо.
    legacy_total_score: int | None = None


class InvalidScoreError(ValueError):
    """Скор невозможен на этой карте: числа противоречат карте или друг другу."""


def validate_score(
    score: Score,
    *,
    total_objects: int,
    beatmap_max_combo: int,
    slider_count: int,
    large_tick_count: int,
    classic_slider_accuracy: bool = False,
) -> None:
    """Проверяет границы счётчиков и необходимые условия согласованности скора.

    Сами Score и HitCounts — просто записи, без контекста карты они проверить
    ничего не могут (и НЕ проверяют даже отрицательные значения: гейты нарочно
    собирают мусорные скоры, чтобы сверить с оракулом поведение вне разумной
    области). Вся проверка собрана здесь и вызывается там, где карта известна, —
    в Simulator.score().

    None у комбо означает максимум карты, у хвостов — все собранные хвосты;
    эти значения проверяются наравне с явными. Под classic_slider_accuracy
    поля хвостов и тиков ограничены картой, но не участвуют в границе комбо.

    Проверка не восстанавливает порядок попаданий и не доказывает достижимость
    прошедшего скора. Проверяются только неоспоримые противоречия. Порогов вида
    «столько соток при таком комбо подозрительно» тут нет и не будет: ложный
    отказ на настоящем скоре хуже, чем пропущенный мусор.
    """
    counts = score.counts
    for name, value in (
        ("great", counts.great),
        ("ok", counts.ok),
        ("meh", counts.meh),
        ("miss", counts.miss),
        ("max_combo", score.max_combo),
        ("slider_tail_hits", score.slider_tail_hits),
        ("large_tick_misses", score.large_tick_misses),
        ("legacy_total_score", score.legacy_total_score),
    ):
        if value is not None and value < 0:
            raise InvalidScoreError(f"negative {name}: {value}")

    if counts.total != total_objects:
        raise InvalidScoreError(
            f"hit counts cover {counts.total} objects, but the beatmap has {total_objects}"
        )

    if score.slider_tail_hits is not None and score.slider_tail_hits > slider_count:
        raise InvalidScoreError(
            f"slider_tail_hits {score.slider_tail_hits} exceeds the beatmap's {slider_count} sliders"
        )

    if score.large_tick_misses > large_tick_count:
        raise InvalidScoreError(
            f"large_tick_misses {score.large_tick_misses} exceeds "
            f"the beatmap's {large_tick_count} ticks and repeats"
        )

    # Значения по умолчанию проверяются в том же смысле, в котором их
    # использует расчёт: None — максимум карты и все собранные хвосты.
    max_combo = beatmap_max_combo if score.max_combo is None else score.max_combo
    tail_hits = slider_count if score.slider_tail_hits is None else score.slider_tail_hits
    if max_combo > beatmap_max_combo:
        raise InvalidScoreError(
            f"max_combo {max_combo} exceeds the beatmap's maximum {beatmap_max_combo}"
        )

    # Каждая потеря убирает хотя бы одну единицу комбо. Это верхняя граница,
    # а не восстановление достигнутого комбо: порядок попаданий неизвестен.
    lost_combo = counts.miss
    anything_hit = counts.total > counts.miss
    if not classic_slider_accuracy:
        lost_combo += score.large_tick_misses + slider_count - tail_hits
        anything_hit = anything_hit or tail_hits > 0 or large_tick_count > score.large_tick_misses

    combo_limit = beatmap_max_combo - lost_combo
    if max_combo > combo_limit:
        raise InvalidScoreError(
            f"max_combo {max_combo} is impossible with these losses: "
            f"at most {combo_limit} combo units remain"
        )
    if max_combo == 0 and anything_hit:
        raise InvalidScoreError("max_combo 0 is impossible when anything was hit: the first hit makes it 1")


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


def score_accuracy(
    counts: HitCounts,
    slider_count: int,
    large_tick_count: int,
    slider_tail_hits: int | None = None,
    large_tick_misses: int = 0,
) -> float:
    """OsuSimulateCommand.GetAccuracy.

    В знаменатель идут все хвосты и тики карты, в числитель — только собранные.
    `slider_tail_hits = None` означает «собраны все», то есть FC.

    Под CL хвостов и тиков в статистике нет вовсе, и C# пропускает оба блока
    целиком; у нас это выражено нулевыми slider_count и large_tick_count.
    """
    total = 6 * counts.great + 2 * counts.ok + counts.meh
    maximum = 6 * (counts.great + counts.ok + counts.meh + counts.miss)

    if slider_tail_hits is None:
        slider_tail_hits = slider_count

    total += _SLIDER_TAIL_WEIGHT * slider_tail_hits
    maximum += _SLIDER_TAIL_WEIGHT * slider_count

    total += _LARGE_TICK_WEIGHT * (large_tick_count - large_tick_misses)
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
