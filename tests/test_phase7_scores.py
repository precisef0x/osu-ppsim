"""Гейт фазы 7: pp за произвольный скор.

Проверяет то, чего нет в фазе 5: промахи, потерянное комбо, недодержанные
хвосты и пропущенные тики. Сверяются не только итоговые pp, но и все
промежуточные величины, которые оракул печатает отдельно, — упавший слой
называет себя сам.

Раскладка читается ИЗ ответа оракула и скармливается расчёту. Выводить её
самостоятельно нельзя: osu-tools сам раскладывает accuracy в попадания,
и два независимых вывода сравнивали бы два разных скора.

Гейт считает попадания в каждую ветку и падает, если хоть одна не сработала:
проверка, не заходившая в код, ничего о нём не говорит.

Запуск: python3 tests/test_phase7_scores.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from oracle import BEATMAPS, OracleError, simulate  # noqa: E402
from osu_ppsim import HitCounts, Score, Simulator  # noqa: E402

#: Карты ppy лежат в ресурсах оракула, наши собственные пробы — в edge_cases.
EDGE_CASES = Path(__file__).resolve().parent / "edge_cases"

MAPS = (
    "801165",
    "diffcalc-test",
    "zero-length-sliders",
    "very-fast-slider",
    "nan-slider",
    # Брейки длиннее пролёта карты меньше чем на секунду: drain_length уходит
    # в минус, и целочисленное деление обязано усечь к нулю, а не вниз.
    "legacy-drain-underflow",
)


def map_path(name: str) -> Path:
    own = EDGE_CASES / f"{name}.osu"
    return own if own.exists() else BEATMAPS / f"{name}.osu"

MOD_SETS = ((), ("HD", "DT"), ("CL",), ("FL",), ("FL", "CL"), ("HR",), ("EZ", "NF"), ("HD", "FL", "CL"))

#: Параметры скора для osu-tools. Пустой словарь — это FC.
CASES = (
    {},
    {"misses": 1},
    {"misses": 1, "combo": 1},
    {"misses": 3, "combo": 200},
    {"misses": 5, "combo": 1800},
    {"misses": 5, "combo": 1800, "large_tick_misses": 3, "slider_tail_misses": 7},
    {"goods": 40, "mehs": 3, "misses": 2, "combo": 1500},
    {"goods": 100, "mehs": 20, "misses": 15, "combo": 300, "large_tick_misses": 40, "slider_tail_misses": 90},
    {"goods": 5, "combo": 500},
    {"slider_tail_misses": 1},
    {"large_tick_misses": 1},
    {"combo": 1},
    {"combo": 2},
    {"misses": 1000},
)

#: Суммы очков ScoreV1 для классических наборов модов. Перебираются в дополнение
#: к каждому случаю выше: оценка промахов по очкам включается только вместе с CL
#: и меняет ответ сильнее, чем любой другой вход.
LEGACY_SCORES = (500_000, 8_000_000, 45_000_000)

#: pp считается отдельно: у нас это Result.pp, у оракула — ключ верхнего уровня.
FIELDS = (
    "combo_based_estimated_miss_count",
    "score_based_estimated_miss_count",
    "effective_miss_count",
    "aim_estimated_slider_breaks",
    "speed_estimated_slider_breaks",
    "aim",
    "speed",
    "accuracy",
    "reading",
    "flashlight",
    "pp",
)

#: Ветки, которые матрица обязана задеть. Ноль в любой строке означает, что
#: гейт проверяет меньше, чем думает.
REQUIRED_BRANCHES = (
    "classic",
    "комбо ниже максимума",
    "промахи > 0",
    "слайдербрейки аима > 0",
    "слайдербрейки скорости > 0",
    "флешлайт > 0",
    "штраф флешлайта",
    "нулевые трудные секции",
    "оценка по очкам",
)


def main() -> int:
    failures: list[str] = []
    checked = 0
    branches: Counter[str] = Counter()

    for name in MAPS:
        path = map_path(name)

        for mods in MOD_SETS:
            classic = "CL" in mods
            simulator = Simulator(path, list(mods) or None)

            # Классическому скору дополнительно перебираются суммы очков:
            # с ними включается совсем другая ветка оценки промахов.
            variants = [(case, None) for case in CASES]
            if classic:
                variants += [(case, total) for case in CASES for total in LEGACY_SCORES]

            for case, legacy_total in variants:
                options = dict(case)
                if classic:
                    # Классическая механика хвостов и тиков не знает вовсе.
                    options.pop("large_tick_misses", None)
                    options.pop("slider_tail_misses", None)
                if legacy_total is not None:
                    options["legacy_total_score"] = legacy_total

                try:
                    expected = simulate(path, 97.0, mods, **options)
                except OracleError as exc:
                    print(f"ОРАКУЛ НЕДОСТУПЕН: {exc}", file=sys.stderr)
                    return 2

                statistics = expected["score"]["statistics"]
                wanted = expected["performance_attributes"]

                score = Score(
                    counts=HitCounts(
                        great=statistics["great"],
                        ok=statistics["ok"],
                        meh=statistics["meh"],
                        miss=statistics["miss"],
                    ),
                    max_combo=expected["score"]["combo"],
                    slider_tail_hits=statistics.get("slider_tail_hit"),
                    large_tick_misses=statistics.get("large_tick_miss", 0),
                    legacy_total_score=legacy_total,
                )
                result = simulator.score(score)
                checked += 1

                if wanted["effective_miss_count"] > 0:
                    branches["промахи > 0"] += 1
                if wanted["aim_estimated_slider_breaks"] > 0:
                    branches["слайдербрейки аима > 0"] += 1
                if wanted["speed_estimated_slider_breaks"] > 0:
                    branches["слайдербрейки скорости > 0"] += 1
                if wanted["flashlight"] > 0:
                    branches["флешлайт > 0"] += 1
                    if wanted["effective_miss_count"] > 0:
                        branches["штраф флешлайта"] += 1
                if classic:
                    branches["classic"] += 1
                if legacy_total is not None:
                    branches["оценка по очкам"] += 1
                if expected["score"]["combo"] < simulator.max_combo:
                    branches["комбо ниже максимума"] += 1
                if wanted["effective_miss_count"] > 0 and min(
                    simulator.difficulty.aim_difficult_strain_count,
                    simulator.difficulty.speed_difficult_strain_count,
                    simulator.difficulty.reading_difficult_note_count,
                ) <= 1:
                    # log(1) = 0, то есть штраф за промахи делит на ноль:
                    # в C# это бесконечность и нулевой множитель, в Python
                    # без c_div было бы падение.
                    branches["нулевые трудные секции"] += 1

                for field in FIELDS:
                    actual = result.pp if field == "pp" else getattr(result.performance, field)
                    # У лазерных скоров оценка по очкам не применяется, и обе
                    # стороны отдают None — сравнение на равенство это покрывает.
                    if actual != wanted[field]:
                        label = f"{name} {''.join(mods) or 'NM'} {case} score={legacy_total}"
                        failures.append(f"{label} {field}: {actual!r} != {wanted[field]!r}")

    print(f"проверено скоров: {checked}, величин: {checked * len(FIELDS)}")
    print("покрытие веток:")
    for branch in REQUIRED_BRANCHES:
        print(f"  {branch:<28} {branches[branch]}")

    missing = [branch for branch in REQUIRED_BRANCHES if not branches[branch]]
    if missing:
        print(f"\nВЕТКИ НЕ ЗАДЕТЫ: {', '.join(missing)}")
        print("Матрица проверяет меньше, чем заявляет — её надо расширить.")
        return 1

    if failures:
        print(f"\nРАСХОЖДЕНИЙ: {len(failures)}\n")
        for failure in failures[:20]:
            print(f"  {failure}")
        if len(failures) > 20:
            print(f"  ... и ещё {len(failures) - 20}")
        return 1

    print("\nГейт фазы 7 пройден: pp произвольного скора совпадает с оракулом.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
