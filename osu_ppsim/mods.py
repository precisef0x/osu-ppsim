"""Моды: разбор, скорость игры, поправки к сложности.

Источник: osu.Game.Rulesets.Osu/Mods/*.cs, osu.Game/Utils/ModUtils.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Охват — NM, rate-моды, HD, FL, HR/EZ и CL. Остальные моды не игнорируются
молча: на них поднимается UnsupportedModError. Тихо посчитать неверное число
хуже, чем отказаться.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .f32 import f32

__all__ = ["Mods", "Difficulty", "UnsupportedModError", "parse_mods", "SUPPORTED_MODS"]


class UnsupportedModError(ValueError):
    pass


#: Моды, которые расчёт поддерживает. Всё остальное отвергается одинаково:
#: делить моды на «известные игре» и «несуществующие» пришлось бы ручным
#: списком из четырёх десятков аббревиатур, который устаревает с каждым
#: релизом osu! и пользователю ничего не даёт.
SUPPORTED_MODS: Final[frozenset[str]] = frozenset({"NM", "NF", "DT", "NC", "HT", "DC", "HR", "EZ", "HD", "FL", "CL"})

_RATE: Final[dict[str, float]] = {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}


@dataclass(frozen=True)
class Mods:
    """Набор модов, влияющих на расчёт."""

    acronyms: frozenset[str]

    @property
    def clock_rate(self) -> float:
        """ModUtils.CalculateRateWithMods. Rate-моды взаимоисключающие."""
        for acronym, rate in _RATE.items():
            if acronym in self.acronyms:
                return rate
        return 1.0

    @property
    def hidden(self) -> bool:
        return "HD" in self.acronyms

    @property
    def flashlight(self) -> bool:
        return "FL" in self.acronyms

    @property
    def hard_rock(self) -> bool:
        return "HR" in self.acronyms

    @property
    def easy(self) -> bool:
        return "EZ" in self.acronyms

    @property
    def classic_slider_accuracy(self) -> bool:
        """CL с NoSliderHeadAccuracy — точность считается по stable-правилам.

        Настройка мода включена по умолчанию (OsuModClassic.NoSliderHeadAccuracy),
        а выключить её в скоре, снятом со stable, нельзя, поэтому CL здесь всегда
        означает классическую точность слайдеров.

        На геометрию и сложность мод не влияет: ClassicSliderBehaviour меняет
        только типы судейства. Комбо тоже сохраняется — вклад переезжает
        с хвоста слайдера (SmallTickHit, комбо не даёт) на сам слайдер
        (OsuJudgement вместо OsuIgnoreJudgement).
        """
        return "CL" in self.acronyms

    def __contains__(self, acronym: str) -> bool:
        return acronym in self.acronyms


def parse_mods(mods: str | list[str] | frozenset[str] | Mods | None) -> Mods:
    """Разбирает моды из строки вида "HDDT", списка или множества."""
    if isinstance(mods, Mods):
        return mods
    if mods is None:
        return Mods(frozenset())

    if isinstance(mods, str):
        text = mods.upper().replace(" ", "").replace(",", "")
        if len(text) % 2 != 0:
            raise UnsupportedModError(f"не разобрать набор модов: {mods!r}")
        acronyms = {text[i : i + 2] for i in range(0, len(text), 2)}
    else:
        acronyms = {m.upper() for m in mods}

    acronyms.discard("NM")

    unsupported = sorted(acronyms - SUPPORTED_MODS)
    if unsupported:
        raise UnsupportedModError(
            f"мод {', '.join(unsupported)} не поддерживается; охват: {', '.join(sorted(SUPPORTED_MODS))}"
        )

    rate_mods = acronyms & set(_RATE)
    if len(rate_mods) > 1:
        raise UnsupportedModError(f"несовместимые моды скорости: {', '.join(sorted(rate_mods))}")

    if {"HR", "EZ"} <= acronyms:
        raise UnsupportedModError("HR и EZ несовместимы")

    return Mods(frozenset(acronyms))


#: ModHardRock.ADJUST_RATIO и ModEasy.ADJUST_RATIO.
_HARD_ROCK_RATIO: Final[float] = 1.4
_EASY_RATIO: Final[float] = 0.5
#: CS у HR масштабируется своим коэффициентом, а не общим.
_HARD_ROCK_CIRCLE_SIZE_RATIO: Final[float] = 1.3


@dataclass(frozen=True)
class Difficulty:
    """Четыре настройки сложности, которые правят моды (BeatmapDifficulty)."""

    circle_size: float
    approach_rate: float
    overall_difficulty: float
    drain_rate: float


def apply_to_difficulty(mods: Mods, difficulty: Difficulty) -> Difficulty:
    """Поправки HR и EZ к настройкам сложности.

    Все величины в BeatmapDifficulty объявлены как float, поэтому арифметика
    идёт во float32. Возвращается новый набор, исходный не меняется.
    """
    circle_size = difficulty.circle_size
    approach_rate = difficulty.approach_rate
    overall_difficulty = difficulty.overall_difficulty
    drain_rate = difficulty.drain_rate

    if mods.hard_rock:
        ratio = f32(_HARD_ROCK_RATIO)
        overall_difficulty = min(f32(overall_difficulty * ratio), 10.0)
        approach_rate = min(f32(approach_rate * ratio), 10.0)
        drain_rate = min(f32(drain_rate * ratio), 10.0)
        # CS использует собственный коэффициент 1.3.
        circle_size = min(f32(circle_size * f32(_HARD_ROCK_CIRCLE_SIZE_RATIO)), 10.0)

    if mods.easy:
        ratio = f32(_EASY_RATIO)
        circle_size = f32(circle_size * ratio)
        approach_rate = f32(approach_rate * ratio)
        drain_rate = f32(drain_rate * ratio)
        # OsuModEasy дополнительно halves OD — в общем ModEasy этого нет.
        overall_difficulty = f32(overall_difficulty * ratio)

    return Difficulty(
        circle_size=circle_size,
        approach_rate=approach_rate,
        overall_difficulty=overall_difficulty,
        drain_rate=drain_rate,
    )
