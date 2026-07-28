"""Гейт краевых случаев.

Каждая карта в tests/edge_cases — крошечный синтетический .osu, бьющий ровно
в одну ветку расчёта, и написана под конкретную найденную ошибку. Сравниваются
с живым запуском osu-tools: карты настолько малы, что звёзд, MaxCombo и pp
достаточно.

ПРОВЕРЯТЬ ПРОБУ НА ОТРИЦАТЕЛЬНОМ РЕЗУЛЬТАТЕ ОБЯЗАТЕЛЬНО. Две из них поначалу
воспроизводили условие, но результата не меняли — и молча проходили бы
с возвращённой ошибкой. Прежде чем добавлять карту сюда, верните ошибку
и убедитесь, что гейт краснеет.

Запуск: python3 tests/test_edge_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from oracle import OracleError, simulate  # noqa: E402
from osu_ppsim import pp_for_accuracy  # noqa: E402

BEATMAPS = Path(__file__).resolve().parent / "edge_cases"

#: Что именно ловит каждая карта — чтобы упавший гейт сразу называл причину.
CASES: dict[str, str] = {
    "clamp-cs12": "CS=12 зажимается в 10 (applyDifficultyRestrictions)",
    "clamp-sm5": "SliderMultiplier=5 зажимается в 3.6",
    "clamp-beatlength": "beatLength=2 зажимается в 6 (BeatLengthBindable)",
    "clamp-sv-v7": "множитель скорости зажимается в 10 (SliderVelocityBindable)",
    "tickmult-f32-v7": "шаг тиков у карт v<8 считается в double, а не во float32",
    "old-stack-repeat": "старый стакинг берёт PositionAt(1), а не конец с учётом пролётов",
    "sv-before-first-point": "до первой точки сложности действует умолчание SV=1",
    "same-time-green-red": "при равном времени зелёная линия побеждает красную",
    "fractional-pos": "координаты объекта усекаются до целых при формате < 128",
    "old-stack-spinner": "спиннер не получает смещения стака (Spinner.StackOffset = Zero)",
    "snap-back-and-forth": "нерф за движение туда-обратно: скобка (1 - distance) считается во float32",
    "acc-rounding": "accuracy проходит круг умножения и деления на число объектов",
    "zero-slider-repeats": "у слайдера нулевой длины повторы сбрасываются (защита от накрутки комбо)",
    "no-speed-difficulty": "нулевая скоростная сложность: 4/0 даёт бесконечность, а не падение",
    "frac-pos-float32": "координата усекается до целого ПОСЛЕ округления до float32",
    "broken-line-skipped": "битая строка пропускается, а не отвергает карту целиком",
    "empty-path-segment": "пустой сегмент пути отвергает объект, как IndexOutOfRange в C#",
    "huge-arc-radius": "дуга радиусом в миллионы: acos(1f) = 0, приведение к int насыщается",
    "utf16-beatmap": "файл в UTF-16: StreamReader распознаёт метку порядка байтов",
    "cl-acc-roundtrip": "под CL точность идёт в раскладку без лишнего круга умножения-деления",
    "cl-no-hit-circles": "под CL карта без кругов обнуляет accuracy-компонент, а не падает",
}

#: По умолчанию карты проверяются на FC со 100%. Карте acc-rounding нужна другая
#: точность: её смысл в том, что оценка числа соток попадает ровно на половину.
ACCURACY = 100.0
ACCURACIES: dict[str, float] = {"acc-rounding": 85.0, "cl-acc-roundtrip": 95.0, "cl-no-hit-circles": 95.0}

#: По умолчанию моды не применяются: карты проверяют декодер, на который они
#: не влияют. Исключение — пробы на классический счёт: ветку с пустым составом
#: объектов, несущих точность, под lazer не получить вовсе, там слайдеры
#: не дают ей обнулиться.
MODS: dict[str, str] = {"cl-acc-roundtrip": "CL", "cl-no-hit-circles": "CL"}


def main() -> int:
    failures: list[str] = []

    print(f"{'карта':<24} {'звёзды':>18} {'combo':>7}  что проверяет")
    print("-" * 100)

    for name, description in CASES.items():
        path = BEATMAPS / f"{name}.osu"
        if not path.exists():
            failures.append(f"{name}: нет файла {path}")
            continue

        accuracy = ACCURACIES.get(name, ACCURACY)
        mods = MODS.get(name, "")

        try:
            expected = simulate(path, accuracy, tuple(mods[i : i + 2] for i in range(0, len(mods), 2)))
        except OracleError as exc:
            print(f"ОРАКУЛ НЕДОСТУПЕН: {exc}", file=sys.stderr)
            return 2

        expected_attributes = expected["difficulty_attributes"]
        expected_pp = expected["performance_attributes"]["pp"]

        # osu-tools принимает «сырую» точность, поэтому обычным пробам хватает
        # raw_accuracy=True. Под CL обе трактовки обязаны совпасть с оракулом —
        # и проверять надо именно обе: с raw_accuracy=True перевод точности
        # не вызывается вовсе, то есть половина расчёта осталась бы непокрытой.
        raw_variants = (True, False) if "CL" in mods else (True,)

        result = pp_for_accuracy(path, accuracy, mods, raw_accuracy=raw_variants[0])

        for field, actual, wanted in (
            ("star_rating", result.difficulty.star_rating, expected_attributes["star_rating"]),
            ("max_combo", result.difficulty.max_combo, expected_attributes["max_combo"]),
            ("pp", result.pp, expected_pp),
        ):
            if actual != wanted:
                failures.append(f"{name}.{field}: {actual!r} != {wanted!r}  ({description})")

        for raw in raw_variants[1:]:
            other_pp = pp_for_accuracy(path, accuracy, mods, raw_accuracy=raw).pp
            if other_pp != expected_pp:
                failures.append(f"{name}.pp[raw_accuracy={raw}]: {other_pp!r} != {expected_pp!r}  ({description})")

        print(f"{name:<24} {result.difficulty.star_rating:>18.12f} {result.difficulty.max_combo:>7}  {description}")

    print("-" * 100)

    if failures:
        print(f"РАСХОЖДЕНИЙ: {len(failures)}\n")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"Гейт краевых случаев пройден: {len(CASES)} карт, расхождений нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
