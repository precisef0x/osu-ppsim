# osu-ppsim

[![CI](https://github.com/precisef0x/osu-ppsim/actions/workflows/ci.yml/badge.svg)](https://github.com/precisef0x/osu-ppsim/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Расчёт **pp за FC-прохождение** карты osu!standard на заданной accuracy.
Чистый Python, без зависимостей.

Соответствует версии расчёта osu! **`20260706`** — ребаланс
[«2026 Q2 SR & PP release»](https://github.com/ppy/osu/pull/37850).

```bash
pip install git+https://github.com/precisef0x/osu-ppsim
```

Нужен Python 3.11 или новее. Внешних зависимостей нет.

## Как пользоваться

```python
from osu_ppsim import pp_for_accuracy

result = pp_for_accuracy("map.osu", accuracy=0.98, mods="HDDT")

result.pp                      # 1156.20
result.difficulty.star_rating  # 9.845
result.difficulty.max_combo    # 2359
result.hit_counts              # HitCounts(great=1553, ok=59, meh=0, miss=0)
```

Для нескольких значений точности на одной карте берите `Simulator`: сложность
от accuracy не зависит и считается один раз.

```python
from osu_ppsim import Simulator

sim = Simulator("map.osu", mods="HDDT")
print(sim.star_rating, sim.max_combo)

for accuracy in (1.0, 0.99, 0.98, 0.95):
    print(accuracy, sim.pp(accuracy).pp)
```

Разница ощутимая: построение `Simulator` на карте в 1600 объектов занимает
около 350 мс, каждый последующий расчёт pp — порядка 9 мкс.

### Про accuracy

Точность задаётся долей или процентами — `0.98` и `98` понимаются одинаково.

Ровно 98% целым числом попаданий обычно не набрать, поэтому результат отдаёт
и запрошенную точность, и фактически достижимую:

```python
r = pp_for_accuracy("map.osu", 0.98)
r.requested_accuracy  # 0.98
r.accuracy            # 0.979894
```

По умолчанию accuracy понимается так, как её видит игрок: в lazer в неё входят
не только круги, но и хвосты слайдеров с тиками. Флаг `raw_accuracy=True`
переключает на «сырую» точность по кругам — в этом виде её принимает `osu-tools`
в ключе `-a`.

### Скор из stable: мод `CL`

Точность и pp в stable считаются иначе, чем в lazer, и на сайте такие скоры
проходят через мод Classic. Чтобы получить их число, добавьте `CL`:

```python
pp_for_accuracy("map.osu", 0.99, "CL")        # 99% так, как их видит stable
```

Под `CL` точность считается по одним кругам, поэтому `raw_accuracy` на неё
не влияет — обе трактовки совпадают.

Разница не косметическая. На карте `801165` за FC на 99%:

| | pp |
|---|---|
| lazer (`mods=None`) | 344.63 |
| stable (`mods="CL"`) | 334.67 |

Растёт она с долей слайдеров и числом соток: от ~1% на SS до ~6% на слайдерных
картах в районе 95–97%.

### Ошибки

```python
from osu_ppsim import BeatmapParseError, UnsupportedModError

try:
    pp_for_accuracy("map.osu", 0.98, "RX")
except UnsupportedModError as exc:
    print(exc)   # мод RX не поддерживается; охват: CL, DC, DT, EZ, FL, HD, HR, HT, NC, NF, NM
except BeatmapParseError as exc:
    print(exc)   # не разобрать карту: ...
```

Неподдерживаемые моды не считаются молча: тихо выдать неверное число хуже,
чем отказаться.

## Что поддерживается

| | |
|---|---|
| Режим | только osu!standard |
| Скор | только FC — без промахов, комбо максимальное |
| Механика | lazer и stable (мод `CL`) |
| Моды | `NM`, `NF`, `DT`, `NC`, `HT`, `DC`, `HR`, `EZ`, `HD`, `FL`, `CL` |
| Accuracy | от 16.67% до 100% (ниже FC недостижим — см. [docs/ACCURACY.md](docs/ACCURACY.md)) |

## Зачем это, если есть rosu-pp

rosu-pp на момент написания портирует состояние osu! от **2025-10-13** и не
знает о скилле `Reading`, который появился в ребалансе 2026 Q2 и заменил прежние
бонусы за AR и HD. Обе механики счёта у него есть (`.lazer(false)` для stable),
но формула — доребалансная, поэтому расходится с сайтом в обоих режимах.
Официальный API тоже не выручает: `BeatmapDifficultyAttributes` не отдаёт
ни `reading_difficulty`, ни `flashlight_difficulty`, ни `hit_circle_count`,
без которых актуальные pp не восстановить.

## Точность расчёта

Порт сверяется с настоящим C#-кодом osu!, а не с опубликованными значениями,
и на всех проверках сходится с ним бит в бит:

| Проверка | Объём | Результат |
|---|---|---|
| Гейты по слоям | 6 фаз, от разбора файла до pp | расхождений нет |
| Сверка на корпусе | 5600 карт, 16 800 комбинаций | худшее отклонение `4.1e-16` |
| Сплошной прогон | 231 780 файлов | аномалий нет |

Обе механики счёта проверяются одинаково: в гейте pp 420 комбинаций из 1020 —
классические, в корпусной сверке классическая половина наборов модов.

Как это устроено и что осталось за границей охвата — [docs/ACCURACY.md](docs/ACCURACY.md).
Как порт соотносится с C#-оригиналом и что учесть при следующем ребалансе —
[docs/PORTING.md](docs/PORTING.md).

## Разработка

Сразу после клонирования запускаются два гейта — им хватает того, что лежит
в git:

```bash
python3 tests/test_snapshot.py
python3 tests/test_public_api.py
```

Снапшот ловит регрессию (расчёт молча уехал), но не правильность: значения
сняты с самого порта. Второй гейт держит контракт публичного API из README.
Правильность доказывают остальные гейты, и им нужен оракул —
клоны `ppy/osu` и `ppy/osu-tools` на запиненных коммитах плюс .NET 8. Как его
поднять, описано в [oracle/PINNED.md](oracle/PINNED.md); после этого:

```bash
python3 tests/run_all.py
```

Гейты идут снизу вверх, и при поломке смотреть надо на самый ранний упавший.

## Лицензия

[MIT](LICENSE).
