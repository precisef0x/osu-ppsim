"""Оценка ритмической сложности.

Источник: osu.Game.Rulesets.Osu/Difficulty/Evaluators/Speed/RhythmEvaluator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

«Остров» — группа подряд идущих объектов с одинаковой дельтой. Оценка ищет смены
ритма и наказывает повторы, поэтому острова сравниваются между собой и копят
счётчик появлений.
"""

from __future__ import annotations

import math
from typing import Final

from ...beatmap.objects import Slider, Spinner
from ..hitobject import MIN_DELTA_TIME, OsuDifficultyHitObject
from ..utils import clamp, logistic, pow_double, reverse_lerp, smoothstep_bell_curve_unit

__all__ = ["evaluate_difficulty_of"]

_HISTORY_TIME_MAX: Final[int] = 5 * 1000
_HISTORY_OBJECTS_MAX: Final[int] = 32
_RHYTHM_OVERALL_MULTIPLIER: Final[float] = 0.95
_RHYTHM_RATIO_DIFFICULTY_MULTIPLIER: Final[float] = 26.0

#: int.MaxValue — маркер «остров ещё не инициализирован».
_INT_MAX: Final[int] = 2147483647

#: Своя нижняя граница дельты: гарантирует, что в этой точке она уже не ноль.
_DELTA_MIN_VALUE: Final[float] = 1e-7


class _Island:
    """Изменяемый по ссылке, как и в C#.

    Это существенно: список islands хранит ссылки, и Occurrences++ меняет
    объект прямо в списке. Неизменяемая структура здесь дала бы другой результат.
    """

    __slots__ = ("delta", "delta_count", "occurrences")

    def __init__(self, delta: int) -> None:
        self.delta = max(delta, MIN_DELTA_TIME)
        self.delta_count = 1
        self.occurrences = 1

    def add_delta(self, delta: int) -> None:
        if self.delta == _INT_MAX:
            self.delta = max(delta, MIN_DELTA_TIME)
        self.delta_count += 1

    def is_similar_polarity(self, other: _Island, epsilon: float) -> bool:
        # Острова из одной дельты не сравниваются.
        if self.delta_count <= 1 or other.delta_count <= 1:
            return False
        return abs(self.delta - other.delta) < epsilon and self.delta_count % 2 == other.delta_count % 2

    def almost_equals(self, other: _Island, epsilon: float) -> bool:
        return abs(self.delta - other.delta) < epsilon and self.delta_count == other.delta_count


def _get_effective_difficulty(delta_difference_ratio: float) -> float:
    """Берётся только дробная часть отношения — наказываются именно кратности."""
    fraction = delta_difference_ratio - math.trunc(delta_difference_ratio)
    return 1.0 + _RHYTHM_RATIO_DIFFICULTY_MULTIPLIER * min(0.5, smoothstep_bell_curve_unit(fraction))


