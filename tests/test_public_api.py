"""Гейт публичного API: контракт, который обещан в README.

Остальные гейты проверяют ЧИСЛА — что порт считает то же, что C#. Этот проверяет
ОБЁРТКУ вокруг них: что обещанные имена импортируются, что «0.98» и «98» значат
одно и то же, что Simulator и pp_for_accuracy дают одинаковый ответ, что мусор
на входе поднимает исключение, а не считается молча.

Ломается такое от безобидных на вид правок в __init__.py, и ни один числовой
гейт этого не заметит: числа-то верные, до них просто больше не добраться
описанным в README способом.

Оракул и фикстуры не нужны: карты лежат в git.

Запуск: python3 tests/test_public_api.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import osu_ppsim  # noqa: E402
from osu_ppsim import (  # noqa: E402
    BeatmapParseError,
    Simulator,
    UnsupportedModError,
    pp_for_accuracy,
)

BEATMAPS = Path(__file__).resolve().parent / "edge_cases"

#: Карта из набора краевых случаев: без слайдеров, 21 объект. Для проверки
#: контракта годится любая, важно лишь, что она лежит в git.
MAP = BEATMAPS / "acc-rounding.osu"

#: Ниже реально достижимого минимума карты расклад попаданий вырождается,
#: и pp перестаёт расти вместе с точностью. Порог взят с запасом: замерено,
#: что монотонность держится от 0.3, здесь проверяется от 0.5.
MONOTONIC_FROM = 0.5


def check(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    # --- то, что обещано в __all__, обязано импортироваться
    for name in osu_ppsim.__all__:
        check(failures, hasattr(osu_ppsim, name), f"__all__ обещает {name}, но его нет в модуле")

    # --- пример из README считается и отдаёт заявленные поля
    result = pp_for_accuracy(MAP, accuracy=0.98, mods="HDDT")
    for attribute in ("pp", "difficulty", "hit_counts", "accuracy", "requested_accuracy", "mods"):
        check(failures, hasattr(result, attribute), f"у Result нет поля {attribute}")

    check(failures, result.pp > 0, f"pp неположительный: {result.pp}")
    check(failures, result.difficulty.star_rating > 0, "звёзды неположительные")
    check(failures, result.difficulty.max_combo > 0, "max_combo неположительный")
    check(failures, result.hit_counts.miss == 0, "FC не может содержать промахов")
    check(
        failures,
        result.hit_counts.total == 21,
        f"расклад попаданий не покрывает все объекты: {result.hit_counts.total} из 21",
    )

    # --- «0.98 и 98 понимаются одинаково»
    check(
        failures,
        pp_for_accuracy(MAP, 98).pp == pp_for_accuracy(MAP, 0.98).pp,
        "проценты и доля дали разный результат",
    )

    # --- requested_accuracy это то, что просили; accuracy - то, что достижимо
    check(failures, result.requested_accuracy == 0.98, f"requested_accuracy поехал: {result.requested_accuracy}")
    check(failures, 0.0 <= result.accuracy <= 1.0, f"accuracy вне [0,1]: {result.accuracy}")

    # --- Simulator это та же математика, только сложность считается один раз
    simulator = Simulator(MAP, mods="HDDT")
    check(failures, simulator.pp(0.98).pp == result.pp, "Simulator разошёлся с pp_for_accuracy")
    check(failures, simulator.star_rating == result.difficulty.star_rating, "star_rating разошёлся")
    check(failures, simulator.max_combo == result.difficulty.max_combo, "max_combo разошёлся")

    # --- повторные вызовы на одном Simulator не портят его состояние
    first = simulator.pp(0.95).pp
    simulator.pp(1.0)
    check(failures, simulator.pp(0.95).pp == first, "повторный вызов pp() на том же Simulator дал другое число")

    # --- больше точность - больше pp (в пределах, где расклад не вырождается)
    previous = None
    for accuracy in (MONOTONIC_FROM, 0.7, 0.9, 0.95, 0.99, 1.0):
        current = simulator.pp(accuracy).pp
        if previous is not None:
            check(failures, current >= previous, f"pp упал при росте точности до {accuracy}: {current} < {previous}")
        previous = current

    # --- моды: строка и список эквивалентны, неподдерживаемый отвергается
    check(
        failures,
        pp_for_accuracy(MAP, 0.98, "HDDT").pp == pp_for_accuracy(MAP, 0.98, ["HD", "DT"]).pp,
        "строка модов и список дали разный результат",
    )

    try:
        pp_for_accuracy(MAP, 0.98, "RX")
    except UnsupportedModError:
        pass
    else:
        failures.append("мод RX обязан подниматься UnsupportedModError, а не считаться молча")

    # --- мод CL: что обязано выполняться без всякого оракула
    #
    # Правильность чисел под CL доказывает гейт фазы 5. Здесь три свойства,
    # которые держат смысл мода и ломаются от правок в обвязке.
    classic = Simulator(MAP, mods="CL")

    # Сложность CL не трогает: он меняет судейство, а не геометрию.
    plain = Simulator(MAP)
    check(failures, classic.star_rating == plain.star_rating, "CL сдвинул звёзды")
    check(failures, classic.max_combo == plain.max_combo, "CL сдвинул max_combo")

    # Под CL точность считается по кругам, поэтому обе трактовки совпадают.
    # Карта и точность взяты те, на которых равенство НЕ тождественно: лишний
    # круг умножения-деления здесь сдвигает раскладку на одну сотку (см.
    # cl-acc-roundtrip в гейте фазы 6). На карте, где обе ветки дают одно
    # и то же по построению, проверка ничего не стоила бы.
    roundtrip = Simulator(BEATMAPS / "cl-acc-roundtrip.osu", mods="CL")
    check(
        failures,
        roundtrip.pp(0.95, raw_accuracy=True).pp == roundtrip.pp(0.95, raw_accuracy=False).pp,
        "под CL raw_accuracy изменил результат, хотя трактовки совпадают",
    )

    # На карте без слайдеров различать нечего: CL обязан совпасть с lazer.
    # MAP — как раз такая (21 круг), поэтому расхождение здесь означало бы,
    # что ветвление задевает что-то помимо слайдеров.
    check(
        failures,
        classic.pp(0.98, raw_accuracy=True).pp == plain.pp(0.98, raw_accuracy=True).pp,
        "на карте без слайдеров CL разошёлся с lazer",
    )

    # --- мусор на входе: отказ, а не число
    with tempfile.TemporaryDirectory() as directory:
        garbage = Path(directory) / "garbage.osu"
        garbage.write_text("это не карта", encoding="utf-8")
        try:
            pp_for_accuracy(garbage, 0.98)
        except BeatmapParseError:
            pass
        else:
            failures.append("мусорный файл обязан подниматься BeatmapParseError")

    # --- raw_accuracy переключает трактовку, а не ломает расчёт
    raw = pp_for_accuracy(MAP, 0.98, raw_accuracy=True)
    check(failures, raw.pp > 0, "raw_accuracy=True дал неположительный pp")

    if failures:
        print(f"РАСХОЖДЕНИЙ: {len(failures)}\n")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"Гейт публичного API пройден: {len(osu_ppsim.__all__)} имён в __all__, контракт README держится.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
