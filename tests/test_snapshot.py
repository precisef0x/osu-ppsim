"""Снапшот-гейт: единственный, которому не нужен ни оракул, ни фикстуры.

Остальные гейты сверяются с эталоном — с дампами оракула (фазы 1-4) или с живым
запуском osu-tools (фазы 5-6). Ни того, ни другого нет у человека, который просто
склонировал репозиторий: `fixtures/` в .gitignore, оракул тянет 856 МБ клонов
и .NET 8. Этот гейт закрывает дыру: карты из tests/edge_cases лежат в git, весят
килобайты, и снятые с них значения ловят регрессию без всякой обвязки.

ЧТО ЭТОТ ГЕЙТ ДОКАЗЫВАЕТ, А ЧТО НЕТ. Значения сняты с самого порта, а не с C#,
поэтому гейт проверяет НЕИЗМЕННОСТЬ, а не ПРАВИЛЬНОСТЬ: он не заметит ошибки,
которая была тут с самого начала. Правильность доказывают гейты с оракулом,
и заменить их этим нельзя. Смысл снапшота в том, что зафиксированные числа уже
сверены с C# бит в бит, — и теперь им не дадут уехать незамеченно.

ПОЧЕМУ ДОПУСК, А НЕ ТОЧНОЕ РАВЕНСТВО. Расчёт идёт через exp, cos, atan2, log,
pow, erf, acos — их libm не обязан считать одинаково на разных платформах,
расхождение в последнем бите между macOS и glibc законно. Снапшот снимается
на одной машине, а проверяется на всех, поэтому точное равенство сделало бы
гейт мигающим. SNAPSHOT_PRECISION на четыре порядка грубее этого шума и на много
порядков тоньше любой настоящей регрессии: все ошибки, которые ловились при
портировании, — зажатия, стакинг, float32 вместо double — сдвигали значения
в разы, а не в последнем знаке.

Переснять после осознанного изменения расчёта (ребаланс, поднятие Version):
    python3 tests/test_snapshot.py --update
и обязательно глазами просмотреть диff — в нём видно, что именно поехало.

Запуск: python3 tests/test_snapshot.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from osu_ppsim import SUPPORTED_DIFFCALC_VERSION, BeatmapParseError, Simulator  # noqa: E402

BEATMAPS = Path(__file__).resolve().parent / "edge_cases"
SNAPSHOT = Path(__file__).resolve().parent / "edge_cases" / "expected.json"

#: Допуск сверки. Крупно относительно расхождений libm (~1e-16) и мелко
#: относительно любой содержательной регрессии — см. шапку модуля.
SNAPSHOT_PRECISION = 1e-12

#: Наборы модов покрывают все поддерживаемые: NM, NF, DT, NC, HT, DC, HR, EZ,
#: HD, FL, CL. Точности разные, чтобы под проверку попал и круг умножения-деления
#: в accuracy. Наборы с CL идут по классической механике счёта.
COMBINATIONS: tuple[tuple[str, float], ...] = (
    ("NM", 100.0),
    ("NM", 98.0),
    ("HDDT", 95.0),
    ("HR", 99.0),
    ("EZHT", 97.0),
    ("FL", 100.0),
    ("NFNC", 96.0),
    ("HDDC", 100.0),
    ("CL", 99.0),
    ("CLHDDT", 95.0),
    ("CLEZHT", 97.0),
    ("CLFL", 100.0),
)

#: Обе трактовки accuracy: как её видит игрок в lazer и «сырая» по кругам,
#: в которой значение принимает osu-tools. Пути разные, ломаются независимо.
#: Под CL они совпадают по построению, и снапшот заодно держит это свойство.
RAW_ACCURACY = (False, True)


def beatmap_names() -> list[str]:
    """Все карты краевых случаев, в стабильном порядке."""
    return sorted(path.stem for path in BEATMAPS.glob("*.osu"))


def collect() -> tuple[list[dict], list[str]]:
    """Считает все комбинации. Возвращает записи и список пропущенных с причиной.

    Пропуски не проглатываются: карта, переставшая считаться под каким-то модом, —
    это ровно та регрессия, которую гейт обязан показать, а не молча недосчитать.
    """
    records: list[dict] = []
    skipped: list[str] = []

    for name in beatmap_names():
        path = BEATMAPS / f"{name}.osu"

        for mods, accuracy in COMBINATIONS:
            try:
                simulator = Simulator(path, mods=mods)
            except BeatmapParseError as exc:
                skipped.append(f"{name} [{mods}]: карта не разобралась: {exc}")
                continue

            for raw in RAW_ACCURACY:
                try:
                    result = simulator.pp(accuracy, raw_accuracy=raw)
                except (ValueError, BeatmapParseError) as exc:
                    skipped.append(f"{name} [{mods}] {accuracy}% raw={raw}: {exc}")
                    continue

                records.append(
                    {
                        "beatmap": name,
                        "mods": mods,
                        "accuracy": accuracy,
                        "raw_accuracy": raw,
                        "star_rating": result.difficulty.star_rating,
                        "max_combo": result.difficulty.max_combo,
                        "actual_accuracy": result.accuracy,
                        "pp": result.pp,
                    }
                )

    return records, skipped


#: Поля, которые сверяются как числа с плавающей точкой; max_combo целый и сверяется точно.
FLOAT_FIELDS = ("star_rating", "actual_accuracy", "pp")


def close_enough(actual: float, wanted: float) -> bool:
    """Сверка по относительному допуску, с абсолютным запасом около нуля."""
    if actual == wanted:
        return True
    if actual != actual or wanted != wanted:  # NaN: nan == nan ложно, но снапшот его допускает
        return actual != actual and wanted != wanted
    return abs(actual - wanted) <= SNAPSHOT_PRECISION * max(abs(wanted), 1.0)


def key_of(record: dict) -> tuple:
    return (record["beatmap"], record["mods"], record["accuracy"], record["raw_accuracy"])


def label_of(record: dict) -> str:
    raw = "raw" if record["raw_accuracy"] else "lazer"
    return f"{record['beatmap']} [{record['mods']}] {record['accuracy']:g}% {raw}"


def update() -> int:
    records, skipped = collect()

    payload = {
        "//": (
            "Снято с самого порта: гейт на регрессию, не на правильность. "
            "Пересъёмка: python3 tests/test_snapshot.py --update"
        ),
        "calculator_version": SUPPORTED_DIFFCALC_VERSION,
        "precision": SNAPSHOT_PRECISION,
        "cases": records,
    }

    SNAPSHOT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Снапшот записан: {SNAPSHOT.relative_to(ROOT)}")
    print(f"  карт: {len(beatmap_names())}, комбинаций: {len(records)}")

    if skipped:
        print(f"  не сосчиталось: {len(skipped)}")
        for note in skipped:
            print(f"    {note}")

    return 0


def main() -> int:
    if not SNAPSHOT.exists():
        print(f"нет снапшота {SNAPSHOT}; снять: python3 tests/test_snapshot.py --update", file=sys.stderr)
        return 1

    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    expected = {key_of(record): record for record in payload["cases"]}

    records, skipped = collect()
    actual = {key_of(record): record for record in records}

    failures: list[str] = []

    for note in skipped:
        failures.append(f"не сосчиталось: {note}")

    for key in expected.keys() - actual.keys():
        failures.append(f"пропала комбинация: {label_of(expected[key])}")

    for key in actual.keys() - expected.keys():
        failures.append(f"комбинация вне снапшота: {label_of(actual[key])} — пересними --update")

    for key in expected.keys() & actual.keys():
        want, got = expected[key], actual[key]

        if got["max_combo"] != want["max_combo"]:
            failures.append(f"{label_of(want)}.max_combo: {got['max_combo']} != {want['max_combo']}")

        for field in FLOAT_FIELDS:
            if not close_enough(got[field], want[field]):
                delta = abs(got[field] - want[field])
                failures.append(f"{label_of(want)}.{field}: {got[field]!r} != {want[field]!r}  (на {delta:.3e})")

    print(f"карт: {len(beatmap_names())}, комбинаций сверено: {len(expected.keys() & actual.keys())}")

    if failures:
        print(f"\nРАСХОЖДЕНИЙ: {len(failures)}\n")
        for failure in failures:
            print(f"  {failure}")
        print("\nЕсли расчёт менялся осознанно — пересними: python3 tests/test_snapshot.py --update")
        return 1

    print(f"Снапшот-гейт пройден: расхождений нет (допуск {SNAPSHOT_PRECISION:g}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(update() if "--update" in sys.argv[1:] else main())
