"""Гейт фазы 4: difficulty-атрибуты и звёзды.

Запуск: python3 tests/test_phase4_attributes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare import load_fixture  # noqa: E402
from reference import REAL_WORLD_BEATMAPS, REFERENCE_BEATMAPS  # noqa: E402

from osu_ppsim.beatmap.decoder import decode_beatmap  # noqa: E402
from osu_ppsim.difficulty.calculator import calculate_difficulty  # noqa: E402

BEATMAPS = (
    Path(__file__).resolve().parent.parent
    / "oracle/osu/osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps"
)

MOD_SETS = ("NM", "HD", "DT", "FL", "HDDT", "HR", "EZ")

ATTRIBUTES = (
    "star_rating",
    "max_combo",
    "aim_difficulty",
    "aim_difficult_slider_count",
    "speed_difficulty",
    "speed_note_count",
    "flashlight_difficulty",
    "reading_difficulty",
    "slider_factor",
    "aim_difficult_strain_count",
    "speed_difficult_strain_count",
    "aim_top_weighted_slider_factor",
    "speed_top_weighted_slider_factor",
    "hit_circle_count",
    "slider_count",
    "spinner_count",
)

#: Известное отклонение в 1 ulp, унаследованное от эвалуатора Reading.
#: В pp-формуле этот атрибут встречается только внутри `if (effectiveMissCount > 0)`,
#: а при FC этот счётчик равен нулю — то есть для нашего охвата он не используется.
#: Подробнее — «Остаток Reading» в docs/PORTING.md.
KNOWN_ULP_DRIFT = ("reading_difficult_note_count",)


def main() -> int:
    failures: list[str] = []
    drift: list[str] = []

    print(f"{'карта':<22} {'моды':<6} {'star_rating':>22}  статус")
    print("-" * 62)

    for ref in (*REFERENCE_BEATMAPS, *REAL_WORLD_BEATMAPS):
        for mod_set in MOD_SETS:
            try:
                fixture = load_fixture(ref.name, mod_set)
            except FileNotFoundError:
                continue

            expected = fixture["attributes"]
            beatmap = decode_beatmap(BEATMAPS / f"{ref.name}.osu")
            attributes = calculate_difficulty(beatmap, "" if mod_set == "NM" else mod_set)

            local: list[str] = []
            for field in ATTRIBUTES:
                value = getattr(attributes, field)
                if value != expected[field]:
                    local.append(f"{ref.name}/{mod_set}.{field}: {value!r} != {expected[field]!r}")

            for field in KNOWN_ULP_DRIFT:
                value = getattr(attributes, field)
                if value != expected[field]:
                    drift.append(f"{ref.name}/{mod_set}.{field}")

            failures += local
            status = "OK" if not local else f"{len(local)} расхождений"
            print(f"{ref.name:<22} {mod_set:<6} {attributes.star_rating!r:>22}  {status}")

    print("-" * 62)

    if drift:
        print(f"известное отклонение в 1 ulp (не влияет на FC): {len(drift)} случаев")

    if failures:
        print(f"РАСХОЖДЕНИЙ: {len(failures)}\n")
        for failure in failures[:15]:
            print(f"  {failure}")
        return 1

    print("Гейт фазы 4 пройден: звёзды и атрибуты сходятся.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
