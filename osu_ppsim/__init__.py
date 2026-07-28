"""osu-ppsim: расчёт pp за FC-прохождение карты на заданной accuracy.

Соответствует версии расчёта osu! 20260706 («2026 Q2 SR & PP release»).

    from osu_ppsim import pp_for_accuracy

    result = pp_for_accuracy("map.osu", accuracy=0.98, mods="HDDT")
    print(result.pp, result.difficulty.star_rating)

Для нескольких значений точности на одной карте — Simulator: сложность
от accuracy не зависит и считается один раз.

Охват: только osu!standard, только FC. Моды — NM, NF, DT, NC, HT, DC, HR, EZ,
HD, FL и CL; на остальных поднимается UnsupportedModError. Мод CL включает
классическую (stable) механику подсчёта точности, без него счёт лазерный.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .beatmap.decoder import Beatmap, BeatmapParseError, decode_beatmap
from .beatmap.objects import NestedType, Slider
from .difficulty.calculator import (
    SUPPORTED_DIFFCALC_VERSION,
    OsuDifficultyAttributes,
    calculate_difficulty,
)
from .mods import Mods, UnsupportedModError, parse_mods
from .performance.accuracy import (
    HitCounts,
    hit_counts_for_raw_accuracy,
    raw_accuracy_for_display,
    score_accuracy,
)
from .performance.calculator import OsuPerformanceAttributes, calculate_performance

#: Версия самой библиотеки; версия расчёта osu! — SUPPORTED_DIFFCALC_VERSION.
__version__ = "0.2.0"

__all__ = [
    # Точки входа.
    "pp_for_accuracy",
    "Simulator",
    # Что возвращается.
    "Result",
    "OsuDifficultyAttributes",
    "OsuPerformanceAttributes",
    "HitCounts",
    "Mods",
    # Что поднимается на негодном входе.
    "BeatmapParseError",
    "UnsupportedModError",
    # Версии и низкоуровневые шаги.
    "SUPPORTED_DIFFCALC_VERSION",
    "__version__",
    "decode_beatmap",
    "calculate_difficulty",
]


@dataclass
class Result:
    """Итог расчёта: pp, атрибуты сложности и использованный расклад попаданий."""

    performance: OsuPerformanceAttributes
    difficulty: OsuDifficultyAttributes
    hit_counts: HitCounts
    mods: Mods
    #: Точность, запрошенная пользователем.
    requested_accuracy: float
    #: Точность, реально достижимая при целом числе попаданий. Ровно 98%
    #: набрать обычно нельзя, и прятать это было бы нечестно.
    accuracy: float

    @property
    def pp(self) -> float:
        return self.performance.total


def _count_large_ticks(beatmap: Beatmap) -> int:
    """Число тиков и реверсов — они входят в accuracy под lazer-механикой."""
    return sum(
        1
        for hit_object in beatmap.hit_objects
        if isinstance(hit_object, Slider)
        for nested in hit_object.nested
        if nested.type in (NestedType.TICK, NestedType.REPEAT)
    )


class Simulator:
    """Карта с применёнными модами: сложность посчитана, pp считается по запросу.

    Difficulty-атрибуты не зависят от accuracy, поэтому считаются один раз:
    около 350 мс на карте в 1600 объектов против 9 мкс на каждый расчёт pp.
    """

    def __init__(self, beatmap: str | Path | Beatmap, mods: str | list[str] | Mods | None = None) -> None:
        self.mods = parse_mods(mods)
        # Расчёт меняет Beatmap на месте, поэтому второй Simulator на том же
        # объекте построить нельзя — флаг processed это запретит.
        self.beatmap = beatmap if isinstance(beatmap, Beatmap) else decode_beatmap(beatmap)

        self.difficulty = calculate_difficulty(self.beatmap, self.mods)
        self._total_objects = len(self.beatmap.hit_objects)
        self._large_ticks = _count_large_ticks(self.beatmap)

    @property
    def star_rating(self) -> float:
        return self.difficulty.star_rating

    @property
    def max_combo(self) -> int:
        return self.difficulty.max_combo

    def pp(self, accuracy: float, *, raw_accuracy: bool = False) -> Result:
        """Считает pp за FC на заданной accuracy: 0.98 либо 98 — понимаются оба.

        По умолчанию accuracy трактуется так, как её видит игрок: в lazer в неё
        входят хвосты слайдеров и тики. Флаг raw_accuracy переключает на «сырую»
        точность по кругам — в этом виде её принимает osu-tools в ключе -a.

        Под модом CL точность считается по одним кругам, поэтому обе трактовки
        совпадают и флаг ни на что не влияет.
        """
        if accuracy > 1.0:
            accuracy /= 100.0
        accuracy = min(max(accuracy, 0.0), 1.0)

        # Под CL хвосты и тики не попадают в статистику скора, то есть не входят
        # ни в перевод точности, ни в её обратный подсчёт.
        classic = self.mods.classic_slider_accuracy
        slider_count = 0 if classic else self.difficulty.slider_count
        large_ticks = 0 if classic else self._large_ticks

        # Ветка `classic` здесь НЕ лишняя, хотя при нулевых счётчиках перевод
        # алгебраически тождественный: он всё равно посчитал бы acc * 6n / 6n,
        # добавив второй круг умножения-деления к тому, что делает раскладка.
        # Оракул делает ровно один. На 181 объекте при 95% разницы последнего
        # бита хватает, чтобы сотка стала пятидесяткой — та же ловушка, что
        # и в hit_counts_for_raw_accuracy (проба cl-acc-roundtrip).
        target_raw = (
            accuracy
            if raw_accuracy or classic
            else raw_accuracy_for_display(accuracy, self._total_objects, slider_count, large_ticks)
        )

        counts = hit_counts_for_raw_accuracy(self._total_objects, target_raw)
        effective = score_accuracy(counts, slider_count, large_ticks)

        performance = calculate_performance(
            self.difficulty,
            counts,
            effective,
            self.mods,
            self.beatmap.overall_difficulty,
            self.beatmap.drain_rate,
        )

        return Result(
            performance=performance,
            difficulty=self.difficulty,
            hit_counts=counts,
            mods=self.mods,
            requested_accuracy=accuracy,
            accuracy=effective,
        )


def pp_for_accuracy(
    beatmap: str | Path | Beatmap,
    accuracy: float,
    mods: str | list[str] | Mods | None = None,
    *,
    raw_accuracy: bool = False,
) -> Result:
    """Считает pp за FC-прохождение на заданной accuracy.

    Разовый расчёт. Для нескольких значений точности на одной карте берите
    Simulator: он считает сложность один раз.
    """
    return Simulator(beatmap, mods).pp(accuracy, raw_accuracy=raw_accuracy)
