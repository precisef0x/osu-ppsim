# Зафиксированные версии оракула

Оракул — дев-инструмент для генерации эталонных фикстур. В библиотеку не входит.
Сами клоны в git не попадают (см. `.gitignore`); воспроизводятся по хешам ниже.

## Целевая версия расчёта

**`OsuDifficultyCalculator.Version = 20260706`**
(«2026 Q2 SR & PP release», [ppy/osu#37850](https://github.com/ppy/osu/pull/37850) от 01.07.2026;
константа поднята в [#38226](https://github.com/ppy/osu/pull/38226) от 06.07.2026)

## Пины

| Репозиторий | Коммит | Дата | Комментарий |
|---|---|---|---|
| `ppy/osu` | `52461f1b82672f019867c2078b357d3cb5d1f130` | 27.07.2026 | `Version => 20260706` |
| `ppy/osu-tools` | `f8bc6aa72b3b2f00c7bbbce3e5caa566919afa26` | 05.07.2026 | «2026 Q2 PP/SR update (#309)» |

## Воспроизведение

```bash
mkdir -p oracle && cd oracle
git clone https://github.com/ppy/osu.git osu
git -C osu checkout 52461f1b82672f019867c2078b357d3cb5d1f130
git clone https://github.com/ppy/osu-tools.git osu-tools
git -C osu-tools checkout f8bc6aa72b3b2f00c7bbbce3e5caa566919afa26
cd osu-tools && ./UseLocalOsu.sh
```

`UseLocalOsu.sh` обязателен: по умолчанию osu-tools тянет игру пакетами
`ppy.osu.Game 2026.702.1` (собран 02.07.2026) — это состояние **до** бампа `Version`
до `20260706`, то есть не наша цель.

## SDK

Нужен **.NET 8**, не свежее: `global.json` в `ppy/osu` пинит `8.0.100` с
`rollForward: latestFeature`, что ограничивает роллфорвард веткой 8.0.

У osu-tools своего `global.json` нет, поэтому рядом лежит наш `oracle/global.json` —
без него `dotnet` выбрал бы установленный 10.0.302.

```bash
brew install --cask dotnet-sdk@8
```

Проверка (обязательно из каталога под `oracle/`, иначе увидишь 10.x):

```bash
cd oracle/osu-tools && dotnet --version   # -> 8.0.423
```

## Эталонные значения

Из `osu.Game.Rulesets.Osu.Tests/OsuDifficultyCalculatorTest.cs`.
Карты лежат в `osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps/`.

| Карта | Звёзды (NM) | Звёзды (DT) | MaxCombo |
|---|---|---|---|
| `diffcalc-test` | 6.5243170265483581 | 9.4677607900646308 | 239 |
| `zero-length-sliders` | 1.3280410795791415 | 1.6856612715618886 | 54 |
| `very-fast-slider` | 0.40867325147697559 | 0.53588473186572561 | 4 |
| `nan-slider` | 0.87058175794353554 | — | 6 |
| `801165` | 6.3059767387139756 | — | 2359 |

Последние три карты подобраны ppy как краевые случаи: слайдеры нулевой длины,
сверхбыстрый слайдер, слайдер с NaN. `801165` — реальная карта, единственная длинная.
