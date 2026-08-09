"""Гейт фазы 5: pp за FC.

Единственный гейт, который сверяется не с дампом, а с живым запуском
`osu-tools simulate`: именно он показывает число, которое увидит игрок.
Требует собранного оракула.

Запуск: python3 tests/test_phase5_performance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from reference import REAL_WORLD_BEATMAPS, REFERENCE_BEATMAPS  # noqa: E402

from oracle import OracleError, simulate  # noqa: E402
from osu_ppsim import pp_for_accuracy  # noqa: E402

BEATMAPS = ROOT / "oracle/osu/osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps"

#: Наборы без CL считаются по лазерной механике, с CL — по классической.
#: Классических наборов меньше: CL ветвит только accuracy-компонент, поэтому
#: их задача — покрыть его во всех сочетаниях с rate- и difficulty-модами,
#: а не повторить всю лазерную сетку.
LAZER_MOD_SETS = ("", "HD", "DT", "HDDT", "FL", "HDFL", "HR", "HRDT", "EZ", "HT", "NF")
CLASSIC_MOD_SETS = ("CL", "CLHD", "CLDT", "CLHDDT", "CLFL", "CLHR", "CLHRDT", "CLEZ")
MOD_SETS = LAZER_MOD_SETS + CLASSIC_MOD_SETS
ACCURACIES = (100.0, 99.5, 99.0, 98.0, 95.0, 90.0)

#: HRDT добавлен намеренно: на diffcalc-test под ним эвалуатор Reading уходит
#: на 1 ulp, и это доходит до pp (~4e-16). Обходить такое сочетание значило бы
#: печатать «0.000e+00» и прятать известное отклонение вместо того, чтобы
#: держать его на виду и под допуском.
#:
#: pp совпадает с оракулом побитово, поэтому допуск нужен лишь как страховка
#: от неожиданностей на новых картах.
TOLERANCE = 1e-12


def split_mods(text: str) -> tuple[str, ...]:
    return tuple(text[i : i + 2] for i in range(0, len(text), 2))


def main() -> int:
    failures: list[str] = []
    worst = 0.0
    checked = 0

    print(f"{'карта':<22} {'моды':<6} {'комбинаций':>11} {'худшее отклонение':>19}")
    print("-" * 62)

    for ref in (*REFERENCE_BEATMAPS, *REAL_WORLD_BEATMAPS):
        beatmap_path = BEATMAPS / f"{ref.name}.osu"

        for mod_set in MOD_SETS:
            local_worst = 0.0
            local_count = 0

            for accuracy in ACCURACIES:
                try:
                    expected = simulate(ref.name, accuracy, split_mods(mod_set))
                except OracleError as exc:
                    print(f"ОРАКУЛ НЕДОСТУПЕН: {exc}", file=sys.stderr)
                    return 2

                expected_pp = expected["performance_attributes"]["pp"]
                # raw_accuracy: osu-tools принимает точность по кругам,
                # без хвостов и тиков.
                actual_pp = pp_for_accuracy(beatmap_path, accuracy, mod_set, raw_accuracy=True).pp

                relative = abs(actual_pp - expected_pp) / expected_pp if expected_pp else abs(actual_pp - expected_pp)
                local_worst = max(local_worst, relative)
                worst = max(worst, relative)
                checked += 1
                local_count += 1

                if relative >= TOLERANCE:
                    failures.append(
                        f"{ref.name}/{mod_set or 'NM'}/{accuracy}%: {actual_pp!r} != {expected_pp!r} "
                        f"(отн. {relative:.2e})"
                    )

            print(f"{ref.name:<22} {mod_set or 'NM':<6} {local_count:>11} {local_worst:>19.2e}")

    print("-" * 62)
    print(f"проверено комбинаций: {checked}, худшее отклонение: {worst:.3e}")

    if failures:
        print(f"РАСХОЖДЕНИЙ: {len(failures)}\n")
        for failure in failures[:15]:
            print(f"  {failure}")
        return 1

    print("Гейт фазы 5 пройден: pp совпадает с оракулом.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
