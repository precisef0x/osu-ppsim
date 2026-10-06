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
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import osu_ppsim  # noqa: E402
from osu_ppsim import (  # noqa: E402
    BeatmapParseError,
    HitCounts,
    InvalidScoreError,
    Score,
    Simulator,
    UnsupportedModError,
    pp_for_accuracy,
    validate_score,
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


def check_score_cases(
    failures: list[str],
    simulator: Simulator,
    large_tick_count: int,
    cases: tuple[tuple[str, Score, bool], ...],
) -> int:
    classic = simulator.mods.classic_slider_accuracy
    context = {
        "total_objects": len(simulator.beatmap.hit_objects),
        "beatmap_max_combo": simulator.max_combo,
        "slider_count": simulator.difficulty.slider_count,
        "large_tick_count": large_tick_count,
    }
    if classic:
        context["classic_slider_accuracy"] = True
    # Для lazer флаг намеренно не передаётся: проверяется публичное умолчание.
    for label, score, accepted in cases:
        label = f"{'CL' if classic else 'lazer'}: {label}"
        try:
            validate_score(score, **context)
        except InvalidScoreError:
            if accepted:
                failures.append(f"validate_score отверг допустимый вход: {label}")
        else:
            if not accepted:
                failures.append(f"validate_score пропустил противоречие: {label}")

        checked = None
        try:
            checked = simulator.score(score)
        except InvalidScoreError:
            if accepted:
                failures.append(f"Simulator.score отверг допустимый вход: {label}")
        else:
            if not accepted:
                failures.append(f"Simulator.score пропустил противоречие: {label}")

        unchecked = simulator.score(score, validate=False)
        if checked is not None:
            check(failures, checked == unchecked, f"validate изменил результат: {label}")
            if classic:
                without_nested = replace(score, slider_tail_hits=None, large_tick_misses=0)
                check(
                    failures,
                    checked == simulator.score(without_nested),
                    f"под CL поля хвостов или тиков изменили результат: {label}",
                )
    return len(cases)


def check_score_validation(failures: list[str]) -> int:
    checked = 0
    for mods in ("NM", "CL"):
        circles = Simulator(MAP, mods)
        checked += check_score_cases(failures, circles, 0, (
            ("FC, неявный максимум", Score(HitCounts(21, 0, 0)), True),
            ("FC, явный максимум", Score(HitCounts(21, 0, 0), max_combo=21), True),
            ("сотки и пятидесятки сохраняют комбо", Score(HitCounts(18, 2, 1), max_combo=21), True),
            ("последний круг промахнут", Score(HitCounts(20, 0, 0, 1), max_combo=20), True),
            ("неявный максимум при промахе", Score(HitCounts(20, 0, 0, 1)), False),
            ("явный максимум при промахе", Score(HitCounts(20, 0, 0, 1), max_combo=21), False),
            ("комбо выше максимума", Score(HitCounts(21, 0, 0), max_combo=22), False),
            ("отрицательное комбо", Score(HitCounts(21, 0, 0), max_combo=-1), False),
            ("нулевое комбо при попаданиях", Score(HitCounts(21, 0, 0), max_combo=0), False),
            ("всё промахнуто", Score(HitCounts(0, 0, 0, 21), max_combo=0), True),
            ("всё промахнуто, неявный максимум", Score(HitCounts(0, 0, 0, 21)), False),
            ("попаданий больше числа объектов", Score(HitCounts(22, 0, 0)), False),
            ("попаданий меньше числа объектов", Score(HitCounts(20, 0, 0)), False),
            ("отрицательные great", Score(HitCounts(-1, 22, 0)), False),
            ("отрицательные ok", Score(HitCounts(22, -1, 0)), False),
            ("отрицательные meh", Score(HitCounts(22, 0, -1)), False),
            ("отрицательные miss", Score(HitCounts(22, 0, 0, -1)), False),
            ("отрицательные хвосты", Score(HitCounts(21, 0, 0), slider_tail_hits=-1), False),
            ("отрицательные пропущенные тики", Score(HitCounts(21, 0, 0), large_tick_misses=-1), False),
            ("хвост на карте без слайдеров", Score(HitCounts(21, 0, 0), slider_tail_hits=1), False),
            ("пропущенный тик на карте без тиков", Score(HitCounts(21, 0, 0), large_tick_misses=1), False),
            ("отрицательные очки ScoreV1", Score(HitCounts(21, 0, 0), legacy_total_score=-1), False),
        ))

        slider = Simulator(BEATMAPS / "old-stack-repeat.osu", mods)
        # Порядок: голова, реверс, хвост, три круга. У потерянного хвоста
        # нет прироста комбо; промах по реверсу сбрасывает уже набранное.
        checked += check_score_cases(failures, slider, 1, (
            ("слайдерный FC, умолчания", Score(HitCounts(4, 0, 0)), True),
            ("слайдерный FC, явные поля", Score(HitCounts(4, 0, 0), max_combo=6, slider_tail_hits=1), True),
            ("промах и максимум", Score(HitCounts(3, 0, 0, 1)), False),
            ("полное комбо и потерянный реверс", Score(HitCounts(4, 0, 0), max_combo=6, large_tick_misses=1), mods == "CL"),
            ("полное комбо и потерянный хвост", Score(HitCounts(4, 0, 0), max_combo=6, slider_tail_hits=0), mods == "CL"),
            ("слишком много хвостов", Score(HitCounts(4, 0, 0), slider_tail_hits=2), False),
            ("слишком много пропущенных тиков", Score(HitCounts(4, 0, 0), large_tick_misses=2), False),
        ))
        if mods == "NM":
            checked += check_score_cases(failures, slider, 1, (
                ("хвост не собран, остальные попадания подряд", Score(HitCounts(4, 0, 0), max_combo=5, slider_tail_hits=0), True),
                ("после промаха по реверсу собраны хвост и круги", Score(HitCounts(4, 0, 0), max_combo=4, large_tick_misses=1), True),
                ("потеряны голова и реверс", Score(HitCounts(3, 0, 0, 1), max_combo=4, large_tick_misses=1), True),
                ("потеряны все части слайдера", Score(HitCounts(3, 0, 0, 1), max_combo=3, slider_tail_hits=0, large_tick_misses=1), True),
                ("комбо выше суммы оставшихся попаданий", Score(HitCounts(3, 0, 0, 1), max_combo=4, slider_tail_hits=0, large_tick_misses=1), False),
                ("неявный максимум при потерях тиков и хвостов", Score(HitCounts(4, 0, 0), slider_tail_hits=0, large_tick_misses=1), False),
                ("основные объекты промахнуты, вложенные собраны", Score(HitCounts(0, 0, 0, 4), max_combo=0), False),
                ("собран только реверс, нулевое комбо", Score(HitCounts(0, 0, 0, 4), max_combo=0, slider_tail_hits=0), False),
                ("собран только реверс", Score(HitCounts(0, 0, 0, 4), max_combo=1, slider_tail_hits=0), True),
                ("собран только хвост, нулевое комбо", Score(HitCounts(0, 0, 0, 4), max_combo=0, large_tick_misses=1), False),
                ("собран только хвост", Score(HitCounts(0, 0, 0, 4), max_combo=1, large_tick_misses=1), True),
                ("промахнуты основные и вложенные объекты", Score(HitCounts(0, 0, 0, 4), max_combo=0, slider_tail_hits=0, large_tick_misses=1), True),
            ))
        else:
            checked += check_score_cases(failures, slider, 1, (
                ("промах по последнему кругу", Score(HitCounts(3, 0, 0, 1), max_combo=5), True),
                ("оба вложенных поля на границах", Score(HitCounts(4, 0, 0), slider_tail_hits=0, large_tick_misses=1), True),
                ("всё промахнуто, вложенные поля по умолчанию", Score(HitCounts(0, 0, 0, 4), max_combo=0), True),
                ("промах ограничивает комбо и при вложенных полях", Score(HitCounts(3, 0, 0, 1), max_combo=6, slider_tail_hits=0, large_tick_misses=1), False),
            ))

        empty = Simulator(b"osu file format v14\n[General]\nMode:0\n[HitObjects]\n", mods)
        checked += check_score_cases(failures, empty, 0, (
            ("пустая карта, умолчания", Score(HitCounts(0, 0, 0)), True),
            ("пустая карта, явное нулевое комбо", Score(HitCounts(0, 0, 0), max_combo=0), True),
            ("пустая карта, ненулевое комбо", Score(HitCounts(0, 0, 0), max_combo=1), False),
        ))
    return checked


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

    # --- карта может прийти не с диска
    #
    # Байты — это содержимое .osu, а не путь: карту, скачанную по HTTP, класть
    # на диск не нужно. Путь и содержимое обязаны дать один и тот же ответ.
    content = MAP.read_bytes()
    from_path = pp_for_accuracy(MAP, 0.98, "HDDT").pp
    check(failures, pp_for_accuracy(content, 0.98, "HDDT").pp == from_path, "bytes разошлись с путём")
    check(failures, pp_for_accuracy(bytearray(content), 0.98, "HDDT").pp == from_path, "bytearray разошёлся с путём")
    check(
        failures,
        Simulator(osu_ppsim.decode_beatmap_string(content.decode("utf-8")), "HDDT").pp(0.98).pp == from_path,
        "decode_beatmap_string разошёлся с путём",
    )

    # Кодировка определяется по BOM внутри decode_beatmap_bytes — ровно так же,
    # как её определяет сам osu!. Карты в UTF-16 в корпусе есть, и путь через
    # байты обязан их вытянуть, а не рассыпаться на mojibake.
    utf16 = b"\xff\xfe" + content.decode("utf-8").encode("utf-16-le")
    check(failures, pp_for_accuracy(utf16, 0.98, "HDDT").pp == from_path, "UTF-16 с BOM разошёлся с UTF-8")

    try:
        pp_for_accuracy(b"\x00\x01 not a beatmap", 0.98)
    except BeatmapParseError:
        pass
    else:
        failures.append("мусорные байты обязаны подниматься BeatmapParseError")

    # --- ряды пообъектной сложности: то, из чего строят график
    #
    # Значения сверены с оракулом в гейте фазы 3. Здесь — форма: что ряды
    # выровнены между собой, что ось времени не идёт вспять и что flashlight
    # появляется ровно вместе с модом.
    series = Simulator(MAP, "HDDT").object_difficulties
    expected_length = 21 - 1  # первый объект прыжка не образует
    check(failures, len(series) == expected_length, f"рядов {len(series)}, ожидалось {expected_length}")

    for name in ("aim", "aim_no_sliders", "speed", "reading"):
        row = getattr(series, name)
        check(failures, len(row) == len(series), f"ряд {name} не выровнен с times")

    check(failures, list(series.times) == sorted(series.times), "времена идут не по возрастанию")
    check(failures, series.flashlight is None, "без мода FL flashlight обязан быть None")

    with_fl = Simulator(MAP, "FL").object_difficulties
    check(failures, with_fl.flashlight is not None, "под FL flashlight обязан считаться")
    check(
        failures,
        with_fl.flashlight is not None and len(with_fl.flashlight) == len(with_fl),
        "ряд flashlight не выровнен с times",
    )

    # Времена делятся на clock rate — это время звучания, а не время в файле.
    # Под DT ряд обязан сжаться ровно в 1.5 раза; иначе график уедет по оси X.
    plain_times = Simulator(MAP).object_difficulties.times
    fast_times = Simulator(MAP, "DT").object_difficulties.times
    check(
        failures,
        all(abs(f * 1.5 - p) < 1e-9 for p, f in zip(plain_times, fast_times, strict=True)),
        "под DT времена не поделены на clock rate",
    )

    # aim и aim_no_sliders — два РАЗНЫХ скилла, а не один и тот же дважды.
    # На карте без слайдеров они совпадают по построению, поэтому различать их
    # надо там, где слайдеров много: у legacy-drain-underflow их 148.
    # (Поэлементного «aim не ниже aim_no_sliders» тут нет: на huge-arc-radius
    # это неверно, ряд без слайдеров местами выше. Что ряды сходятся с C#
    # поимённо, доказывает гейт фазы 3 — здесь только что они не склеены.)
    sliders = Simulator(BEATMAPS / "legacy-drain-underflow.osu").object_difficulties
    check(failures, sliders.aim != sliders.aim_no_sliders, "на карте со 148 слайдерами aim склеился с aim_no_sliders")
    check(failures, series.aim == series.aim_no_sliders, "на карте без слайдеров aim разошёлся с aim_no_sliders")

    # Вырожденные карты: рядов нет, но flashlight обязан различать «мода FL нет»
    # (None) и «мод есть, считать было нечего» (пустой ряд). Карта собирается
    # в памяти — заодно через тот же путь из байтов.
    header = (
        "osu file format v14\n\n[General]\nMode: 0\n\n[Difficulty]\n"
        "HPDrainRate:5\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n"
        "SliderMultiplier:1.4\nSliderTickRate:1\n\n[TimingPoints]\n0,300,4,2,0,60,1,0\n\n[HitObjects]\n"
    )
    for label, body in (("без объектов", ""), ("с одним объектом", "100,100,1000,1,0,0:0:0:0:\n")):
        empty = (header + body).encode()
        check(failures, Simulator(empty).object_difficulties.flashlight is None, f"{label}: без FL ожидался None")
        check(
            failures,
            Simulator(empty, "FL").object_difficulties.flashlight == (),
            f"{label}: под FL ожидался пустой ряд, а не None",
        )

    # Ряды не должны зависеть от того, считали ли до этого pp.
    sim_series = Simulator(MAP, "HDDT")
    before = sim_series.object_difficulties.aim
    sim_series.pp(0.95)
    check(failures, sim_series.object_difficulties.aim == before, "расчёт pp испортил ряды сложности")

    # --- невозможный скор отвергается, а не считается молча
    #
    # Для фиксированных карт ожидаемые исходы заданы независимо от валидатора:
    # проверяются границы счётчиков, значения по умолчанию и обе механики.
    check(failures, issubclass(InvalidScoreError, ValueError), "InvalidScoreError обязан быть ValueError")

    validation_cases = check_score_validation(failures)

    if failures:
        print(f"РАСХОЖДЕНИЙ: {len(failures)}\n")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"случаев валидации: {validation_cases}, проверены validate_score и Simulator.score")
    print(f"Гейт публичного API пройден: {len(osu_ppsim.__all__)} имён в __all__, контракт README держится.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
