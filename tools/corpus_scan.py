#!/usr/bin/env python3
"""Сплошной прогон корпуса .osu без оракула: отказы разбора и падения.

Дев-инструмент. Дополняет tools/sweep.py, а не заменяет его: sweep сверяет
числа с эталоном на случайной выборке, а этот скрипт проходит корпус целиком
и отвечает на два других вопроса —

  * какие входы порт отказывается разбирать и законен ли отказ;
  * где он падает там, где C# спокойно продолжает.

Оракул для этого не нужен, поэтому прогон выполним целиком: 70 мс на карту,
шесть процессов, около 45 минут на 230 тысяч файлов. Именно так нашлись
семнадцатая (дуга радиусом в миллионы делила на ноль) и восемнадцатая
(пустой сегмент пути, где порт был снисходительнее C#) ошибки.

Найденные аномалии выписываются построчно в JSONL — дальше их стоит прогнать
через оракул точечно.

    python3 tools/corpus_scan.py ~/Downloads/osu_files
    python3 tools/corpus_scan.py ~/Downloads/osu_files --out /tmp/anomalies.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from osu_ppsim import BeatmapParseError, Simulator, decode_beatmap  # noqa: E402

#: Числа и содержимое кавычек выкидываем из сообщения, чтобы одинаковые причины
#: схлопывались в одну строку статистики, а не рассыпались по значениям.
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_QUOTED = re.compile(r"'[^']*'")

#: Точность, на которой считаются pp. Величина не важна — важно, что расчёт pp
#: вообще выполняется: четырнадцатая находка жила именно там, а не в сложности.
_ACCURACY = 0.98


def _signature(exc: BaseException) -> str:
    text = _QUOTED.sub("'…'", str(exc))
    return f"{type(exc).__name__}: {_NUM.sub('N', text)[:120]}"


def examine(path_str: str) -> dict:
    """Разбирает и считает одну карту, возвращая стадию и причину отказа."""
    path = Path(path_str)
    result: dict = {"name": path.name}

    try:
        beatmap = decode_beatmap(path)
    except BeatmapParseError as exc:
        result["stage"] = "не std" if "osu!standard" in str(exc) else "разбор"
        result["reason"] = _signature(exc)
        return result
    except Exception as exc:  # noqa: BLE001 — любое другое исключение уже дефект
        result["stage"] = "разбор: падение"
        result["reason"] = _signature(exc)
        result["traceback"] = traceback.format_exc().splitlines()[-3:]
        return result

    result["objects"] = len(beatmap.hit_objects)
    result["unparsed_lines"] = beatmap.unparsed_lines

    if not beatmap.hit_objects:
        result["stage"] = "пустая"
        return result

    try:
        simulator = Simulator(path)
        result["star_rating"] = simulator.star_rating
        result["max_combo"] = simulator.max_combo
        result["pp"] = simulator.pp(_ACCURACY).pp
    except Exception as exc:  # noqa: BLE001
        result["stage"] = "расчёт: падение"
        result["reason"] = _signature(exc)
        result["traceback"] = traceback.format_exc().splitlines()[-3:]
        return result

    # Без оракула неверное число само себя не выдаст — кроме NaN и бесконечности.
    for field in ("star_rating", "pp"):
        value = result[field]
        if value != value or value in (float("inf"), float("-inf")):
            result["stage"] = "нечисло"
            result["reason"] = f"{field} = {value!r}"
            return result

    result["stage"] = "ок"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpus", type=Path, help="каталог с .osu-файлами")
    parser.add_argument("--out", type=Path, default=ROOT / "anomalies.jsonl", help="куда выписать аномалии")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    args = parser.parse_args()

    files = sorted(str(p) for p in args.corpus.glob("*.osu"))
    if not files:
        print(f"в {args.corpus} нет .osu-файлов", file=sys.stderr)
        return 2

    print(f"файлов в корпусе: {len(files)}, процессов: {args.workers}", flush=True)

    stages: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    examples: dict[str, str] = {}
    with_broken_lines = 0

    with args.out.open("w", encoding="utf-8") as anomalies, ProcessPoolExecutor(args.workers) as pool:
        for done, result in enumerate(pool.map(examine, files, chunksize=64), 1):
            stage = result["stage"]
            stages[stage] += 1
            broken = bool(result.get("unparsed_lines"))
            with_broken_lines += broken

            if stage not in ("ок", "не std"):
                key = f"[{stage}] {result.get('reason', '—')}"
                reasons[key] += 1
                examples.setdefault(key, result["name"])

            if stage not in ("ок", "не std") or broken:
                anomalies.write(json.dumps(result, ensure_ascii=False) + "\n")

            if done % 20000 == 0:
                other = done - stages["ок"] - stages["не std"]
                print(f"  {done} файлов: ок {stages['ок']}, не std {stages['не std']}, прочее {other}", flush=True)

    print()
    print("=== по стадиям ===")
    for stage, count in stages.most_common():
        print(f"  {stage:<20} {count:>7}")
    print(f"  {'с битыми строками':<20} {with_broken_lines:>7}")

    if reasons:
        print()
        print("=== причины (кроме «ок» и «не std») ===")
        for reason, count in reasons.most_common(40):
            print(f"  {count:>6}  {reason}")
            print(f"          пример: {examples[reason]}")

    print()
    print(f"аномалии выписаны в {args.out}")

    # Отказы разбора и падения — повод разбираться, всё остальное штатно.
    suspicious = sum(count for stage, count in stages.items() if stage not in ("ок", "не std", "пустая"))
    return 1 if suspicious else 0


if __name__ == "__main__":
    raise SystemExit(main())
