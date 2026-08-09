"""Расчёт pp.

Источник: osu.Game.Rulesets.Osu/Difficulty/OsuPerformanceCalculator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Скор задаётся раскладкой попаданий, комбо, собранными хвостами и пропущенными
тиками; значения по умолчанию описывают FC. Обе механики счёта — лазерная
и классическая (мод CL) — ветвятся так же, как в оригинале.

У классического скора с известной суммой очков промахи оцениваются по ней,
а не по комбо (см. performance/legacy_miss.py): в stable слайдербрейк
неотличим от потерянного хвоста по статистике, но отличим по сумме очков.

Вне охвата остаются ветки неподдерживаемых модов — Relax, Autopilot, SpunOut,
Blinds, Traceable и ScoreV2. Они отсекаются на входе (см. osu_ppsim.mods).
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
from ..difficulty.utils import (
    SQRT2,
    c_div,
    c_int_div,
    erf,
    erf_inv,
    logistic,
    norm,
    pow_double,
    pow_int,
    reverse_lerp,
    smoothstep,
)
from ..mods import Mods
from .accuracy import HitCounts, Score
from .legacy_miss import score_based_estimated_miss_count

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
    #: Промахи, которыми расчёт оперирует на самом деле: к настоящим добавлена
    #: оценка слайдербреков по потерянному комбо.
    effective_miss_count: float = 0.0
    combo_based_estimated_miss_count: float = 0.0
    #: None, если оценка по очкам не применялась (лазерный скор либо
    #: классический без известной суммы очков).
    score_based_estimated_miss_count: float | None = None
    aim_estimated_slider_breaks: float = 0.0
    speed_estimated_slider_breaks: float = 0.0


@dataclass
class _ScoreState:
    """Состояние скора — аналог полей экземпляра OsuPerformanceCalculator.

    Собрано в один объект, чтобы формулы принимали его целиком: в C# это поля
    класса, и передавать их по одному значило бы плодить восьмиместные сигнатуры.
    """

    counts: HitCounts
    accuracy: float
    score_max_combo: int
    #: SliderCount - SliderTailHit: сколько хвостов не засчитано.
    slider_ends_dropped: int
    #: LargeTickMiss: пропущенные тики и реверсы.
    slider_tick_miss: int
    classic: bool

    effective_miss_count: float = 0.0
    aim_slider_breaks: float = 0.0
    speed_slider_breaks: float = 0.0

    @property
    def total_hits(self) -> int:
        return self.counts.total

    @property
    def total_successful_hits(self) -> int:
        return self.counts.great + self.counts.ok + self.counts.meh

    @property
    def total_imperfect_hits(self) -> int:
        return self.counts.ok + self.counts.meh + self.counts.miss


def _combo_based_estimated_miss_count(attributes: OsuDifficultyAttributes, s: _ScoreState) -> float:
    """OsuPerformanceCalculator.calculateComboBasedEstimatedMissCount.

    Потерянное комбо переводится в оценку числа слайдербреков. Обе ветки
    воспроизведены буквально, включая то, что в classic оценка получается
    ДЕЛЕНИЕМ порога на комбо, а не вычитанием: величина используется как число
    промахов, хотя по смыслу это отношение.
    """
    if attributes.slider_count <= 0:
        return s.counts.miss

    miss_count: float = s.counts.miss

    if s.classic:
        # Трудные слайдеры чаще теряют хвост, лёгкие — чаще ломают комбо.
        likely_missed_sliderend_portion = 0.04 + 0.06 * pow_int(min(attributes.aim_top_weighted_slider_factor, 1), 2)
        full_combo_threshold = attributes.max_combo - min(
            4 + likely_missed_sliderend_portion * attributes.slider_count, attributes.slider_count
        )

        if s.score_max_combo < full_combo_threshold:
            miss_count = full_combo_threshold / max(1.0, s.score_max_combo)

        miss_count = min(miss_count, s.total_imperfect_hits)

        # Каждый слайдер в классике даёт минимум 2 комбо, поэтому потеря одного
        # комбо слайдербреком быть не может — это потерянный хвост.
        # ВНИМАНИЕ: в C# оба операнда int: деление нацело с усечением к нулю.
        max_possible_slider_breaks = min(attributes.slider_count, c_int_div(attributes.max_combo - s.score_max_combo, 2))

        if miss_count - s.counts.miss > max_possible_slider_breaks:
            miss_count = s.counts.miss + max_possible_slider_breaks
    else:
        full_combo_threshold = attributes.max_combo - s.slider_ends_dropped

        if s.score_max_combo < full_combo_threshold:
            miss_count = full_combo_threshold / max(1.0, s.score_max_combo)

        # Пропущенные тики тоже ломают комбо.
        miss_count = min(miss_count, s.slider_tick_miss + s.counts.miss)

    return miss_count


def _estimated_slider_breaks(
    top_weighted_slider_factor: float, attributes: OsuDifficultyAttributes, s: _ScoreState
) -> float:
    """OsuPerformanceCalculator.calculateEstimatedSliderBreaks.

    Только для классической механики: там слайдербрейк неотличим от потерянного
    хвоста, и его приходится оценивать по числу неточных попаданий.

    Порядок операций важен: nonMissMistakeAdjustment считается по оценке ДО
    сглаживания, а умножение на Smoothstep идёт после. Переставить — значит
    получить другое число.

    Деление на attributes.max_combo безопасно: сюда заходят только при
    effective_miss_count > 0, а он зажат сверху числом попаданий, которое
    у карты с нулевым максимальным комбо тоже ноль.
    """
    non_miss_mistakes = s.counts.ok + s.counts.meh

    if not s.classic or non_miss_mistakes == 0:
        return 0.0

    missed_combo_percent = 1.0 - s.score_max_combo / attributes.max_combo
    estimated = min(non_miss_mistakes, s.effective_miss_count * top_weighted_slider_factor)

    # Слагаемые с обеих сторон дроби стабилизируют её на краях диапазона.
    adjustment = (non_miss_mistakes - estimated + 4.5) / (non_miss_mistakes + 4)

    # При оценке около единицы слайдербрейк вероятнее всего ровно один.
    estimated *= smoothstep(s.effective_miss_count, 1, 2)

    return estimated * adjustment * logistic(missed_combo_percent, 0.33, 15)


def _miss_penalty(miss_count: float, difficult_strain_count: float) -> float:
    """OsuPerformanceCalculator.calculateMissPenalty.

    Промахи считаются пришедшимися на самые трудные места, поэтому штраф тем
    жёстче, чем меньше на карте трудных секций.

    При difficult_strain_count <= 1 логарифм даёт ноль: C# делит на него,
    получает бесконечность и в итоге ноль, Python упал бы — отсюда c_div.
    """
    return 0.93 / (c_div(miss_count, 4 * math.log(max(1, difficult_strain_count))) + 1)


def _combo_scaling_factor(attributes: OsuDifficultyAttributes, s: _ScoreState) -> float:
    """OsuPerformanceCalculator.getComboScalingFactor."""
    if attributes.max_combo <= 0:
        return 1.0
    return min(pow_double(s.score_max_combo, 0.8) / pow_double(attributes.max_combo, 0.8), 1.0)


def calculate_performance(
    attributes: OsuDifficultyAttributes,
    score: Score,
    accuracy: float,
    mods: Mods,
    overall_difficulty: float,
    drain_rate: float,
) -> OsuPerformanceAttributes:
    """OsuPerformanceCalculator.CreatePerformanceAttributes."""
    counts = score.counts
    classic = mods.classic_slider_accuracy

    # Как в C#: countSliderEndsDropped = SliderCount - statistics[SliderTailHit],
    # а под classic ключа в статистике нет и GetValueOrDefault даёт ноль, то есть
    # «потеряны все хвосты». Обе величины под classic никем не читаются —
    # ветки, где они встречаются, туда не заходят, — но считаем как оригинал.
    slider_tail_hits = 0 if classic else (
        attributes.slider_count if score.slider_tail_hits is None else score.slider_tail_hits
    )
    state = _ScoreState(
        counts=counts,
        accuracy=min(max(accuracy, 0.0), 1.0),
        score_max_combo=min(
            max(attributes.max_combo if score.max_combo is None else score.max_combo, 0), attributes.max_combo
        ),
        slider_ends_dropped=attributes.slider_count - slider_tail_hits,
        slider_tick_miss=0 if classic else score.large_tick_misses,
        classic=classic,
    )

    clock_rate = mods.clock_rate
    accuracy = state.accuracy

    great_window = hit_window(overall_difficulty, GREAT_WINDOW_RANGE) / clock_rate
    ok_window = hit_window(overall_difficulty, OK_WINDOW_RANGE) / clock_rate
    meh_window = hit_window(overall_difficulty, MEH_WINDOW_RANGE) / clock_rate

    # Внимание: здесь окно НЕ удваивается, в отличие от HitWindowGreat
    # у difficulty-объекта. Отсюда и разные формулы OverallDifficulty.
    effective_od = (79.5 - great_window) / 6

    combo_based = _combo_based_estimated_miss_count(attributes, state)

    # У классического скора с известной суммой очков промахи оцениваются
    # по ней, а не по комбо: в stable слайдербрейк неотличим от потерянного
    # хвоста по статистике, но отличим по сумме очков.
    score_based: float | None = None
    if classic and score.legacy_total_score is not None:
        # Комбо передаётся НЕЗАЖАТЫМ: легаси-калькулятор читает score.MaxCombo
        # напрямую, в обход зажима, которым пользуется остальной расчёт.
        raw_max_combo = attributes.max_combo if score.max_combo is None else score.max_combo
        score_based = score_based_estimated_miss_count(
            attributes, counts, raw_max_combo, score.legacy_total_score, mods
        )
        state.effective_miss_count = score_based
    else:
        state.effective_miss_count = combo_based
    state.effective_miss_count = max(counts.miss, state.effective_miss_count)
    state.effective_miss_count = min(state.total_hits, state.effective_miss_count)
    state.effective_miss_count = max(0, state.effective_miss_count)

    if state.effective_miss_count > 0:
        state.aim_slider_breaks = _estimated_slider_breaks(
            attributes.aim_top_weighted_slider_factor, attributes, state
        )
        state.speed_slider_breaks = _estimated_slider_breaks(
            attributes.speed_top_weighted_slider_factor, attributes, state
        )

    multiplier = PERFORMANCE_BASE_MULTIPLIER
    if "NF" in mods:
        multiplier *= max(0.90, 1.0 - 0.02 * state.effective_miss_count)

    speed_deviation = _speed_deviation(attributes, counts, great_window, ok_window, meh_window)

    aim_value = _compute_aim(attributes, state)
    speed_value = _compute_speed(attributes, state, speed_deviation)
    accuracy_value = _compute_accuracy(attributes, state, effective_od)
    reading_value = _compute_reading(attributes, state)
    flashlight_value = _compute_flashlight(attributes, state, mods)
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
        effective_miss_count=state.effective_miss_count,
        combo_based_estimated_miss_count=combo_based,
        score_based_estimated_miss_count=score_based,
        aim_estimated_slider_breaks=state.aim_slider_breaks,
        speed_estimated_slider_breaks=state.speed_slider_breaks,
    )


def _compute_aim(attributes: OsuDifficultyAttributes, s: _ScoreState) -> float:
    aim_difficulty = attributes.aim_difficulty

    if attributes.slider_count > 0 and attributes.aim_difficult_slider_count > 0:
        if s.classic:
            # В классике недодержанные слайдеры неотличимы от потерянного комбо,
            # поэтому всё потерянное комбо считается ими.
            improperly_followed = min(max(min(s.total_imperfect_hits, attributes.max_combo - s.score_max_combo), 0),
                                      attributes.aim_difficult_slider_count)
        else:
            # Тики тоже означают, что слайдер не был пройден как надо. Промахи
            # сюда НЕ входят: за потерянную голову штраф и так суровый.
            improperly_followed = min(max(s.slider_ends_dropped + s.slider_tick_miss, 0),
                                      attributes.aim_difficult_slider_count)

        slider_nerf_factor = (1 - attributes.slider_factor) * pow_int(
            1 - improperly_followed / attributes.aim_difficult_slider_count, 3
        ) + attributes.slider_factor
        aim_difficulty *= slider_nerf_factor

    aim_value = aim_difficulty_to_performance(aim_difficulty)

    total_hits = s.total_hits
    length_bonus = (
        0.95
        + 0.35 * min(1.0, total_hits / 2000.0)
        + (math.log10(total_hits / 2000.0) * 0.5 if total_hits > 2000 else 0.0)
    )
    aim_value *= length_bonus

    if s.effective_miss_count > 0:
        relevant_miss_count = min(
            s.effective_miss_count + s.aim_slider_breaks, s.total_imperfect_hits + s.slider_tick_miss
        )
        aim_value *= _miss_penalty(relevant_miss_count, attributes.aim_difficult_strain_count)

    # Бонусы Blinds и Traceable опущены: моды вне охвата.

    aim_value *= s.accuracy
    return aim_value


def _compute_speed(
    attributes: OsuDifficultyAttributes, s: _ScoreState, speed_deviation: float | None
) -> float:
    if speed_deviation is None:
        return 0.0

    speed_value = HarmonicSkill.difficulty_to_performance(attributes.speed_difficulty)

    if s.effective_miss_count > 0:
        relevant_miss_count = min(
            s.effective_miss_count + s.speed_slider_breaks, s.total_imperfect_hits + s.slider_tick_miss
        )
        speed_value *= _miss_penalty(relevant_miss_count, attributes.speed_difficult_strain_count)

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
    attributes: OsuDifficultyAttributes, s: _ScoreState, overall_difficulty: float
) -> float:
    # Под lazer-механикой в точность входят и слайдеры, а не только круги;
    # под CL головы слайдеров не судятся на время, поэтому остаются одни круги.
    objects_with_accuracy = attributes.hit_circle_count
    if not s.classic:
        objects_with_accuracy += attributes.slider_count

    counts = s.counts

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


def _compute_reading(attributes: OsuDifficultyAttributes, s: _ScoreState) -> float:
    reading_value = HarmonicSkill.difficulty_to_performance(attributes.reading_difficulty)

    if s.effective_miss_count > 0:
        reading_value *= _miss_penalty(
            s.effective_miss_count + s.aim_slider_breaks, attributes.reading_difficult_note_count
        )

    # Точность влияет на чтение жёстко.
    reading_value *= pow_int(s.accuracy, 3)
    return reading_value


def _compute_flashlight(attributes: OsuDifficultyAttributes, s: _ScoreState, mods: Mods) -> float:
    if not mods.flashlight:
        return 0.0

    flashlight_value = Flashlight.difficulty_to_performance(attributes.flashlight_difficulty)

    # Штраф считается от доли промахов, а не от их числа: три процента снимаются
    # за любое их количество.
    if s.effective_miss_count > 0:
        flashlight_value *= 0.97 * pow_double(
            1 - pow_double(s.effective_miss_count / s.total_hits, 0.775), pow_double(s.effective_miss_count, 0.875)
        )

    flashlight_value *= _combo_scaling_factor(attributes, s)
    flashlight_value *= 0.5 + s.accuracy / 2.0
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
