"""Сравнение порта с фикстурами оракула.

Главная тонкость — точность. Дамп сериализует поля C# как есть, и `float`-поля
(координаты, Scale) записываются в float32: 283.402 в JSON это на самом деле
283.4020080566406. Python читает такой литерал как double, поэтому прямое
сравнение с нашим значением даёт ложное расхождение.

Поэтому у каждого поля указана его точность, и float32-поля перед сравнением
приводятся к float32 с обеих сторон.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from osu_ppsim.f32 import f32

__all__ = ["load_fixture", "values_equal", "FLOAT32_FIELDS", "fixture_number"]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "raw"

#: Поля, объявленные в C# как float. Всё остальное — double и сравнивается точно.
FLOAT32_FIELDS: frozenset[str] = frozenset(
    {
        "x",
        "y",
        "stacked_x",
        "stacked_y",
        "scale",
        # CircularArcProperties.Radius и прочие геометрические float-величины,
        # если появятся в дампе, добавлять сюда же.
    }
)


def load_fixture(beatmap: str, mods: str = "NM") -> dict:
    path = FIXTURES / f"{beatmap}__{mods}.json"
    if not path.exists():
        raise FileNotFoundError(f"нет фикстуры {path}; собрать: python3 tools/oracle.py fixtures")
    return json.loads(path.read_text())


def fixture_number(value):
    """Разворачивает NaN и Infinity, которые дамп пишет строками."""
    if isinstance(value, str):
        return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}.get(value, value)
    return value


def values_equal(field: str, got, expected) -> bool:
    """Сравнивает значение порта с фикстурой с учётом точности поля."""
    expected = fixture_number(expected)

    if got is None or expected is None:
        return got is expected

    if isinstance(expected, str | bool):
        return got == expected

    if isinstance(expected, float) and math.isnan(expected):
        return isinstance(got, float) and math.isnan(got)

    if field in FLOAT32_FIELDS:
        return f32(got) == f32(expected)

    return got == expected
