"""Оценка числа промахов по сумме очков классического скора.

Источник: osu.Game.Rulesets.Osu/Difficulty/OsuLegacyScoreMissCalculator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

В stable слайдербрейк не оставляет следа в статистике: скор с нулём промахов
и комбо ниже максимума может быть и десятком отпущенных хвостов, и тремя
разрывами посреди карты. Различить их можно только по сумме очков — в ScoreV1
каждое попадание умножается на текущий множитель комбо, поэтому итог зависит
от того, КАК раздроблено комбо, а не только сколько его потеряно.
"""

from __future__ import annotations

from typing import Final

from ..difficulty.calculator import OsuDifficultyAttributes
from ..difficulty.utils import c_int_div, pow_double, pow_int
from ..mods import Mods
from .accuracy import HitCounts

__all__ = ["legacy_score_multiplier", "score_based_estimated_miss_count"]

#: OsuLegacyScoreSimulator.GetLegacyScoreMultiplier — множители ScoreV1.
#: Мод ScoreV2 у нас вне охвата, поэтому взяты значения ветки без него.
_MOD_MULTIPLIERS: Final[dict[str, float]] = {
    "NF": 0.5,
    "EZ": 0.5,
    "HT": 0.3,
    "DC": 0.3,
    "HD": 1.06,
    "HR": 1.06,
    "DT": 1.12,
    "NC": 1.12,
    "FL": 1.12,
}


def legacy_score_multiplier(mods: Mods) -> float:
    """Множитель ScoreV1 за моды.

    Порядок умножения не важен: множители независимы, а `CL` в список не входит —
    в C# у OsuModClassic своей ветки нет.
    """
    multiplier = 1.0
    for acronym, value in _MOD_MULTIPLIERS.items():
        if acronym in mods:
            multiplier *= value
    return multiplier


def score_based_estimated_miss_count(
    attributes: OsuDifficultyAttributes,
    counts: HitCounts,
    score_max_combo: int,
    legacy_total_score: int,
    mods: Mods,
) -> float:
    """OsuLegacyScoreMissCalculator.Calculate.

    ВНИМАНИЕ: комбо сюда приходит НЕЗАЖАТЫМ. Остальной калькулятор работает
    с `Math.Clamp(score.MaxCombo, 0, MaxCombo)`, а этот класс читает
    `score.MaxCombo` напрямую — и на комбо выше максимума карты выдаёт
    отрицательную оценку. Зажать здесь значит разойтись с оракулом.
    """
    if attributes.max_combo == 0:
        return 0.0

    score_v1_multiplier = attributes.legacy_score_base_multiplier * legacy_score_multiplier(mods)
    relevant_combo_per_object = _relevant_combo_per_object(attributes, score_v1_multiplier)

    maximum_miss_count = _maximum_combo_based_miss_count(attributes, counts, score_max_combo)

    accuracy = _score_accuracy(counts)
    obtained = _score_at_combo(attributes, counts, accuracy, score_max_combo, relevant_combo_per_object, score_v1_multiplier)
    remaining_score = legacy_total_score - obtained

    if remaining_score <= 0:
        return maximum_miss_count

    remaining_combo = attributes.max_combo - score_max_combo
    expected_remaining = _score_at_combo(
        attributes, counts, accuracy, remaining_combo, relevant_combo_per_object, score_v1_multiplier
    )

    estimated = expected_remaining / remaining_score

    # Меньше одного разрыва оценка по очкам различить не умеет — в этой зоне
    # решает оценка по комбо.
    estimated = max(estimated, 1)

    return min(estimated, maximum_miss_count)


def _score_accuracy(counts: HitCounts) -> float:
    """ScoreInfo.Accuracy, как её видит калькулятор: по кругам.

    Скор с суммой очков всегда классический, а там хвостов и тиков
    в статистике нет.
    """
    maximum = 6 * counts.total
    if maximum == 0:
        return 0.0
    return (6 * counts.great + 2 * counts.ok + counts.meh) / maximum


def _score_at_combo(
    attributes: OsuDifficultyAttributes,
    counts: HitCounts,
    accuracy: float,
    combo: float,
    relevant_combo_per_object: float,
    score_v1_multiplier: float,
) -> float:
    """Сколько очков набралось бы к заданному комбо.

    Комбо-часть ScoreV1 — арифметическая прогрессия, поэтому сумма считается
    по формуле прогрессии, а не перебором.
    """
    estimated_objects = combo / relevant_combo_per_object - 1

    if relevant_combo_per_object > 0:
        combo_score = (
            (2 * (relevant_combo_per_object - 1) + (estimated_objects - 1) * relevant_combo_per_object)
            * estimated_objects
            / 2
        )
    else:
        combo_score = 0.0

    combo_score *= accuracy * 300 / 25 * score_v1_multiplier

    objects_hit = (counts.total - counts.miss) * combo / attributes.max_combo
    non_combo_score = (300 + attributes.nested_score_per_object) * accuracy * objects_hit

    return combo_score + non_combo_score


def _relevant_combo_per_object(attributes: OsuDifficultyAttributes, score_v1_multiplier: float) -> float:
    """Среднее комбо на объект, обращённое из максимальной комбо-суммы.

    Прямой подсчёт не годится: у «жужжащих» слайдеров комбо на объект не
    ложится в арифметическую прогрессию, а обращение из суммы это учитывает.
    """
    combo_score = attributes.maximum_legacy_combo_score
    combo_score /= 300.0 / 25.0 * attributes.legacy_score_base_multiplier

    result = (attributes.max_combo - 2) * attributes.max_combo
    result /= max(attributes.max_combo + 2 * (combo_score - 1), 1)

    return result


def _maximum_combo_based_miss_count(
    attributes: OsuDifficultyAttributes, counts: HitCounts, score_max_combo: int
) -> float:
    """Жёсткий потолок оценки — на случай, когда по очкам сказать нечего.

    Отличается от обычной оценки по комбо возведением отношения в степень 2.5.
    """
    if attributes.slider_count <= 0:
        return counts.miss

    total_imperfect_hits = counts.ok + counts.meh + counts.miss
    miss_count = 0.0

    likely_missed_sliderend_portion = 0.04 + 0.06 * pow_int(min(attributes.aim_top_weighted_slider_factor, 1), 2)
    full_combo_threshold = attributes.max_combo - min(
        4 + likely_missed_sliderend_portion * attributes.slider_count, attributes.slider_count
    )

    if score_max_combo < full_combo_threshold:
        miss_count = pow_double(full_combo_threshold / max(1.0, score_max_combo), 2.5)

    miss_count = min(miss_count, total_imperfect_hits)

    # В C# оба операнда int: деление нацело с усечением К НУЛЮ.
    max_possible_slider_breaks = min(attributes.slider_count, c_int_div(attributes.max_combo - score_max_combo, 2))

    if miss_count - counts.miss > max_possible_slider_breaks:
        miss_count = counts.miss + max_possible_slider_breaks

    return miss_count
