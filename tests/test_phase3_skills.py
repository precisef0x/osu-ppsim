"""Гейт фазы 3: скиллы и эвалуаторы.

Сверяются три уровня. Пообъектные значения эвалуаторов ловят ошибку там, где она
возникла; значения скиллов и пики страйнов — там, где она накопилась. Без первого
уровня локализовать расхождение внутри скилла нечем.

Третий уровень — то, что из всего этого видно снаружи: Simulator.object_difficulties.
Сами по себе ряды сверены на втором уровне, но там скиллы собраны руками; здесь
проверяется, что публичный путь отдаёт именно их и в том же порядке — что aim
не поменялся местами с aim_no_sliders, а ось времени взята с difficulty-объектов,
то есть уже поделена на clock rate.

Запуск: python3 tests/test_phase3_skills.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare import load_fixture  # noqa: E402
from reference import REAL_WORLD_BEATMAPS, REFERENCE_BEATMAPS  # noqa: E402

from osu_ppsim import Simulator  # noqa: E402
from osu_ppsim.beatmap.decoder import decode_beatmap  # noqa: E402
from osu_ppsim.difficulty.calculator import prepare_beatmap  # noqa: E402
from osu_ppsim.difficulty.evaluators import aim as aim_eval  # noqa: E402
from osu_ppsim.difficulty.evaluators import flashlight as fl_eval  # noqa: E402
from osu_ppsim.difficulty.evaluators import reading as reading_eval  # noqa: E402
from osu_ppsim.difficulty.evaluators import rhythm as rhythm_eval  # noqa: E402
from osu_ppsim.difficulty.evaluators import speed as speed_eval  # noqa: E402
from osu_ppsim.difficulty.hitobject import create_difficulty_hit_objects  # noqa: E402
from osu_ppsim.difficulty.skills.aim import Aim  # noqa: E402
from osu_ppsim.difficulty.skills.flashlight import Flashlight  # noqa: E402
from osu_ppsim.difficulty.skills.reading import Reading  # noqa: E402
from osu_ppsim.difficulty.skills.speed import Speed  # noqa: E402
from osu_ppsim.mods import Mods, parse_mods  # noqa: E402

BEATMAPS = (
    Path(__file__).resolve().parent.parent
    / "oracle/osu/osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps"
)

MOD_SETS = ("NM", "HD", "DT", "FL", "HDDT", "HR", "EZ")

#: Известное отклонение в 1-4 ulp у эвалуатора Reading. Разобрано в фазе 4:
#: из семнадцати атрибутов оно задевает только reading_difficult_note_count,
#: который в pp-формуле встречается лишь внутри `if (effectiveMissCount > 0)`,
#: то есть при FC не используется вовсе. Звёзды и reading_difficulty сходятся
#: точно. Считаем отдельно, чтобы гейт не был постоянно красным.
KNOWN_ULP_DRIFT = ("reading",)

#: Как в снапшот-гейте: относительный допуск с абсолютным запасом около нуля.
READING_PRECISION = 1e-12


def build(name: str, mods: Mods):
    beatmap = decode_beatmap(BEATMAPS / f"{name}.osu")
    prepare_beatmap(beatmap, mods)
    objects = create_difficulty_hit_objects(beatmap.hit_objects, mods.clock_rate, beatmap.overall_difficulty)
    return beatmap, objects


def check_evaluators(objects, expected, mods: Mods) -> tuple[list[str], int]:
    """Пообъектная сверка всех семи эвалуаторов."""
    checks = {
        "reading": lambda o: reading_eval.evaluate_difficulty_of(o, mods.hidden),
        "speed": speed_eval.evaluate_difficulty_of,
        "rhythm": rhythm_eval.evaluate_difficulty_of,
        "flashlight": lambda o: fl_eval.evaluate_difficulty_of(o, mods),
        "agility": aim_eval.evaluate_agility,
        "snap_aim": lambda o: aim_eval.evaluate_snap(o, True),
        "snap_aim_no_sliders": lambda o: aim_eval.evaluate_snap(o, False),
        "flow_aim": lambda o: aim_eval.evaluate_flow(o, True),
        "flow_aim_no_sliders": lambda o: aim_eval.evaluate_flow(o, False),
    }

    failures: list[str] = []
    drift = 0
    for index, (obj, exp) in enumerate(zip(objects, expected, strict=True)):
        for key, fn in checks.items():
            value = fn(obj)
            if value == exp[key]:
                continue
            if key in KNOWN_ULP_DRIFT:
                drift += 1
                continue
            failures.append(f"эвалуатор {key}[{index}]: {value!r} != {exp[key]!r}")
    return failures, drift


def check_skills(beatmap, objects, expected_skills, mods: Mods) -> list[str]:
    """Сверка итоговых значений скиллов, пообъектных сложностей и пиков."""
    skills: list = [Aim(mods, True), Aim(mods, False), Speed(mods), Reading(mods)]
    if mods.flashlight:
        skills.append(Flashlight(mods, len(beatmap.hit_objects)))

    for obj in objects:
        for skill in skills:
            skill.process(obj)

    failures: list[str] = []

    for skill in skills:
        name = type(skill).__name__
        matches = [
            s
            for s in expected_skills
            if s["name"] == name
            and (not isinstance(skill, Aim) or s["include_sliders"] == skill.include_sliders)
        ]
        if not matches:
            failures.append(f"в фикстуре нет скилла {name}")
            continue
        exp = matches[0]

        # У Reading накопленные величины наследуют отклонение эвалуатора.
        tolerated = isinstance(skill, Reading)

        value = skill.difficulty_value()
        if value != exp["difficulty_value"] and not tolerated:
            failures.append(f"{name}.difficulty_value: {value!r} != {exp['difficulty_value']!r}")

        if not tolerated:
            for i, (a, b) in enumerate(zip(skill.object_difficulties, exp["object_difficulties"], strict=True)):
                if a != b:
                    failures.append(f"{name}.object_difficulties[{i}]: {a!r} != {b!r}")
                    break

        expected_peaks = exp.get("strain_peaks")
        if expected_peaks is None:
            continue

        if isinstance(skill, Aim):
            peaks = [(p.value, p.section_length) for p in skill.get_current_strain_peaks()]
            wanted = [(p["value"], p["section_length"]) for p in expected_peaks]
        else:
            peaks = list(skill.get_current_strain_peaks())
            wanted = list(expected_peaks)

        if peaks != wanted:
            failures.append(f"{name}.strain_peaks расходятся ({len(peaks)} против {len(wanted)})")

    return failures


def check_object_difficulties(name: str, mods: Mods, fixture) -> list[str]:
    """Сверка публичного ряда сложностей с дампом оракула.

    Карта декодируется заново: build() уже израсходовал свою (расчёт правит её
    на месте), а Simulator обязан пройти весь путь сам.
    """
    series = Simulator(BEATMAPS / f"{name}.osu", mods).object_difficulties
    failures: list[str] = []

    # Ось X — времена difficulty-объектов, а не самих нот: первый объект прыжка
    # не образует, поэтому рядов на один меньше, чем объектов на карте.
    wanted_times = [o["start_time"] for o in fixture["difficulty_objects"]]
    if list(series.times) != wanted_times:
        failures.append(f"times расходятся ({len(series.times)} против {len(wanted_times)})")

    expected: dict[str, list[float]] = {}
    for skill in fixture["skills"]:
        if skill["name"] == "Aim":
            key = "aim" if skill["include_sliders"] else "aim_no_sliders"
        else:
            key = skill["name"].lower()
        expected[key] = skill["object_difficulties"]

    for key in ("aim", "aim_no_sliders", "speed", "reading", "flashlight"):
        got = getattr(series, key)

        if key == "flashlight" and not mods.flashlight:
            # Без FL скилла нет вовсе, и ноли тут были бы враньём.
            if got is not None:
                failures.append("flashlight без мода FL обязан быть None")
            continue

        wanted = expected.get(key)
        if wanted is None:
            # Как и в check_skills: пропажа скилла из фикстуры — это отказ гейта,
            # а не повод упасть с KeyError.
            failures.append(f"в фикстуре нет ряда {key}")
            continue

        # У Reading ряд наследует известное отклонение эвалуатора в 1-4 ulp.
        # Допуск оставляет запас для libm, но значения всё равно проверяются.
        if key == "reading":
            if got is None or len(got) != len(wanted):
                failures.append("reading: длина ряда разошлась")
                continue
            for index, (value, expected_value) in enumerate(zip(got, wanted, strict=True)):
                if (
                    not math.isfinite(value)
                    or not math.isfinite(expected_value)
                    or abs(value - expected_value) > READING_PRECISION * max(abs(expected_value), 1.0)
                ):
                    failures.append(f"reading[{index}]: {value!r} != {expected_value!r}")
                    break
            continue

        if got is None or list(got) != wanted:
            failures.append(f"{key}: ряд разошёлся с оракулом")

    return failures


def main() -> int:
    all_failures: list[str] = []
    total_drift = 0
    checked = 0
    series_values = 0

    print(f"{'карта':<22} {'моды':<6} {'объектов':>9}  эвалуаторы  скиллы  ряды")
    print("-" * 68)

    for ref in (*REFERENCE_BEATMAPS, *REAL_WORLD_BEATMAPS):
        for mod_set in MOD_SETS:
            try:
                fixture = load_fixture(ref.name, mod_set)
            except FileNotFoundError as exc:
                all_failures.append(str(exc))
                continue

            mods = parse_mods("" if mod_set == "NM" else mod_set)
            beatmap, objects = build(ref.name, mods)

            ev_failures, ev_drift = check_evaluators(objects, fixture["evaluators"], mods)
            total_drift += ev_drift
            sk_failures = check_skills(beatmap, objects, fixture["skills"], mods)
            od_failures = check_object_difficulties(ref.name, mods, fixture)
            all_failures += [f"{ref.name}/{mod_set}: {f}" for f in ev_failures + sk_failures + od_failures]
            checked += 1
            series_values += len(fixture["difficulty_objects"]) + sum(
                len(skill["object_difficulties"]) for skill in fixture["skills"]
            )

            print(
                f"{ref.name:<22} {mod_set:<6} {len(objects):>9}  "
                f"{'OK' if not ev_failures else str(len(ev_failures)):>10}  "
                f"{'OK' if not sk_failures else str(len(sk_failures)):>6}  "
                f"{'OK' if not od_failures else str(len(od_failures))}"
            )

    print("-" * 68)
    expected_combinations = len((*REFERENCE_BEATMAPS, *REAL_WORLD_BEATMAPS)) * len(MOD_SETS)
    print(f"комбинаций: {checked}/{expected_combinations}, значений публичных рядов: {series_values}")

    if total_drift:
        print(f"известное отклонение Reading в 1-4 ulp: {total_drift} значений (см. docs/PORTING.md)")

    if all_failures:
        print(f"РАСХОЖДЕНИЙ: {len(all_failures)}\n")
        for failure in all_failures[:15]:
            print(f"  {failure}")
        if len(all_failures) > 15:
            print(f"  ... и ещё {len(all_failures) - 15}")
        return 1

    print("Гейт фазы 3 пройден: расхождений нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
