"""Прогон всех гейтов подряд.

Порядок важен: фазы идут снизу вверх, и при поломке смотреть надо на самую
раннюю упавшую — расхождение в разборе карты неизбежно утащит за собой всё
остальное.

Запуск: python3 tests/run_all.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: Фазы 1-4 сверяются с сохранёнными дампами, фазам 5 и 6 нужен собранный
#: оракул: они сравниваются с живым запуском osu-tools.
#:
#: Снапшот идёт последним и фазой не является: он не слой порта, а сеть на
#: регрессию поверх всех слоёв — единственный гейт, которому хватает того,
#: что лежит в git (см. шапку test_snapshot.py). Упади он вместе с фазами —
#: причину всё равно надо искать в самой ранней упавшей фазе.
GATES = (
    ("1", "разбор карты и геометрия", "test_phase1_beatmap.py", False),
    ("2", "OsuDifficultyHitObject", "test_phase2_difficulty_objects.py", False),
    ("3", "скиллы и эвалуаторы", "test_phase3_skills.py", False),
    ("4", "атрибуты и звёзды", "test_phase4_attributes.py", False),
    ("5", "pp за FC", "test_phase5_performance.py", True),
    ("6", "краевые случаи декодера", "test_edge_cases.py", True),
    ("7", "pp за произвольный скор", "test_phase7_scores.py", True),
    ("—", "снапшот краевых случаев", "test_snapshot.py", False),
    ("—", "публичный API", "test_public_api.py", False),
)


def main() -> int:
    failed: list[str] = []

    for phase, title, script, needs_oracle in GATES:
        name = f"фаза {phase}" if phase.isdigit() else title
        label = f"фаза {phase}: {title}" if phase.isdigit() else title
        if needs_oracle:
            label += " (нужен оракул)"

        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")

        started = time.perf_counter()
        result = subprocess.run([sys.executable, str(TESTS / script)], capture_output=True, text=True)
        elapsed = time.perf_counter() - started

        # Показываем только хвост: у гейтов подробный вывод, а нужен итог.
        tail = result.stdout.rstrip().splitlines()[-3:]
        for line in tail:
            print(line)

        if result.returncode == 2:
            print(f"ПРОПУЩЕНО за {elapsed:.1f} с — оракул недоступен")
            if result.stderr.strip():
                print(result.stderr.strip().splitlines()[0])
            continue

        if result.returncode != 0:
            failed.append(name)
            print(f"ПРОВАЛ за {elapsed:.1f} с")
            # Гейт, упавший исключением, пишет всё в stderr: без этого
            # на экране остался бы один «ПРОВАЛ» без единой строки причины.
            if result.stderr.strip():
                print(result.stderr.rstrip())
        else:
            print(f"за {elapsed:.1f} с")

    print(f"\n{'=' * 70}")
    if failed:
        print(f"ПРОВАЛЕНЫ: {', '.join(failed)}")
        print("Смотреть надо на самую раннюю из них.")
        return 1

    print("Все гейты пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
