#!/usr/bin/env python3
"""Драйвер оракула: запускает osu-tools и отдаёт разобранный JSON.

Дев-инструмент. В библиотеку не входит и ею не импортируется.
Версии C#-исходников зафиксированы в oracle/PINNED.md.

Использование:
    python3 tools/oracle.py check          # сверить пять эталонных карт с литералами ppy
    python3 tools/oracle.py sim MAP [-a A] [-m MOD ...]
    python3 tools/oracle.py dump MAP [-m MOD ...]
    python3 tools/oracle.py fixtures       # сгенерировать эталонные дампы
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORACLE = ROOT / "oracle"
BEATMAPS = ORACLE / "osu/osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps"
DLL = ORACLE / "osu-tools/PerformanceCalculator/bin/Release/net8.0/PerformanceCalculator.dll"
DUMP_DLL = ORACLE / "dump/bin/Release/net8.0/Dump.dll"

FIXTURES = ROOT / "fixtures"
FIXTURES_RAW = FIXTURES / "raw"

#: Наборы модов для фикстур. Ограничены охватом первого этапа: NM + rate-моды + HD/FL.
FIXTURE_MOD_SETS: tuple[tuple[str, ...], ...] = ((), ("HD",), ("DT",), ("FL",), ("HD", "DT"), ("HR",), ("EZ",))


class OracleError(RuntimeError):
    pass


def _resolve_beatmap(beatmap: str | Path) -> Path:
    path = Path(beatmap)
    if path.exists():
        return path
    candidate = BEATMAPS / f"{beatmap}.osu"
    if not candidate.exists():
        raise OracleError(f"карта не найдена: {beatmap}")
    return candidate


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise OracleError(f"{Path(cmd[1]).name} упал ({proc.returncode}):\n{proc.stderr.strip()}")
    return proc.stdout


def simulate(
    beatmap: str | Path,
    accuracy: float = 100.0,
    mods: tuple[str, ...] = (),
    *,
    misses: int | None = None,
    goods: int | None = None,
    mehs: int | None = None,
    combo: int | None = None,
    large_tick_misses: int | None = None,
    slider_tail_misses: int | None = None,
    legacy_total_score: int | None = None,
) -> dict:
    """Считает скор и возвращает JSON целиком.

    Без именованных параметров описывает FC: у osu-tools --combo по умолчанию
    равно максимуму карты, а --misses нулю. Каждый заданный параметр добавляет
    ключ в командную строку и только тогда меняет поведение — поэтому FC-вызовы
    остаются байт в байт теми же, что и до появления этих параметров.

    ВНИМАНИЕ: goods и mehs у osu-tools ПЕРЕОПРЕДЕЛЯЮТ accuracy, а не уточняют её.
    Раскладку, с которой считал оракул, надо читать из score.statistics ответа,
    а не выводить самостоятельно, иначе сверяются два разных скора.
    """
    if not DLL.exists():
        raise OracleError(
            f"не собран PerformanceCalculator: {DLL}\n"
            f"собрать: cd {ORACLE}/osu-tools && dotnet build "
            f"PerformanceCalculator/PerformanceCalculator.csproj -c Release"
        )

    cmd = ["dotnet", str(DLL), "simulate", "osu", str(_resolve_beatmap(beatmap)), "-a", str(accuracy), "-j"]
    for mod in mods:
        cmd += ["-m", mod]

    for option, value in (
        ("--misses", misses),
        ("--goods", goods),
        ("--mehs", mehs),
        ("--combo", combo),
        ("--large-tick-misses", large_tick_misses),
        ("--slider-tail-misses", slider_tail_misses),
        ("--legacy-total-score", legacy_total_score),
    ):
        if value is not None:
            cmd += [option, str(value)]

    out = _run(cmd)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise OracleError(f"не разобрался вывод osu-tools:\n{out[:500]}") from exc


def dump(beatmap: str | Path, mods: tuple[str, ...] = ()) -> dict:
    """Выгружает промежуточное состояние расчёта: карту, difficulty-объекты, скиллы, атрибуты."""
    if not DUMP_DLL.exists():
        raise OracleError(
            f"не собран Dump: {DUMP_DLL}\n"
            f"собрать: cd {ORACLE} && dotnet build dump/Dump.csproj -c Release"
        )

    cmd = ["dotnet", str(DUMP_DLL), str(_resolve_beatmap(beatmap))]
    for mod in mods:
        cmd += ["-m", mod]

    out = _run(cmd)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise OracleError(f"не разобрался вывод Dump:\n{out[:500]}") from exc


def cmd_check() -> int:
    """Сверяет оракул с литералами из OsuDifficultyCalculatorTest.cs."""
    sys.path.insert(0, str(ROOT / "tests"))
    from reference import REFERENCE_BEATMAPS  # noqa: PLC0415

    # Допуск самих ppy. Заметно свободнее, чем нам нужно для порта, — см. вывод ниже.
    ppy_precision = 1e-5
    failures = 0

    print(f"{'карта':<22} {'моды':<5} {'оракул':<22} {'литерал ppy':<22} {'delta':>11}  combo")
    print("-" * 96)

    for ref in REFERENCE_BEATMAPS:
        for mod_key, expected in ref.stars.items():
            mods = (mod_key,) if mod_key else ()
            result = simulate(ref.name, 100.0, mods)
            attrs = result["difficulty_attributes"]
            actual = attrs["star_rating"]
            combo = attrs["max_combo"]

            delta = actual - expected
            star_ok = abs(delta) < ppy_precision
            combo_ok = combo == ref.max_combo
            if not (star_ok and combo_ok):
                failures += 1

            flag = "" if (star_ok and combo_ok) else "   <-- РАСХОЖДЕНИЕ"
            if not combo_ok:
                flag += f" (combo ожидалось {ref.max_combo})"
            print(
                f"{ref.name:<22} {mod_key or 'NM':<5} {actual!r:<22} {expected!r:<22} "
                f"{delta:>+11.2e}  {combo}{flag}"
            )

    print("-" * 96)
    if failures:
        print(f"РАСХОЖДЕНИЙ: {failures}")
        return 1

    print(f"Все совпали в пределах допуска ppy ({ppy_precision:g}).")
    print()
    print("Важно: литералы ppy держатся на свободном допуске 1e-5 и отстают от master")
    print("(последний раз правились 15.07.2026, коммиты в диффкалк были 25.07 и 27.07).")
    print("Точной целью порта служит полноточный JSON оракула, а не эти литералы.")
    return 0


def cmd_fixtures() -> int:
    """Генерирует эталонные дампы для сверки порта.

    Сырые дампы большие и полностью воспроизводимы с запиненных исходников,
    поэтому в git не попадают (см. .gitignore). Коммитится только манифест —
    по нему видно, если фикстуры разъехались с оракулом.
    """
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    from reference import GEOMETRY_EDGE_CASES, REAL_WORLD_BEATMAPS, REFERENCE_BEATMAPS  # noqa: PLC0415

    from osu_ppsim import SUPPORTED_DIFFCALC_VERSION  # noqa: PLC0415

    FIXTURES_RAW.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[str, tuple[str, ...]]] = []
    for ref in (*REFERENCE_BEATMAPS, *REAL_WORLD_BEATMAPS):
        jobs += [(ref.name, mods) for mods in FIXTURE_MOD_SETS]
    # Краевые случаи геометрии нужны только для фазы 1 — там моды не важны.
    jobs += [(name, ()) for name in GEOMETRY_EDGE_CASES]

    manifest = []
    print(f"{'карта':<24} {'моды':<7} {'объектов':>9} {'SR':>20} {'размер':>10}")
    print("-" * 76)

    for name, mods in jobs:
        data = dump(name, mods)
        mod_key = "".join(mods) or "NM"
        out_path = FIXTURES_RAW / f"{name}__{mod_key}.json"
        text = json.dumps(data, indent=1)
        out_path.write_text(text)

        attrs = data["attributes"]
        entry = {
            "beatmap": name,
            "mods": list(mods),
            "file": out_path.relative_to(FIXTURES).as_posix(),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "hit_objects": len(data["beatmap"]),
            "difficulty_objects": len(data["difficulty_objects"]),
            "skills": [s["name"] for s in data["skills"]],
            "star_rating": attrs["star_rating"],
            "max_combo": attrs["max_combo"],
        }
        manifest.append(entry)
        print(
            f"{name:<24} {mod_key:<7} {entry['hit_objects']:>9} "
            f"{attrs['star_rating']:>20.15f} {len(text) / 1024:>9.0f}K"
        )

    manifest_path = FIXTURES / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "diffcalc_version": SUPPORTED_DIFFCALC_VERSION,
                "osu_commit": "52461f1b82672f019867c2078b357d3cb5d1f130",
                "osu_tools_commit": "f8bc6aa72b3b2f00c7bbbce3e5caa566919afa26",
                "fixtures": manifest,
            },
            indent=2,
        )
        + "\n"
    )

    total = sum(f.stat().st_size for f in FIXTURES_RAW.glob("*.json"))
    print("-" * 76)
    print(f"{len(manifest)} фикстур, {total / 1024 / 1024:.1f} МБ в {FIXTURES_RAW.relative_to(ROOT)}/ (не в git)")
    print(f"манифест: {manifest_path.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="сверить эталонные карты с литералами ppy")
    sub.add_parser("fixtures", help="сгенерировать эталонные дампы")

    p_sim = sub.add_parser("sim", help="посчитать pp одной карты")
    p_sim.add_argument("map", help="путь к .osu или имя тестовой карты")
    p_sim.add_argument("-a", "--accuracy", type=float, default=100.0)
    p_sim.add_argument("-m", "--mod", action="append", default=[], dest="mods")

    p_dump = sub.add_parser("dump", help="выгрузить промежуточное состояние расчёта")
    p_dump.add_argument("map", help="путь к .osu или имя тестовой карты")
    p_dump.add_argument("-m", "--mod", action="append", default=[], dest="mods")

    args = parser.parse_args()

    try:
        if args.cmd == "check":
            return cmd_check()
        if args.cmd == "fixtures":
            return cmd_fixtures()
        if args.cmd == "dump":
            print(json.dumps(dump(args.map, tuple(args.mods)), indent=2))
            return 0
        print(json.dumps(simulate(args.map, args.accuracy, tuple(args.mods)), indent=2))
        return 0
    except OracleError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
