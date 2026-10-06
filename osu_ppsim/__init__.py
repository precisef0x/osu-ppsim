"""osu-ppsim: расчёт pp для карты osu!standard.

Соответствует версии расчёта osu! 20260706 («2026 Q2 SR & PP release»).

    from osu_ppsim import pp_for_accuracy

    result = pp_for_accuracy("map.osu", accuracy=0.98, mods="HDDT")
    print(result.pp, result.difficulty.star_rating)

Для нескольких значений точности на одной карте — Simulator: сложность
от accuracy не зависит и считается один раз. Он же считает произвольный скор:

    sim.score(Score(counts=HitCounts(great=1542, ok=65, meh=0, miss=5),
                    max_combo=1800))

Карта берётся с диска по пути либо из байтов — скачанной по HTTP на диск
попадать не обязательно. Simulator.object_difficulties отдаёт пообъектную
сложность по каждому скиллу: ряд, из которого строится график сложности карты.

Охват: только osu!standard. Моды — NM, NF, DT, NC, HT, DC, HR, EZ, HD, FL и CL;
на остальных поднимается UnsupportedModError. Мод CL включает классическую
(stable) механику подсчёта точности, без него счёт лазерный. Противоречия
в счётчиках скора поднимают InvalidScoreError (score(..., validate=False) отключает).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .beatmap.decoder import (
    Beatmap,
    BeatmapParseError,
    decode_beatmap,
    decode_beatmap_bytes,
    decode_beatmap_string,
)
from .beatmap.objects import NestedType, Slider
from .difficulty.calculator import (
    SUPPORTED_DIFFCALC_VERSION,
    ObjectDifficulties,
    OsuDifficultyAttributes,
    calculate_difficulty,
    calculate_difficulty_with_strains,
)
from .mods import Mods, UnsupportedModError, parse_mods
from .performance.accuracy import (
    HitCounts,
    InvalidScoreError,
    Score,
    hit_counts_for_raw_accuracy,
    raw_accuracy_for_display,
    score_accuracy,
    validate_score,
)
from .performance.calculator import OsuPerformanceAttributes, calculate_performance

#: Версия самой библиотеки; версия расчёта osu! — SUPPORTED_DIFFCALC_VERSION.
__version__ = "0.4.0"

__all__ = [
    # Точки входа.
    "pp_for_accuracy",
    "Simulator",
    # Что возвращается.
    "Result",
    "OsuDifficultyAttributes",
    "OsuPerformanceAttributes",
    "ObjectDifficulties",
    "HitCounts",
    "Score",
    "Mods",
    # Что поднимается на негодном входе.
    "BeatmapParseError",
    "UnsupportedModError",
    "InvalidScoreError",
    "validate_score",
    # Версии и низкоуровневые шаги.
    "SUPPORTED_DIFFCALC_VERSION",
    "__version__",
    "decode_beatmap",
    "decode_beatmap_bytes",
    "decode_beatmap_string",
    "calculate_difficulty",
    "calculate_difficulty_with_strains",
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


#: Откуда берётся карта. str и Path — это ПУТЬ к файлу, bytes — само СОДЕРЖИМОЕ
#: .osu, ещё не декодированное в строку: карта, скачанная по HTTP или добытая из
#: архива, на диск попадать не обязана. Уже декодированную строку принимает
#: decode_beatmap_string — угадывать по виду str, путь это или содержимое, было бы
#: гаданием, а тихо угадать неверно хуже, чем потребовать явности.
BeatmapSource = str | Path | bytes | bytearray | Beatmap


def _load_beatmap(source: BeatmapSource) -> Beatmap:
    if isinstance(source, Beatmap):
        return source
    if isinstance(source, (bytes, bytearray)):
        return decode_beatmap_bytes(source)
    return decode_beatmap(source)


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
    около 280 мс на карте в 1600 объектов против 12 мкс на каждый расчёт pp.

    Карта принимается путём (str или Path), содержимым .osu в байтах или уже
    разобранным Beatmap. Помимо pp наружу отдаётся object_difficulties —
    пообъектная сложность каждого скилла, ряд для графика сложности.
    """

    def __init__(self, beatmap: BeatmapSource, mods: str | list[str] | Mods | None = None) -> None:
        self.mods = parse_mods(mods)
        # Расчёт меняет Beatmap на месте, поэтому второй Simulator на том же
        # объекте построить нельзя — флаг processed это запретит.
        self.beatmap = _load_beatmap(beatmap)

        #: Пообъектная сложность по каждому скиллу — ряд для графика сложности.
        #: Достаётся даром: скиллы копят её по ходу того же расчёта.
        self.difficulty, self.object_difficulties = calculate_difficulty_with_strains(self.beatmap, self.mods)
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
        return self.score(Score(counts=counts), requested_accuracy=accuracy)

    def score(self, score: Score, *, requested_accuracy: float | None = None, validate: bool = True) -> Result:
        """Считает pp за произвольный скор: с промахами, потерянным комбо,
        недодержанными хвостами и пропущенными тиками.

        Значения по умолчанию в `Score` описывают FC, поэтому `pp()` — частный
        случай этого метода, а не отдельная ветка расчёта.

        Проверяются границы счётчиков, сумма попаданий и связь потерь с комбо.
        None у комбо означает максимум карты, поэтому при потерях нужно задать
        комбо явно. Противоречие поднимает InvalidScoreError. Проверка не
        восстанавливает порядок попаданий и не доказывает достижимость скора.
        validate=False отключает проверку и считает как есть; сам расчёт от
        флага не зависит ни на бит — так гейты сверяют с оракулом и поведение
        за пределами разумной области.
        """
        classic = self.mods.classic_slider_accuracy
        if validate:
            validate_score(
                score,
                total_objects=self._total_objects,
                beatmap_max_combo=self.difficulty.max_combo,
                # Границы у карты одни, механика судейства их не меняет: под CL
                # хвосты и тики не судятся, но их всё равно не больше, чем есть.
                slider_count=self.difficulty.slider_count,
                large_tick_count=self._large_ticks,
                classic_slider_accuracy=classic,
            )

        slider_count = 0 if classic else self.difficulty.slider_count
        large_ticks = 0 if classic else self._large_ticks

        effective = score_accuracy(
            score.counts,
            slider_count,
            large_ticks,
            slider_tail_hits=None if classic else score.slider_tail_hits,
            large_tick_misses=0 if classic else score.large_tick_misses,
        )

        performance = calculate_performance(
            self.difficulty,
            score,
            effective,
            self.mods,
            self.beatmap.overall_difficulty,
            self.beatmap.drain_rate,
        )

        return Result(
            performance=performance,
            difficulty=self.difficulty,
            hit_counts=score.counts,
            mods=self.mods,
            requested_accuracy=effective if requested_accuracy is None else requested_accuracy,
            accuracy=effective,
        )


def pp_for_accuracy(
    beatmap: BeatmapSource,
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