def evaluate_difficulty_of(current: OsuDifficultyHitObject) -> float:
    if isinstance(current.base_object, Spinner):
        return 0.0

    rhythm_complexity_sum = 0.0
    delta_difference_epsilon = current.hit_window_great * 0.3

    island = _Island(_INT_MAX)
    previous_island = _Island(_INT_MAX)
    islands: list[_Island] = []

    # Сложность начала текущего острова — для бонуса за плотные ритмы.
    start_difficulty = 0.0
    first_delta_switch = False

    historical_note_count = min(current.index, _HISTORY_OBJECTS_MAX)

    rhythm_start = 0
    while (
        rhythm_start < historical_note_count - 2
        and current.start_time - current.previous(rhythm_start).start_time < _HISTORY_TIME_MAX
    ):
        rhythm_start += 1

    prev_obj = current.previous(rhythm_start)
    prev_prev_obj = current.previous(rhythm_start + 1)

    # Идём от самого дальнего объекта к текущему.
    for i in range(rhythm_start, 0, -1):
        curr_obj = current.previous(i - 1)
        if curr_obj is None or isinstance(curr_obj.base_object, Spinner):
            continue

        time_decay = (_HISTORY_TIME_MAX - (current.start_time - curr_obj.start_time)) / _HISTORY_TIME_MAX
        note_decay = (historical_note_count - i) / historical_note_count
        # Ограничивает либо время, либо число объектов — берётся меньшее.
        curr_historical_decay = min(note_decay, time_decay)

        curr_delta = max(curr_obj.delta_time, _DELTA_MIN_VALUE)
        prev_delta = max(prev_obj.delta_time, _DELTA_MIN_VALUE) if prev_obj is not None else _DELTA_MIN_VALUE
        delta_difference = abs(prev_delta - curr_delta)

        # Остров должен быть инициализирован здесь, иначе это случится только
        # на следующей смене ритма.
        if island.delta == _INT_MAX:
            island = _Island(int(curr_delta))

        delta_difference_ratio = max(prev_delta, curr_delta) / min(prev_delta, curr_delta)
        # Слишком большая разница дельт ослабляет бонус.
        difference_multiplier = clamp(2.0 - delta_difference_ratio / 8.0, 0.0, 1.0)
        window_penalty = clamp((delta_difference - delta_difference_epsilon) / delta_difference_epsilon, 0.0, 1.0)

        effective_difficulty = _get_effective_difficulty(delta_difference_ratio) * window_penalty * difference_multiplier

        # После слайдера нажатие проще: движение «отпустить-нажать» короче,
        # поэтому слайдер-круг-круг считается обычной тройкой, а не одиночкой с двойкой.
        if prev_obj is not None and isinstance(prev_obj.base_object, Slider):
            slider_lazy_end_delta = curr_obj.minimum_jump_time
            slider_lazy_ratio = max(slider_lazy_end_delta, curr_delta) / min(slider_lazy_end_delta, curr_delta)

            slider_real_end_delta = curr_obj.last_object_end_delta_time
            slider_real_ratio = max(slider_real_end_delta, curr_delta) / min(slider_real_end_delta, curr_delta)

            slider_effective = min(
                _get_effective_difficulty(slider_lazy_ratio), _get_effective_difficulty(slider_real_ratio)
            )
            effective_difficulty = min(slider_effective, effective_difficulty)

        if delta_difference < delta_difference_epsilon:
            island.add_delta(int(curr_delta))

        if first_delta_switch:
            if delta_difference > delta_difference_epsilon:
                # Смена темпа в слайдер — мягкое окно точности.
                if isinstance(curr_obj.base_object, Slider):
                    effective_difficulty *= 0.5

                # Повтор чётности острова (2 -> 4, 3 -> 5).
                if island.is_similar_polarity(previous_island, delta_difference_epsilon):
                    effective_difficulty *= 0.5

                # Предыдущее ускорение было ровно нотой назад: 1/1 -> 1/2 -> 1/4.
                prev_prev_delta = max(prev_prev_obj.delta_time, _DELTA_MIN_VALUE) if prev_prev_obj else _DELTA_MIN_VALUE
                if (
                    prev_prev_delta > prev_delta + delta_difference_epsilon
                    and prev_delta > curr_delta + delta_difference_epsilon
                ):
                    effective_difficulty *= 0.125

                # Повтор размера острова (тройка -> тройка).
                if previous_island.delta_count == island.delta_count:
                    effective_difficulty *= 0.5

                if prev_delta > curr_delta + delta_difference_epsilon:
                    effective_difficulty *= 0.65

                found = False
                for existing_island in islands:
                    if existing_island.almost_equals(island, delta_difference_epsilon):
                        # Счётчик растёт, только если острова идут подряд.
                        if previous_island.almost_equals(island, delta_difference_epsilon):
                            existing_island.occurrences += 1

                        power = logistic(island.delta, midpoint_offset=58.33, multiplier=0.24, max_value=2.75)
                        effective_difficulty *= min(
                            3.0 / existing_island.occurrences,
                            pow_double(1.0 / existing_island.occurrences, power),
                        )

                        found = True
                        break

                if not found and island.delta_count > 0:
                    islands.append(island)

                # Дабл-тапабельные пары ослабляются.
                if prev_obj is not None:
                    effective_difficulty *= 1 - prev_obj.calculate_doubletap_feasibility(curr_obj) * 0.75

                if island.delta_count > 1:
                    rhythm_complexity_sum += math.sqrt(effective_difficulty * start_difficulty) * curr_historical_decay
                else:
                    # У островов из одной ноты сложность постоянная.
                    rhythm_complexity_sum += 0.7 * curr_historical_decay

                start_difficulty = effective_difficulty

                # Замедляемся — перестаём считать; при ускорении счёт продолжается.
                if prev_delta + delta_difference_epsilon < curr_delta:
                    first_delta_switch = False

                previous_island = island
                island = _Island(int(curr_delta))

        elif prev_delta > curr_delta + delta_difference_epsilon:
            # Ускорение: начинаем считать остров до следующей смены темпа.
            first_delta_switch = True

            if isinstance(curr_obj.base_object, Slider):
                effective_difficulty *= 0.6
            if prev_obj is not None and isinstance(prev_obj.base_object, Slider):
                effective_difficulty *= 0.6

            start_difficulty = effective_difficulty
            island = _Island(int(curr_delta))

        prev_prev_obj = prev_obj
        prev_obj = curr_obj

    # Длинный текущий остров ослабляет вклад суммы.
    rhythm_complexity_sum *= reverse_lerp(island.delta_count, 22, 3)

    return math.sqrt(4 + rhythm_complexity_sum * _RHYTHM_OVERALL_MULTIPLIER) / 2.0
