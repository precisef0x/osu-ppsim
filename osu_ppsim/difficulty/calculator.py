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
from .legacy_score import (
    difficulty_peppy_stars,
    drain_length,
    maximum_legacy_combo_score,
    nested_score_per_object,
)
from .skills.aim import Aim
from .skills.base import HarmonicSkill
from .skills.flashlight import Flashlight
from .skills.reading import Reading
from .skills.speed import Speed
from .utils import norm, pow_double, pow_int

__all__ = [
    "OsuDifficultyAttributes",
    "ObjectDifficulties",
    "calculate_difficulty",
    "calculate_difficulty_with_strains",
    "prepare_beatmap",
    "SUPPORTED_DIFFCALC_VERSION",
]

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

    #: Величины легаси-скоринга. Нужны только оценке промахов по сумме очков
    #: классического скора. Источники разные, как и в C#: множитель снимается
    #: с ИСХОДНОЙ карты (WorkingBeatmap.Beatmap), а две остальные — с playable,
    #: то есть уже с применёнными модами. На поддерживаемых модах это ничего
    #: не меняет: HR и EZ правят CS/AR/OD/HP, а число тиков и хвостов зависит
    #: от тайминга и длины слайдеров.
    legacy_score_base_multiplier: float = 0.0
    nested_score_per_object: float = 0.0
    maximum_legacy_combo_score: float = 0.0


@dataclass(frozen=True)
class ObjectDifficulties:
    """Сложность каждого объекта по каждому скиллу — ряд, из которого строится
    график сложности карты.

    Это `Skill.ObjectDifficulties` из C#: то, что скилл насчитал на объекте, ещё
    до того как значения сложились в итоговую сложность. Все ряды выровнены друг
    с другом и с `times`, длина у всех одна.

    Почему не «пики страйнов по секциям в 400 мс», в виде которых их отдаёт
    rosu-pp: в ребалансе 2026 Q2 скиллы разошлись по трём схемам накопления.
    Секции фиксированной длины остались только у Flashlight. У Aim секции
    переменной длины, и список пиков отсортирован по величине, а не по времени —
    как ось X он не годится. Speed и Reading вообще не страйновые: это
    HarmonicSkill, они складывают отсортированные сложности объектов, и никаких
    секций у них нет. Пообъектный ряд есть у всех четырёх, сопоставим между ними
    и сверен с оракулом — поэтому наружу отдаётся он.

    Ряд `reading` наследует известное отклонение эвалуатора Reading в 1-4 ulp
    (docs/ACCURACY.md); остальные сходятся с C# бит в бит.
    """

    #: Время каждого объекта в миллисекундах, УЖЕ ПОДЕЛЁННОЕ на clock rate, —
    #: так его видит расчёт сложности (`DifficultyHitObject.StartTime`). Это
    #: время звучания, а не время на таймлайне карты: под DT оно в 1.5 раза
    #: меньше исходного. Чтобы вернуться к временам из .osu, умножьте на
    #: `mods.clock_rate`.
    times: tuple[float, ...] = ()

    aim: tuple[float, ...] = ()
    aim_no_sliders: tuple[float, ...] = ()
    speed: tuple[float, ...] = ()
    reading: tuple[float, ...] = ()

    #: None без мода FL: без него скилл не создаётся вовсе, и нули тут были бы
    #: враньём — они означали бы «фонарик посчитан и дал ноль».
    flashlight: tuple[float, ...] | None = None

    def __len__(self) -> int:
        return len(self.times)


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
            "beatmap already processed: the calculation mutates it in place, so a second run "
            "would apply the mod adjustments twice. Decode the file again, or use Simulator."
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
    return calculate_difficulty_with_strains(beatmap, mods)[0]


def calculate_difficulty_with_strains(
    beatmap: Beatmap, mods: Mods | str | None = None
) -> tuple[OsuDifficultyAttributes, ObjectDifficulties]:
    """То же самое, но вдобавок отдаёт пообъектные сложности всех скиллов.

    Отдельная точка входа, а не флаг: расчёт от этого не меняется ни на бит,
    меняется только то, выбрасываются ли посчитанные ряды или отдаются наружу.
    Копятся они всё равно — `Skill.process` складывает их безусловно, — поэтому
    цена здесь только в копировании списков: 36 мкс против 250 мс самого расчёта
    на карте в 1600 объектов. Удерживаемая память растёт с числом объектов и
    сохраняемых рядов; её размер зависит от реализации Python.
    """
    resolved = parse_mods(mods)

    # ВНИМАНИЕ: множитель ScoreV1 считается от ИСХОДНЫХ настроек сложности,
    # до поправок модов — в C# он берётся из WorkingBeatmap.Beatmap, а не из
    # playable-карты. Поэтому снимается до prepare_beatmap, который правит
    # HP/OD/CS на месте.
    legacy_multiplier = difficulty_peppy_stars(beatmap, len(beatmap.hit_objects), drain_length(beatmap))

    prepare_beatmap(beatmap, resolved)

    if not beatmap.hit_objects:
        # flashlight здесь пустой ряд, а не None: None означает «мода FL нет»,
        # и на пустой карте под FL он соврал бы. Ниже по коду ровно так же.
        return OsuDifficultyAttributes(), ObjectDifficulties(
            flashlight=() if resolved.flashlight else None
        )

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

    strains = ObjectDifficulties(
        times=tuple(o.start_time for o in objects),
        aim=tuple(aim.object_difficulties),
        aim_no_sliders=tuple(aim_no_sliders.object_difficulties),
        speed=tuple(speed.object_difficulties),
        reading=tuple(reading.object_difficulties),
        flashlight=None if flashlight is None else tuple(flashlight.object_difficulties),
    )

    attributes = OsuDifficultyAttributes(
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
        legacy_score_base_multiplier=legacy_multiplier,
        nested_score_per_object=nested_score_per_object(beatmap, len(beatmap.hit_objects)),
        maximum_legacy_combo_score=maximum_legacy_combo_score(beatmap, legacy_multiplier),
    )

    return attributes, strains
