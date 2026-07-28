"""Гейт фазы 1: разбор карты, геометрия слайдеров, стакинг, MaxCombo.

Сверяется с полноточными дампами оракула (fixtures/raw), снятыми с запиненного
коммита ppy/osu. Запуск: python3 tests/test_phase1_beatmap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare import load_fixture, values_equal  # noqa: E402
from reference import GEOMETRY_EDGE_CASES, REAL_WORLD_BEATMAPS, REFERENCE_BEATMAPS  # noqa: E402

from osu_ppsim.beatmap.decoder import decode_beatmap  # noqa: E402
from osu_ppsim.beatmap.defaults import apply_defaults  # noqa: E402
from osu_ppsim.beatmap.objects import HitCircle, Slider, Spinner  # noqa: E402
from osu_ppsim.beatmap.processor import get_max_combo, post_process  # noqa: E402

BEATMAPS = (
    Path(__file__).resolve().parent.parent
    / "oracle/osu/osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps"
)

TYPE_NAMES = {HitCircle: "HitCircle", Slider: "Slider", Spinner: "Spinner"}

#: Поля объекта, сверяемые для всех типов.
COMMON_FIELDS = ("start_time", "x", "y", "stacked_x", "stacked_y", "stack_height", "radius", "scale", "time_preempt")

#: Поля, специфичные для слайдера.
SLIDER_FIELDS = ("repeat_count", "span_count", "span_duration", "duration", "end_time", "velocity", "tick_distance")


def build(name: str):
    beatmap = decode_beatmap(BEATMAPS / f"{name}.osu")
    apply_defaults(beatmap)
    post_process(beatmap)
    return beatmap


def check_beatmap(name: str) -> list[str]:
    fixture = load_fixture(name)
    beatmap = build(name)
    expected_objects = fixture["beatmap"]
    failures: list[str] = []

    if len(beatmap.hit_objects) != len(expected_objects):
        failures.append(f"{name}: объектов {len(beatmap.hit_objects)}, ожидалось {len(expected_objects)}")
        return failures

    for obj, expected in zip(beatmap.hit_objects, expected_objects, strict=True):
        index = expected["index"]

        if TYPE_NAMES[type(obj)] != expected["type"]:
            failures.append(f"{name}[{index}]: тип {TYPE_NAMES[type(obj)]}, ожидался {expected['type']}")
            continue

        values = {
            "start_time": obj.start_time,
            "x": obj.position.x,
            "y": obj.position.y,
            "stacked_x": obj.stacked_position.x,
            "stacked_y": obj.stacked_position.y,
            "stack_height": obj.stack_height,
            "radius": obj.radius,
            "scale": obj.scale,
            "time_preempt": obj.time_preempt,
        }
        for field in COMMON_FIELDS:
            if not values_equal(field, values[field], expected[field]):
                failures.append(f"{name}[{index}].{field}: {values[field]!r} != {expected[field]!r}")

        if not isinstance(obj, Slider):
            continue

        slider_values = {
            "repeat_count": obj.repeat_count,
            "span_count": obj.span_count,
            "span_duration": obj.span_duration,
            "duration": obj.duration,
            "end_time": obj.end_time,
            "velocity": obj.velocity,
            "tick_distance": obj.tick_distance,
            "path_distance": obj.path_distance,
        }
        for field in (*SLIDER_FIELDS, "path_distance"):
            if not values_equal(field, slider_values[field], expected[field]):
                failures.append(f"{name}[{index}].{field}: {slider_values[field]!r} != {expected[field]!r}")

        expected_nested = expected["nested"]
        if len(obj.nested) != len(expected_nested):
            failures.append(f"{name}[{index}]: вложенных {len(obj.nested)}, ожидалось {len(expected_nested)}")
            continue

        for nested, expected_n in zip(obj.nested, expected_nested, strict=True):
            nested_values = {
                "type": nested.type.value,
                "start_time": nested.start_time,
                "x": nested.position.x,
                "y": nested.position.y,
                "stacked_x": nested.stacked_position.x,
                "stacked_y": nested.stacked_position.y,
            }
            for field, value in nested_values.items():
                if not values_equal(field, value, expected_n[field]):
                    failures.append(f"{name}[{index}].nested.{field}: {value!r} != {expected_n[field]!r}")

    expected_combo = fixture["attributes"]["max_combo"]
    actual_combo = get_max_combo(beatmap)
    if actual_combo != expected_combo:
        failures.append(f"{name}: MaxCombo {actual_combo}, ожидалось {expected_combo}")

    return failures


def main() -> int:
    names = [b.name for b in (*REFERENCE_BEATMAPS, *REAL_WORLD_BEATMAPS)] + list(GEOMETRY_EDGE_CASES)
    all_failures: list[str] = []

    print(f"{'карта':<24} {'объектов':>9} {'MaxCombo':>9}  статус")
    print("-" * 60)

    for name in names:
        failures = check_beatmap(name)
        all_failures += failures

        fixture = load_fixture(name)
        count = len(fixture["beatmap"])
        combo = fixture["attributes"]["max_combo"]
        status = "OK" if not failures else f"{len(failures)} расхождений"
        print(f"{name:<24} {count:>9} {combo:>9}  {status}")

    print("-" * 60)

    if all_failures:
        print(f"РАСХОЖДЕНИЙ: {len(all_failures)}\n")
        for failure in all_failures[:20]:
            print(f"  {failure}")
        if len(all_failures) > 20:
            print(f"  ... и ещё {len(all_failures) - 20}")
        return 1

    print(f"Гейт фазы 1 пройден: {len(names)} карт, расхождений нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
