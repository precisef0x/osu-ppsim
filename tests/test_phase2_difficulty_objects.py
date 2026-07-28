"""Гейт фазы 2: OsuDifficultyHitObject.

Сверяет пообъектно все поля препроцессинга с дампами оракула, включая прогон
с DT — так проверяется обработка clock rate до того, как от неё начнут зависеть
скиллы. Запуск: python3 tests/test_phase2_difficulty_objects.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare import load_fixture, values_equal  # noqa: E402
from reference import GEOMETRY_EDGE_CASES, REAL_WORLD_BEATMAPS, REFERENCE_BEATMAPS  # noqa: E402

from osu_ppsim.beatmap.decoder import decode_beatmap  # noqa: E402
from osu_ppsim.difficulty.calculator import prepare_beatmap  # noqa: E402
from osu_ppsim.difficulty.hitobject import create_difficulty_hit_objects  # noqa: E402
from osu_ppsim.mods import parse_mods  # noqa: E402

BEATMAPS = (
    Path(__file__).resolve().parent.parent
    / "oracle/osu/osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps"
)

#: Простые поля, читаемые с объекта напрямую.
FIELDS = (
    "start_time",
    "end_time",
    "delta_time",
    "adjusted_delta_time",
    "last_object_end_delta_time",
    "preempt",
    "jump_distance",
    "lazy_jump_distance",
    "minimum_jump_distance",
    "minimum_jump_time",
    "travel_distance",
    "travel_time",
    "lazy_travel_distance",
    "lazy_travel_time",
    "angle",
    "normalised_vector_angle",
    "small_circle_bonus",
    "overall_difficulty",
    "hit_window_great",
)

#: Наборы модов и соответствующий им clock rate.
#: HD здесь обязателен: мод меняет TimeFadeIn у не-слайдеров, а от него зависит
#: начало затухания в OpacityAt. Без HD-фикстур эта ветка не проверяется вовсе —
#: ошибка всплыла только в фазе 3, на скилле Reading.
MOD_SETS = (("NM", 1.0), ("DT", 1.5), ("HD", 1.0), ("HDDT", 1.5), ("HR", 1.0), ("EZ", 1.0))


def check(name: str, mods: str, clock_rate: float) -> tuple[int, int, list[str]]:
    fixture = load_fixture(name, mods)
    beatmap = decode_beatmap(BEATMAPS / f"{name}.osu")
    prepare_beatmap(beatmap, parse_mods("" if mods == "NM" else mods))

    objects = create_difficulty_hit_objects(beatmap.hit_objects, clock_rate, beatmap.overall_difficulty)
    expected = fixture["difficulty_objects"]

    failures: list[str] = []
    if len(objects) != len(expected):
        failures.append(f"{name}/{mods}: объектов {len(objects)}, ожидалось {len(expected)}")
        return 0, len(failures), failures

    checked = 0
    for obj, exp in zip(objects, expected, strict=True):
        values = {field: getattr(obj, field) for field in FIELDS}
        # Прозрачность — вход скилла Reading, проверяется в обеих ветках HD.
        values["opacity_at_click"] = obj.opacity_at(obj.base_object.start_time, False)
        values["opacity_at_click_hidden"] = obj.opacity_at(obj.base_object.start_time, True)

        for field, value in values.items():
            checked += 1
            if not values_equal(field, value, exp[field]):
                failures.append(f"{name}/{mods}[{exp['index']}].{field}: {value!r} != {exp[field]!r}")

    return checked, len(failures), failures


def main() -> int:
    names = [b.name for b in (*REFERENCE_BEATMAPS, *REAL_WORLD_BEATMAPS)] + list(GEOMETRY_EDGE_CASES)
    all_failures: list[str] = []
    total_checked = 0

    print(f"{'карта':<24} {'моды':<5} {'объектов':>9} {'значений':>9}  статус")
    print("-" * 66)

    for name in names:
        for mods, clock_rate in MOD_SETS:
            try:
                fixture = load_fixture(name, mods)
            except FileNotFoundError:
                continue

            checked, failed, failures = check(name, mods, clock_rate)
            all_failures += failures
            total_checked += checked

            count = len(fixture["difficulty_objects"])
            status = "OK" if not failed else f"{failed} расхождений"
            print(f"{name:<24} {mods:<5} {count:>9} {checked:>9}  {status}")

    print("-" * 66)
    print(f"проверено значений: {total_checked}")

    if all_failures:
        print(f"РАСХОЖДЕНИЙ: {len(all_failures)}\n")
        for failure in all_failures[:15]:
            print(f"  {failure}")
        if len(all_failures) > 15:
            print(f"  ... и ещё {len(all_failures) - 15}")
        return 1

    print("Гейт фазы 2 пройден: расхождений нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
