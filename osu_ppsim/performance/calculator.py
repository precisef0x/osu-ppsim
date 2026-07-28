"""Расчёт pp за FC-прохождение.

Источник: osu.Game.Rulesets.Osu/Difficulty/OsuPerformanceCalculator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Реализовано FC-подмножество. При нулевом числе промахов и максимальном комбо
из оригинальной формулы выпадают:

* `effectiveMissCount` и все зависящие от него штрафы;
* оценка слайдербреков (`aim/speed EstimatedSliderBreaks`);
* нерф аима за недодержанные слайдеры — множитель обращается в единицу;
* `getComboScalingFactor` — тоже единица;
* вся legacy-ветка `OsuLegacyScoreMissCalculator`, работающая только для
  классических скоров с известной суммой очков.

Из 552 строк оригинала остаётся около двухсот.

МОД CLASSIC. В оригинале `usingClassicSliderAccuracy` ветвит расчёт в пяти
местах, но при FC живым остаётся ровно одно — состав объектов, несущих
точность (см. _compute_accuracy). Остальные четыре схлопываются, и это
проверяется, а не предполагается: гейт фазы 5 гоняет CL-наборы против оракула.

* `OsuLegacyScoreMissCalculator` — недостижим: нужен `LegacyTotalScore`,
  которого у симулированного скора нет.
* classic-оценка миссов по комбо — `fullComboThreshold` не превышает `MaxCombo`,
  а при FC комбо ему равно, поэтому оценка остаётся нулевой. Вместе с этой
  веткой не нужны и legacy-атрибуты `Aim/SpeedTopWeightedSliderFactor`:
  за её пределами они нигде не читаются.
* `calculateEstimatedSliderBreaks` — под гейтом `effectiveMissCount > 0`.
* classic-нерф аима за недодержанные слайдеры — оценка равна
  `Min(неточные, MaxCombo - комбо)`, то есть нулю при FC; множитель выходит
  тот же, что и в lazer-ветке.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from ..beatmap.difficulty_info import (
    GREAT_WINDOW_RANGE,
    MEH_WINDOW_RANGE,
    OK_WINDOW_RANGE,
    hit_window,
)
from ..difficulty.calculator import (
    PERFORMANCE_BASE_MULTIPLIER,
    PERFORMANCE_NORM_EXPONENT,
    OsuDifficultyAttributes,
    aim_difficulty_to_performance,
    sum_cognition_difficulty,
)
from ..difficulty.skills.base import HarmonicSkill
from ..difficulty.skills.flashlight import Flashlight
from ..difficulty.utils import SQRT2, c_div, erf, erf_inv, norm, pow_double, pow_int, reverse_lerp
from ..mods import Mods
from .accuracy import HitCounts

__all__ = ["OsuPerformanceAttributes", "calculate_performance"]

_SPEED_HIGH_DEVIATION_SCALE: Final[float] = 50.0


@dataclass
class OsuPerformanceAttributes:
    total: float = 0.0
    aim: float = 0.0
    speed: float = 0.0
    accuracy: float = 0.0
    reading: float = 0.0
    flashlight: float = 0.0
    speed_deviation: float | None = None
    #: Точность, с которой считался результат: под lazer с хвостами и тиками,
    #: под CL — по одним кругам.
    effective_accuracy: float = 1.0


def calculate_performance(
    attributes: OsuDifficultyAttributes,
    counts: HitCounts,
    accuracy: float,
    mods: Mods,
    overall_difficulty: float,
    drain_rate: float,
) -> OsuPerformanceAttributes:
    """pp за FC. Промахи и потерянное комбо не поддерживаются намеренно."""
    if counts.miss:
        raise ValueError("расчёт поддерживает только FC: промахов быть не должно")

    clock_rate = mods.clock_rate
    accuracy = min(max(accuracy, 0.0), 1.0)

    great_window = hit_window(overall_difficulty, GREAT_WINDOW_RANGE) / clock_rate
    ok_window = hit_window(overall_difficulty, OK_WINDOW_RANGE) / clock_rate
    meh_window = hit_window(overall_difficulty, MEH_WINDOW_RANGE) / clock_rate

    # Внимание: здесь окно НЕ удваивается, в отличие от HitWindowGreat
    # у difficulty-объекта. Отсюда и разные формулы OverallDifficulty.
    effective_od = (79.5 - great_window) / 6

    multiplier = PERFORMANCE_BASE_MULTIPLIER
    if "NF" in mods:
        # При FC штраф вырождается в единицу, но формула сохранена как в оригинале.
        multiplier *= max(0.90, 1.0 - 0.02 * 0.0)

    speed_deviation = _speed_deviation(attributes, counts, great_window, ok_window, meh_window)

    aim_value = _compute_aim(attributes, counts, accuracy)
    speed_value = _compute_speed(attributes, speed_deviation)
    accuracy_value = _compute_accuracy(attributes, counts, effective_od, mods)
    reading_value = _compute_reading(attributes, accuracy)
    flashlight_value = _compute_flashlight(attributes, accuracy, mods)
    cognition_value = sum_cognition_difficulty(reading_value, flashlight_value)

    total = (
        norm(PERFORMANCE_NORM_EXPONENT, aim_value, speed_value, accuracy_value, cognition_value) * multiplier
    )

    return OsuPerformanceAttributes(
        total=total,
        aim=aim_value,
        speed=speed_value,
        accuracy=accuracy_value,
        reading=reading_value,
        flashlight=flashlight_value,
        speed_deviation=speed_deviation,
        effective_accuracy=accuracy,
    )


def _compute_aim(attributes: OsuDifficultyAttributes, counts: HitCounts, accuracy: float) -> float:
    aim_difficulty = attributes.aim_difficulty

    if attributes.slider_count > 0 and attributes.aim_difficult_slider_count > 0:
        # При FC ни хвостов, ни тиков не потеряно, поэтому оценка недодержанных
        # слайдеров равна нулю. Множитель всё равно считается по формуле:
        # (1 - SF) * 1 + SF не обязано быть в точности единицей в плавающей точке.
        slider_nerf_factor = (1 - attributes.slider_factor) * pow_int(1 - 0.0, 3) + attributes.slider_factor
        aim_difficulty *= slider_nerf_factor

    aim_value = aim_difficulty_to_performance(aim_difficulty)

    total_hits = counts.total
    length_bonus = (
        0.95
        + 0.35 * min(1.0, total_hits / 2000.0)
        + (math.log10(total_hits / 2000.0) * 0.5 if total_hits > 2000 else 0.0)
    )
    aim_value *= length_bonus

    # Штраф за промахи и бонусы Blinds/Traceable опущены: промахов нет,
    # а эти моды вне охвата.

    aim_value *= accuracy
    return aim_value


def _compute_speed(attributes: OsuDifficultyAttributes, speed_deviation: float | None) -> float:
    if speed_deviation is None:
        return 0.0

    speed_value = HarmonicSkill.difficulty_to_performance(attributes.speed_difficulty)
    speed_value *= _speed_high_deviation_nerf(attributes, speed_deviation)

    # Эффективное окно выводится из самой скоростной сложности: чем она выше,
    # тем уже окно. При нулевой сложности (карта из одних спиннеров или из одного
    # объекта) C# получает бесконечное окно и erf(inf) = 1, то есть множитель
    # без эффекта; Python на этом делении упал бы, отсюда c_div.
    effective_hit_window = 20 * pow_double(c_div(4, attributes.speed_difficulty), 0.35)
    effective_accuracy = erf(effective_hit_window / speed_deviation)

    speed_value *= pow_int(effective_accuracy, 2)
    return speed_value


def _compute_accuracy(
    attributes: OsuDifficultyAttributes, counts: HitCounts, overall_difficulty: float, mods: Mods
) -> float:
    # Под lazer-механикой в точность входят и слайдеры, а не только круги;
    # под CL головы слайдеров не судятся на время, поэтому остаются одни круги.
    objects_with_accuracy = attributes.hit_circle_count
    if not mods.classic_slider_accuracy:
        objects_with_accuracy += attributes.slider_count

    if objects_with_accuracy > 0:
        better_accuracy = (
            (counts.great - max(counts.total - objects_with_accuracy, 0)) * 6 + counts.ok * 2 + counts.meh
        ) / (objects_with_accuracy * 6)
    else:
        better_accuracy = 0.0

    better_accuracy = max(0.0, better_accuracy)

    accuracy_value = pow_double(1.52163, overall_difficulty) * pow_int(better_accuracy, 24) * 2.83

    # Держать точность дольше труднее.
    accuracy_value *= (
        pow_double(objects_with_accuracy / 1000.0, 0.3)
        if objects_with_accuracy < 1000
        else pow_double(objects_with_accuracy / 1000.0, 0.1)
    )

    return accuracy_value


def _compute_reading(attributes: OsuDifficultyAttributes, accuracy: float) -> float:
    reading_value = HarmonicSkill.difficulty_to_performance(attributes.reading_difficulty)
    # Штраф за промахи опущен: при FC он не выполняется. Именно поэтому
    # атрибут reading_difficult_note_count в этом охвате не используется.
    reading_value *= pow_int(accuracy, 3)
    return reading_value


def _compute_flashlight(attributes: OsuDifficultyAttributes, accuracy: float, mods: Mods) -> float:
    if not mods.flashlight:
        return 0.0

    flashlight_value = Flashlight.difficulty_to_performance(attributes.flashlight_difficulty)
    # Комбо-скейлинг при FC равен единице, штраф за промахи не применяется.
    flashlight_value *= 0.5 + accuracy / 2.0
    return flashlight_value


def _speed_deviation(
    attributes: OsuDifficultyAttributes,
    counts: HitCounts,
    great_window: float,
    ok_window: float,
    meh_window: float,
) -> float | None:
    """Оценка разброса нажатий на скоростных нотах, в худшем случае.

    Все неточные попадания условно относятся к скоростным нотам — это верхняя
    оценка, поэтому результат воспроизводим для одинаковых скоров.
    """
    if counts.great + counts.ok + counts.meh == 0:
        return None

    speed_note_count = attributes.speed_note_count
    speed_note_count += (counts.total - attributes.speed_note_count) * 0.1

    relevant_miss = min(counts.miss, speed_note_count)
    relevant_meh = min(counts.meh, speed_note_count - relevant_miss)
    relevant_ok = min(counts.ok, speed_note_count - relevant_miss - relevant_meh)
    relevant_great = max(0.0, speed_note_count - relevant_miss - relevant_meh - relevant_ok)

    return _deviation(relevant_great, relevant_ok, relevant_meh, great_window, ok_window, meh_window)


def _deviation(
    great: float, ok: float, meh: float, great_window: float, ok_window: float, meh_window: float
) -> float | None:
    """Оценка разброса по числу троек, соток и пятидесяток.

    Тройки и сотки считаются нормально распределёнными, пятидесятки —
    равномерно. Промахи игнорируются: обычно они от промашки по позиции,
    а не по времени.
    """
    if great + ok + meh <= 0:
        return None

    n = max(1.0, great + ok)
    p = great / n

    # Односторонний 99-процентный квантиль нормального распределения.
    z = 2.32634787404

    p_lower_bound = min(
        p,
        (n * p + z * z / 2) / (n + z * z) - z / (n + z * z) * math.sqrt(n * p * (1 - p) + z * z / 4),
    )

    if p_lower_bound > 0.01:
        deviation = great_window / (SQRT2 * erf_inv(p_lower_bound))

        # Вычитаем вклад хвостов за пределами окна соток — это равносильно
        # разбросу нормального распределения, усечённого на +-ok_window.
        ok_tail_amount = (
            math.sqrt(2 / math.pi)
            * ok_window
            * math.exp(-0.5 * pow_int(ok_window / deviation, 2))
            / (deviation * erf(ok_window / (SQRT2 * deviation)))
        )
        deviation *= math.sqrt(1 - ok_tail_amount)
    else:
        # Предельное значение для скора из одних соток.
        deviation = ok_window / math.sqrt(3)

    meh_variance = (meh_window * meh_window + ok_window * meh_window + ok_window * ok_window) / 3

    return math.sqrt(((great + ok) * pow_int(deviation, 2) + meh * meh_variance) / (great + ok + meh))


def _speed_high_deviation_nerf(attributes: OsuDifficultyAttributes, speed_deviation: float) -> float:
    """Занижает скорость, если разброс говорит о неаккуратном нажатии."""
    speed_value = HarmonicSkill.difficulty_to_performance(attributes.speed_difficulty)

    # Порог, выше которого сложность считается «натыканной». Всё сверх него
    # растёт логарифмически, то есть по сути срезается.
    cutoff = 100 + 220 * pow_double(22 / speed_deviation, 6.5)

    if speed_value <= cutoff:
        return 1.0

    adjusted = _SPEED_HIGH_DEVIATION_SCALE * (
        math.log((speed_value - cutoff) / _SPEED_HIGH_DEVIATION_SCALE + 1)
        + cutoff / _SPEED_HIGH_DEVIATION_SCALE
    )

    # Разброс до 22 (то есть UR 220 и меньше) считается аккуратным нажатием
    # и не наказывается.
    lerp = 1 - reverse_lerp(speed_deviation, 22.0, 27.0)
    adjusted = adjusted + (speed_value - adjusted) * lerp

    return adjusted / speed_value
