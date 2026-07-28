"""Эталонные значения из ppy/osu.

Источник: osu.Game.Rulesets.Osu.Tests/OsuDifficultyCalculatorTest.cs
Пин: ppy/osu @ 52461f1b82672f019867c2078b357d3cb5d1f130 (Version = 20260706)

ВНИМАНИЕ: это НЕ точная цель порта, а только грубая проверка вменяемости.

Литералы держатся на свободном допуске ppy 1e-5 и успевают отстать от master:
последний раз их правили 15.07.2026 (#38276), а коммиты в диффкалк были 25.07 (#38199)
и 27.07 (#38200). Замеренное расхождение оракула с ними — порядка 6e-06: тест ppy
проходит, но для валидации порта такой точности мало.

Точной целью служит полноточный JSON оракула (tools/oracle.py), снятый с запиненного
коммита, — там сверяемся до ~1e-9.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Допуск ppy из DifficultyCalculatorTest.CHECK_PRECISION. Свободный — см. шапку модуля.
PPY_CHECK_PRECISION = 1e-5

#: Допуск для сверки с оракулом: тот же путь загрузки, та же сборка, расхождений быть не должно.
ORACLE_PRECISION = 1e-9


@dataclass(frozen=True)
class ReferenceBeatmap:
    """Карта из тестового набора ppy с известными ответами."""

    name: str
    max_combo: int
    #: звёзды по набору модов; ключ — отсортированная аббревиатура, "" означает NM
    stars: dict[str, float] = field(default_factory=dict)
    note: str = ""


REFERENCE_BEATMAPS: tuple[ReferenceBeatmap, ...] = (
    ReferenceBeatmap(
        name="diffcalc-test",
        max_combo=239,
        stars={"": 6.5243170265483581, "DT": 9.4677607900646308},
        note="основная синтетическая карта диффкалка",
    ),
    ReferenceBeatmap(
        name="zero-length-sliders",
        max_combo=54,
        stars={"": 1.3280410795791415, "DT": 1.6856612715618886},
        note="краевой случай: слайдеры нулевой длины",
    ),
    ReferenceBeatmap(
        name="very-fast-slider",
        max_combo=4,
        stars={"": 0.40867325147697559, "DT": 0.53588473186572561},
        note="краевой случай: сверхбыстрый слайдер",
    ),
    ReferenceBeatmap(
        name="nan-slider",
        max_combo=6,
        stars={"": 0.87058175794353554},
        note="краевой случай: слайдер, дающий NaN при наивном расчёте",
    ),
    ReferenceBeatmap(
        name="801165",
        max_combo=2359,
        stars={"": 6.3059767387139756},
        note="реальная карта; единственная длинная — ловит накопление ошибки",
    ),
)

#: Мод Classic не должен менять звёзды — он влияет только на подсчёт очков.
#: (OsuDifficultyCalculatorTest.TestClassicMod: те же значения, что и NM.)
CLASSIC_MATCHES_NOMOD = ("diffcalc-test", "zero-length-sliders", "very-fast-slider")

#: Реальные карты, добавленные ПОСЛЕ того, как порт был объявлен готовым.
#: Проверка вне обучающей выборки нашла на них две настоящие ошибки, которых
#: не поймали 13 карт и 300 комбинаций из основного набора:
#:
#: * число точек аппроксимации дуги считалось в double вместо float32
#:   (1124896, расхождение звёзд 1e-7);
#: * смещение стака округлялось один раз в конце вместо двух умножений
#:   во float32 (EZ на 1341554 и 2593923, расхождение 1e-10).
#:
#: Оставлены в наборе навсегда: обе ошибки проявляются только здесь.
REAL_WORLD_BEATMAPS: tuple[ReferenceBeatmap, ...] = (
    ReferenceBeatmap(
        name="1124896",
        max_combo=1428,
        note="два спиннера и дуга, где число точек аппроксимации зависит от точности деления",
    ),
    ReferenceBeatmap(
        name="1341554",
        max_combo=877,
        note="под EZ вылезает округление смещения стака",
    ),
    ReferenceBeatmap(
        name="2593923",
        max_combo=1429,
        note="то же, что и 1341554, независимое подтверждение",
    ),
    ReferenceBeatmap(name="basic", max_combo=44, note="короткая карта со спиннерами"),
    ReferenceBeatmap(
        name="old-stacking",
        max_combo=35,
        note=(
            "формат v3. Раньше лежала только в GEOMETRY_EDGE_CASES, которые гейт "
            "фазы 5 не перебирает, — из-за чего неверная настройка декодера "
            "(сдвиг 24 мс) дожила до проверки на реальном корпусе"
        ),
    ),
)

#: Дополнительные карты из того же каталога, полезные для фазы 1 (геометрия слайдеров).
#: Эталонных звёзд у них нет — проверяются только через оракул.
GEOMETRY_EDGE_CASES = (
    "multi-segment-slider",
    "colinear-perfect-curve",
    "slider-paths-edge-case",
    "slider-ticks-edge-case",
    "slider-ticks",
    "uneven-repeat-slider",
    "repeat-slider",
)
