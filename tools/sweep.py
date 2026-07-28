#!/usr/bin/env python3
"""Массовая сверка порта с оракулом на случайной выборке реальных карт.

Дев-инструмент. Гейты проверяют аккуратно подобранные карты, а этот скрипт —
случайные из большого корпуса .osu; семь ошибок из девятнадцати нашёл именно он.
Парный к нему tools/corpus_scan.py ищет не расхождения в числах, а отказы
и падения, и проходит корпус целиком.

Выборка воспроизводима: одно зерно даёт один и тот же набор карт, смена зерна —
новые карты.

    python3 tools/sweep.py ~/Downloads/osu_files --count 200
    python3 tools/sweep.py ~/Downloads/osu_files --count 500 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from oracle import OracleError, simulate  # noqa: E402
from osu_ppsim import BeatmapParseError, Simulator, decode_beatmap  # noqa: E402

#: Наборы модов, из которых выбирается по одному на карту. Половина с CL:
#: классическая механика счёта ветвится отдельно от лазерной, и проверять её
#: надо на том же реальном корпусе, а не только на подобранных картах.
LAZER_MOD_SETS = ("", "HD", "DT", "HR", "EZ", "HDDT", "HDHR", "HRDT", "FL", "HT")
MOD_SETS = LAZER_MOD_SETS + tuple("CL" + mods for mods in LAZER_MOD_SETS)

ACCURACIES = (100.0, 98.0, 95.0)

#: Порог, с которого расхождение считается настоящим. Один-два младших бита —
#: известный остаток отклонения Reading, он на pp при FC не влияет.
TOLERANCE = 1e-12


def split_mods(text: str) -> tuple[str, ...]:
    return tuple(text[i : i + 2] for i in range(0, len(text), 2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpus", type=Path, help="каталог с .osu-файлами")
    parser.add_argument("--count", type=int, default=200, help="сколько карт проверить (по умолчанию 200)")
    parser.add_argument("--seed", type=int, default=555000111, help="зерно выборки")
    args = parser.parse_args()

    all_files = sorted(args.corpus.glob("*.osu"))
    if not all_files:
        print(f"в {args.corpus} нет .osu-файлов", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    # Берём с запасом: на std-карты приходится примерно половина корпуса.
    sample = rng.sample(all_files, min(args.count * 4, len(all_files)))
    print(f"в корпусе: {len(all_files)} файлов, зерно {args.seed}", flush=True)

    checked_maps = 0
    checked_combos = 0
    skipped = {"не std": 0, "пустая": 0, "не разобралась": 0, "оракул отказал": 0}
    mismatches: list[dict] = []
    worst = 0.0

    for path in sample:
        if checked_maps >= args.count:
            break

        try:
            beatmap = decode_beatmap(path)
        except BeatmapParseError as exc:
            if "osu!standard" in str(exc):
                skipped["не std"] += 1
                continue

            # Отказ разбора — расхождение, если оракул карту принимает. Раньше
            # он молча шёл в «не разобралась», и ровно эта дыра спрятала находку
            # с пропуском битых строк: карта 2052199 попала в пропуски вместо
            # несовпадений.
            try:
                simulate(path, ACCURACIES[0], split_mods(rng.choice(MOD_SETS)))
            except OracleError:
                skipped["не разобралась"] += 1
            else:
                mismatches.append(
                    {"map": path.name, "error": f"порт не разобрал карту, а оракул разобрал: {exc}"}
                )
            continue

        if len(beatmap.hit_objects) < 2:
            skipped["пустая"] += 1
            continue

        mods = rng.choice(MOD_SETS)

        try:
            simulator = Simulator(path, mods)
        except Exception:  # noqa: BLE001 — падение порта тоже расхождение
            mismatches.append({"map": path.name, "mods": mods or "NM", "error": traceback.format_exc().splitlines()[-1]})
            continue

        map_ok = True
        for accuracy in ACCURACIES:
            try:
                expected = simulate(path, accuracy, split_mods(mods))
            except OracleError:
                skipped["оракул отказал"] += 1
                map_ok = False
                break

            expected_pp = expected["performance_attributes"]["pp"]
            expected_sr = expected["difficulty_attributes"]["star_rating"]

            # raw_accuracy: osu-tools принимает точность по кругам.
            # Падение здесь ловится по той же причине, что и на построении:
            # необработанное исключение — такое же расхождение с эталоном, и
            # прогон на тысячах карт не должен из-за него обрываться целиком.
            try:
                actual_pp = simulator.pp(accuracy, raw_accuracy=True).pp
            except Exception:  # noqa: BLE001
                mismatches.append(
                    {
                        "map": path.name,
                        "mods": mods or "NM",
                        "accuracy": accuracy,
                        "error": traceback.format_exc().splitlines()[-1],
                    }
                )
                continue

            checked_combos += 1

            pp_deviation = abs(actual_pp - expected_pp) / expected_pp if expected_pp else abs(actual_pp - expected_pp)
            sr_deviation = abs(simulator.star_rating - expected_sr) / expected_sr if expected_sr else 0.0
            worst = max(worst, pp_deviation, sr_deviation)

            if pp_deviation >= TOLERANCE or sr_deviation >= TOLERANCE:
                mismatches.append(
                    {
                        "map": path.name,
                        "mods": mods or "NM",
                        "accuracy": accuracy,
                        "objects": len(beatmap.hit_objects),
                        "pp": [actual_pp, expected_pp, pp_deviation],
                        "star_rating": [simulator.star_rating, expected_sr, sr_deviation],
                    }
                )

        if map_ok:
            checked_maps += 1
            if checked_maps % 25 == 0:
                print(
                    f"  {checked_maps} карт, {checked_combos} комбинаций, "
                    f"расхождений {len(mismatches)}, худшее {worst:.2e}",
                    flush=True,
                )

    print()
    print(f"проверено карт: {checked_maps}, комбинаций: {checked_combos}")
    print(f"пропущено: {skipped}")
    print(f"расхождений: {len(mismatches)}, худшее относительное отклонение: {worst:.3e}")

    if mismatches:
        print()
        for mismatch in mismatches[:10]:
            print(f"  {json.dumps(mismatch, ensure_ascii=False)}")
        if len(mismatches) > 10:
            print(f"  ... и ещё {len(mismatches) - 10}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
