"""Базовые классы скиллов.

Источник: osu.Game/Rulesets/Difficulty/Skills/{Skill,StrainSkill,
          VariableLengthStrainSkill,HarmonicSkill}.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Три разные схемы накопления сложности:

* `StrainSkill` — классические страйны с секциями фиксированной длины (Flashlight);
* `VariableLengthStrainSkill` — секции переменной длины с очередью отложенных
  страйнов, чтобы резкий пик не обрезался следующим слабым объектом (Aim);
* `HarmonicSkill` — без страйнов вовсе, гармоническая сумма по отсортированным
  сложностям объектов (Speed, Reading).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...mods import Mods
from ..hitobject import OsuDifficultyHitObject
from ..utils import logistic, pow_double, pow_int, sequential_sum

__all__ = ["Skill", "StrainSkill", "VariableLengthStrainSkill", "HarmonicSkill", "StrainPeak"]


@dataclass(frozen=True)
class StrainPeak:
    """VariableLengthStrainSkill.StrainPeak.

    Конструктор ОКРУГЛЯЕТ длину секции — так в C#:

        SectionLength = Math.Round(sectionLength);

    Под rate-модами времена дробные, и без округления длины расходятся
    (190.66666666667152 против 191). Math.Round в C# и round() в Python
    оба округляют половину к чётному, так что поведение совпадает.

    Внимание: накопитель total_length в _save_current_peak складывает СЫРУЮ
    длину, а вычитает уже округлённую — эта асимметрия тоже из оригинала.
    """

    value: float
    section_length: float

    def __post_init__(self) -> None:
        # round() отдаёт int для float без второго аргумента, поэтому приводим.
        object.__setattr__(self, "section_length", float(round(self.section_length)))


class Skill(ABC):
    """Skill: обрабатывает объекты и копит их сложности."""

    def __init__(self, mods: Mods) -> None:
        self.mods = mods
        self.object_difficulties: list[float] = []

    def process(self, current: OsuDifficultyHitObject) -> None:
        self.object_difficulties.append(self._process_internal(current))

    @abstractmethod
    def _process_internal(self, current: OsuDifficultyHitObject) -> float: ...

    @abstractmethod
    def difficulty_value(self) -> float: ...


class StrainSkill(Skill):
    """Страйны с секциями фиксированной длины."""

    decay_weight: float = 0.9
    section_length: int = 400

    def __init__(self, mods: Mods) -> None:
        super().__init__(mods)
        self._current_section_peak = 0.0
        self._current_section_end = 0.0
        self._strain_peaks: list[float] = []

    @abstractmethod
    def _strain_value_at(self, current: OsuDifficultyHitObject) -> float: ...

    @abstractmethod
    def _calculate_initial_strain(self, time: float, current: OsuDifficultyHitObject) -> float: ...

    def _process_internal(self, current: OsuDifficultyHitObject) -> float:
        # Первый объект страйна не даёт, поэтому секция сразу сдвигается вперёд.
        if current.index == 0:
            self._current_section_end = math.ceil(current.start_time / self.section_length) * self.section_length

        while current.start_time > self._current_section_end:
            self._strain_peaks.append(self._current_section_peak)
            self._current_section_peak = self._calculate_initial_strain(self._current_section_end, current)
            self._current_section_end += self.section_length

        strain = self._strain_value_at(current)
        self._current_section_peak = max(strain, self._current_section_peak)
        return strain

    def get_current_strain_peaks(self) -> list[float]:
        """Пики всех секций плюс пик текущей. В отличие от версии переменной
        длины, состояние здесь не меняется."""
        return [*self._strain_peaks, self._current_section_peak]

    def count_top_weighted_strains(self, difficulty_value: float) -> float:
        if not self.object_difficulties:
            return 0.0

        # Каким был бы верхний страйн, будь все страйны одинаковыми.
        consistent_top_strain = difficulty_value * (1 - self.decay_weight)
        if consistent_top_strain == 0:
            return float(len(self.object_difficulties))

        return sequential_sum(logistic(s / consistent_top_strain, 0.88, 10, 1.1) for s in self.object_difficulties)

    def difficulty_value(self) -> float:
        difficulty = 0.0
        weight = 1.0

        # Нулевые секции отбрасываются: они не влияют на сумму, но портят
        # худший случай сортировки.
        peaks = [p for p in self.get_current_strain_peaks() if p > 0]

        for strain in sorted(peaks, reverse=True):
            difficulty += strain * weight
            weight *= self.decay_weight

        return difficulty


class VariableLengthStrainSkill(Skill):
    """Страйны с секциями переменной длины и очередью отложенных значений."""

    def __init__(self, mods: Mods, decay_weight: float = 0.9, max_section_length: int = 400) -> None:
        super().__init__(mods)
        self.decay_weight = decay_weight
        self.max_section_length = max_section_length

        self._current_section_peak = 0.0
        self._current_section_begin = 0.0
        self._current_section_end = 0.0

        # Столько секций сохраняет не менее 99.999% итоговой сложности.
        self._max_stored_length = 11 / (1 - decay_weight)

        self._strain_peaks: list[StrainPeak] = []
        self._total_length = 0.0
        # Отложенные страйны: если за сильным объектом идёт слабый, сильный
        # должен получить полную секцию, а не обрезаться.
        self._queued_strains: list[tuple[float, float]] = []
        self._final_peak: StrainPeak | None = None

    @abstractmethod
    def _strain_value_at(self, current: OsuDifficultyHitObject) -> float: ...

    @abstractmethod
    def _calculate_initial_strain(self, time: float, current: OsuDifficultyHitObject) -> float: ...

    def _process_internal(self, current: OsuDifficultyHitObject) -> float:
        if current.index == 0:
            self._current_section_begin = current.start_time
            self._current_section_end = self._current_section_begin + self.max_section_length
            self._current_section_peak = self._strain_value_at(current)
            return self._current_section_peak

        self._backfill_peaks(current)

        current_strain = self._strain_value_at(current)

        if current_strain > self._current_section_peak:
            # Очередь больше не нужна: её значения слабее нового пика.
            self._queued_strains.clear()
            self._save_current_peak(current.start_time - self._current_section_begin)

            self._current_section_begin = current.start_time
            self._current_section_end = self._current_section_begin + self.max_section_length
            self._current_section_peak = current_strain
        else:
            # Из хвоста очереди убираются значения слабее текущего.
            while self._queued_strains and self._queued_strains[-1][0] < current_strain:
                self._queued_strains.pop()
            self._queued_strains.append((current_strain, current.start_time))

        return current_strain

    def _backfill_peaks(self, current: OsuDifficultyHitObject) -> None:
        """Заполняет промежуток между концом секции и текущим объектом."""
        while current.start_time > self._current_section_end:
            self._save_current_peak(self._current_section_end - self._current_section_begin)
            self._current_section_begin = self._current_section_end

            if self._queued_strains:
                strain, start_time = self._queued_strains.pop(0)
                # Секция заканчивается через max_section_length после самого
                # отложенного страйна, а не после начала секции: иначе на большом
                # разрыве между секциями возникал бы резкий скачок сложности.
                self._current_section_end = start_time + self.max_section_length
                self._current_section_peak = self._calculate_initial_strain(self._current_section_begin, current)
                self._current_section_peak = max(self._current_section_peak, strain)
            else:
                self._current_section_end = self._current_section_begin + self.max_section_length
                self._current_section_peak = self._calculate_initial_strain(self._current_section_begin, current)

    def _save_current_peak(self, section_length: float) -> None:
        if self._final_peak is not None:
            self._strain_peaks.remove(self._final_peak)
            self._final_peak = None

        peak = StrainPeak(self._current_section_peak, section_length)
        self._add_in_place(peak)
        self._total_length += section_length

        # Хвост списка отбрасывается: слишком глубокие секции уже не влияют.
        while self._total_length > self._max_stored_length * self.max_section_length:
            self._total_length -= self._strain_peaks[-1].section_length
            self._strain_peaks.pop()

    def _add_in_place(self, peak: StrainPeak) -> None:
        """ExtensionMethods.AddInPlace: вставка бинарным поиском.

        StrainPeak.CompareTo сравнивает значения в обратном порядке, поэтому
        список отсортирован по убыванию value.

        Именно алгоритм List<T>.BinarySearch из .NET, а не bisect: при равных
        значениях они дают разный индекс, а с ним и разный порядок равных пиков.
        """
        low = 0
        high = len(self._strain_peaks) - 1
        index = None

        while low <= high:
            i = low + ((high - low) >> 1)
            # list[i].CompareTo(peak), а CompareTo у StrainPeak развёрнут:
            # other.Value.CompareTo(Value).
            other = self._strain_peaks[i].value
            order = (peak.value > other) - (peak.value < other)

            if order == 0:
                index = i
                break
            if order < 0:
                low = i + 1
            else:
                high = i - 1

        self._strain_peaks.insert(low if index is None else index, peak)

    def get_current_strain_peaks(self) -> list[StrainPeak]:
        """ВНИМАНИЕ: метод меняет состояние — он лениво запечатывает последнюю
        секцию и дописывает её в список. Вызывать можно только после того, как
        обработаны все объекты, иначе расчёт будет испорчен."""
        if self._final_peak is None:
            self._final_peak = StrainPeak(
                self._current_section_peak, self._current_section_end - self._current_section_begin
            )
            self._add_in_place(self._final_peak)

        return self._strain_peaks

    def count_top_weighted_strains(self, difficulty_value: float) -> float:
        if not self.object_difficulties:
            return 0.0

        consistent_top_strain = difficulty_value * (1 - self.decay_weight)
        if consistent_top_strain == 0:
            return float(len(self.object_difficulties))

        return sequential_sum(logistic(s / consistent_top_strain, 0.88, 10, 1.1) for s in self.object_difficulties)


class HarmonicSkill(Skill):
    """Гармоническая сумма по сложностям объектов, без страйнов."""

    harmonic_scale: float = 1.0
    decay_exponent: float = 0.9

    def __init__(self, mods: Mods) -> None:
        super().__init__(mods)
        self.object_weight_sum = 0.0

    @abstractmethod
    def _object_difficulty_of(self, current: OsuDifficultyHitObject) -> float: ...

    def _process_internal(self, current: OsuDifficultyHitObject) -> float:
        return self._object_difficulty_of(current)

    def _get_transformed_difficulties(self, difficulties: list[float]) -> list[float]:
        """Позволяет скиллу занизить вес отдельных объектов перед суммированием."""
        return difficulties

    def difficulty_value(self) -> float:
        self.object_weight_sum = 0.0

        if not self.object_difficulties:
            return 0.0

        difficulties = self._get_transformed_difficulties(self.object_difficulties)

        difficulty = 0.0
        index = 0

        # Нулевые сложности исключаются — они не вносят вклад, но ухудшают сортировку.
        # Счётчик ручной, а не enumerate: так же, как в C#-оригинале.
        for obj in (v for v in sorted(difficulties, reverse=True) if v > 0):
            weight = (1 + (self.harmonic_scale / (1 + index))) / (
                pow_double(index, self.decay_exponent) + 1 + (self.harmonic_scale / (1 + index))
            )
            self.object_weight_sum += weight
            difficulty += obj * weight
            index += 1  # noqa: SIM113

        return difficulty

    def count_top_weighted_object_difficulties(self, difficulty_value: float) -> float:
        if not self.object_difficulties:
            return 0.0
        if self.object_weight_sum == 0:
            return 0.0

        consistent_top_object = difficulty_value / self.object_weight_sum
        if consistent_top_object == 0:
            return 0.0

        return sequential_sum(logistic(d / consistent_top_object, 0.88, 10, 1.1) for d in self.object_difficulties)

    @staticmethod
    def difficulty_to_performance(difficulty: float) -> float:
        """HarmonicSkill.DifficultyToPerformance. Степень записана целым литералом."""
        return 4.0 * pow_int(difficulty, 3)
