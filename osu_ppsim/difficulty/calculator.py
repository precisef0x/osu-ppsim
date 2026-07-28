"""Сборка difficulty-атрибутов и звёзд.

Источник: osu.Game.Rulesets.Osu/Difficulty/OsuDifficultyCalculator.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from ..beatmap.decoder import Beatmap
from ..beatmap.defaults import apply_defaults
from ..beatmap.objects import HitCircle, Slider, Spinner
from ..beatmap.processor import get_max_combo, post_process, reflect_vertically
from ..mods import Difficulty, Mods, apply_to_difficulty, parse_mods
from .hitobject import create_difficulty_hit_objects
from .skills.aim import Aim
from .skills.base import HarmonicSkill
from .skills.flashlight import Flashlight
from .skills.reading import Reading
from .skills.speed import Speed
from .utils import norm, pow_double, pow_int

__all__ = ["OsuDifficultyAttributes", "calculate_difficulty", "prepare_beatmap", "SUPPORTED_DIFFCALC_VERSION"]

#: Версия расчёта, которой соответствует этот порт.
SUPPORTED_DIFFCALC_VERSION: Final[int] = 20260706

#: OsuPerformanceCalculator.PERFORMANCE_BASE_MULTIPLIER и PERFORMANCE_NORM_EXPONENT.
PERFORMANCE_BASE_MULTIPLIER: Final[float] = 1.12
PERFORMANCE_NORM_EXPONENT: Final[float] = 1.1


@dataclass
class OsuDifficultyAttributes:
    star_rating: float = 0.0
    max_combo: int = 0
    aim_difficulty: float = 0.0
    aim_difficult_slider_count: float = 0.0
    speed_difficulty: float = 0.0
    speed_note_count: float = 0.0
    flashlight_difficulty: float = 0.0
    reading_difficulty: float = 0.0
    slider_factor: float = 1.0
    aim_difficult_strain_count: float = 0.0
    speed_difficult_strain_count: float = 0.0
    reading_difficult_note_count: float = 0.0
    aim_top_weighted_slider_factor: float = 0.0
    speed_top_weighted_slider_factor: float = 0.0
    hit_circle_count: int = 0
    slider_count: int = 0
    spinner_count: int = 0


def aim_difficulty_rating(difficulty_value: float) -> float:
    """OsuDifficultyCalculator.calculateAimDifficultyRating."""
    return pow_double(difficulty_value, 0.63) * 0.02275


def difficulty_rating(difficulty_value: float) -> float:
    """OsuDifficultyCalculator.calculateDifficultyRating."""
    return math.sqrt(difficulty_value) * 0.0675


def aim_difficulty_to_performance(difficulty: float) -> float:
    """OsuPerformanceCalculator.DifficultyToPerformance."""
    return 4.0 * pow_int(difficulty, 3)


def sum_cognition_difficulty(reading: float, flashlight: float) -> float:
    """OsuDifficultyCalculator.SumCognitionDifficulty.

    Когда reading больше flashlight, вклад флешлайта дополнительно занижается:
    читать плотную карту и так тяжело, фонарик добавляет к этому меньше.
    """
    if reading <= 0:
        return flashlight
    if flashlight <= 0:
        return reading

    return norm(
        PERFORMANCE_NORM_EXPONENT,
        reading,
        flashlight * min(max(flashlight / reading, 0.25), 1.0),
    )


def prepare_beatmap(beatmap: Beatmap, mods: Mods) -> None:
    """Готовит карту к расчёту: поправки модов, отражение HR, умолчания, стакинг.

    Вынесено отдельно, чтобы гейты собирали карту ровно так же, как боевой расчёт.
    Меняет карту на месте и одноразова: повторный вызов применил бы поправки
    дважды, а под HR отразил бы карту обратно.
    """
    if beatmap.processed:
        raise ValueError(
            "карта уже обработана: расчёт меняет её на месте, поэтому повторный прогон "
            "применил бы поправки модов дважды. Разберите файл заново или используйте Simulator."
        )
    beatmap.processed = True

    adjusted = apply_to_difficulty(
        mods,
        Difficulty(
            circle_size=beatmap.circle_size,
            approach_rate=beatmap.approach_rate,
            overall_difficulty=beatmap.overall_difficulty,
            drain_rate=beatmap.drain_rate,
        ),
    )
    beatmap.circle_size = adjusted.circle_size
    beatmap.approach_rate = adjusted.approach_rate
    beatmap.overall_difficulty = adjusted.overall_difficulty
    beatmap.drain_rate = adjusted.drain_rate

    if mods.hard_rock:
        # HR переворачивает карту по вертикали. В C# это происходит после
        # ApplyDefaults, но до стакинга; здесь раньше — отражение изометрично,
        # длины и времена от него не меняются.
        reflect_vertically(beatmap)

    apply_defaults(beatmap, mods)
    post_process(beatmap)


def calculate_difficulty(beatmap: Beatmap, mods: Mods | str | None = None) -> OsuDifficultyAttributes:
    """Считает difficulty-атрибуты карты с заданными модами.

    Карта изменяется на месте: настройки сложности правятся модами, объектам
    проставляются умолчания и стак. Так же устроен и C#-калькулятор.
    """
    resolved = parse_mods(mods)
    prepare_beatmap(beatmap, resolved)

    if not beatmap.hit_objects:
        return OsuDifficultyAttributes()

    objects = create_difficulty_hit_objects(
        beatmap.hit_objects, resolved.clock_rate, beatmap.overall_difficulty
    )

    aim = Aim(resolved, True)
    aim_no_sliders = Aim(resolved, False)
    speed = Speed(resolved)
    reading = Reading(resolved)
    flashlight = Flashlight(resolved, len(beatmap.hit_objects)) if resolved.flashlight else None

    skills = [aim, aim_no_sliders, speed, reading]
    if flashlight is not None:
        skills.append(flashlight)

    for obj in objects:
        for skill in skills:
            skill.process(obj)

    aim_value = aim.difficulty_value()
    aim_no_sliders_value = aim_no_sliders.difficulty_value()
    speed_value = speed.difficulty_value()
    reading_value = reading.difficulty_value()

    aim_difficult_strain_count = aim.count_top_weighted_strains(aim_value)
    speed_difficult_strain_count = speed.count_top_weighted_object_difficulties(speed_value)
    reading_difficult_note_count = reading.count_top_weighted_object_difficulties(reading_value)

    speed_notes = speed.relevant_object_count()

    aim_no_sliders_top_weighted_sliders = aim_no_sliders.count_top_weighted_sliders(aim_no_sliders_value)
    aim_no_sliders_difficult_strains = aim_no_sliders.count_top_weighted_strains(aim_no_sliders_value)
    aim_top_weighted_slider_factor = aim_no_sliders_top_weighted_sliders / max(
        1, aim_no_sliders_difficult_strains - aim_no_sliders_top_weighted_sliders
    )

    speed_top_weighted_sliders = speed.count_top_weighted_sliders(speed_value)
    speed_top_weighted_slider_factor = speed_top_weighted_sliders / max(
        1, speed_difficult_strain_count - speed_top_weighted_sliders
    )

    aim_rating = aim_difficulty_rating(aim_value)
    aim_no_sliders_rating = aim_difficulty_rating(aim_no_sliders_value)
    slider_factor = aim_no_sliders_rating / aim_rating if aim_value > 0 else 1.0

    speed_rating = difficulty_rating(speed_value)
    reading_rating = difficulty_rating(reading_value)
    flashlight_rating = difficulty_rating(flashlight.difficulty_value()) if flashlight is not None else 0.0

    base_aim = aim_difficulty_to_performance(aim_rating)
    base_speed = HarmonicSkill.difficulty_to_performance(speed_rating)
    base_reading = HarmonicSkill.difficulty_to_performance(reading_rating)
    base_flashlight = Flashlight.difficulty_to_performance(flashlight_rating)
    base_cognition = sum_cognition_difficulty(base_reading, base_flashlight)

    base_performance = norm(PERFORMANCE_NORM_EXPONENT, base_aim, base_speed, base_cognition)
    star_rating = math.cbrt(base_performance * PERFORMANCE_BASE_MULTIPLIER)

    return OsuDifficultyAttributes(
        star_rating=star_rating,
        max_combo=get_max_combo(beatmap),
        aim_difficulty=aim_rating,
        aim_difficult_slider_count=aim.get_difficult_sliders(),
        speed_difficulty=speed_rating,
        speed_note_count=speed_notes,
        flashlight_difficulty=flashlight_rating,
        reading_difficulty=reading_rating,
        slider_factor=slider_factor,
        aim_difficult_strain_count=aim_difficult_strain_count,
        speed_difficult_strain_count=speed_difficult_strain_count,
        reading_difficult_note_count=reading_difficult_note_count,
        aim_top_weighted_slider_factor=aim_top_weighted_slider_factor,
        speed_top_weighted_slider_factor=speed_top_weighted_slider_factor,
        hit_circle_count=sum(1 for h in beatmap.hit_objects if isinstance(h, HitCircle)),
        slider_count=sum(1 for h in beatmap.hit_objects if isinstance(h, Slider)),
        spinner_count=sum(1 for h in beatmap.hit_objects if isinstance(h, Spinner)),
    )
