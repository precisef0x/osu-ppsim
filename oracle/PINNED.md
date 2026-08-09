# Pinned oracle versions

The oracle is a development tool for generating reference fixtures. It is not
part of the library. The clones themselves are kept out of git (see
`.gitignore`); they are reproduced from the hashes below.

## Target calculation version

**`OsuDifficultyCalculator.Version = 20260706`**
("2026 Q2 SR & PP release", [ppy/osu#37850](https://github.com/ppy/osu/pull/37850)
of 2026-07-01; the constant was raised in
[#38226](https://github.com/ppy/osu/pull/38226) of 2026-07-06)

## Pins

| Repository | Commit | Date | Note |
|---|---|---|---|
| `ppy/osu` | `52461f1b82672f019867c2078b357d3cb5d1f130` | 2026-07-27 | `Version => 20260706` |
| `ppy/osu-tools` | `f8bc6aa72b3b2f00c7bbbce3e5caa566919afa26` | 2026-07-05 | "2026 Q2 PP/SR update (#309)" |

## Reproducing

```bash
mkdir -p oracle && cd oracle
git clone https://github.com/ppy/osu.git osu
git -C osu checkout 52461f1b82672f019867c2078b357d3cb5d1f130
git clone https://github.com/ppy/osu-tools.git osu-tools
git -C osu-tools checkout f8bc6aa72b3b2f00c7bbbce3e5caa566919afa26
cd osu-tools && ./UseLocalOsu.sh
```

`UseLocalOsu.sh` is mandatory: by default osu-tools pulls the game as the
`ppy.osu.Game 2026.702.1` package (built 2026-07-02), which is the state
**before** `Version` was bumped to `20260706` — not our target.

## SDK

**.NET 8** is required, not newer: `global.json` in `ppy/osu` pins `8.0.100`
with `rollForward: latestFeature`, which limits roll-forward to the 8.0 branch.

osu-tools has no `global.json` of its own, so ours sits next to it at
`oracle/global.json` — without it `dotnet` would pick the installed 10.0.302.

```bash
brew install --cask dotnet-sdk@8
```

Check (necessarily from a directory under `oracle/`, otherwise you will see
10.x):

```bash
cd oracle/osu-tools && dotnet --version   # -> 8.0.423
```

## Reference values

From `osu.Game.Rulesets.Osu.Tests/OsuDifficultyCalculatorTest.cs`. The beatmaps
live in `osu.Game.Rulesets.Osu.Tests/Resources/Testing/Beatmaps/`.

**These literals are not what the port must match.** ppy's own test holds them
to a `1e-5` tolerance, so they drift behind master: for `diffcalc-test` the
literal reads 6.5243170 while a live run of the pinned code gives 6.5243230 —
a gap of 6e-6, comfortably inside their tolerance and thousands of times wider
than ours. The port is compared against the running oracle, not against this
table; the table is only a sanity check that the right commit is checked out
(`python3 tools/oracle.py check`).

| Beatmap | Stars (NM) | Stars (DT) | MaxCombo |
|---|---|---|---|
| `diffcalc-test` | 6.5243170265483581 | 9.4677607900646308 | 239 |
| `zero-length-sliders` | 1.3280410795791415 | 1.6856612715618886 | 54 |
| `very-fast-slider` | 0.40867325147697559 | 0.53588473186572561 | 4 |
| `nan-slider` | 0.87058175794353554 | — | 6 |
| `801165` | 6.3059767387139756 | — | 2359 |

The last three were chosen by ppy as edge cases: zero-length sliders, an
extremely fast slider, a slider with NaN. `801165` is a real beatmap, the only
long one.
