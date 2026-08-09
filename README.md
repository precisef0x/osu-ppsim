# osu-ppsim

[![CI](https://github.com/precisef0x/osu-ppsim/actions/workflows/ci.yml/badge.svg)](https://github.com/precisef0x/osu-ppsim/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**pp** for an osu!standard beatmap: for an FC at a given accuracy, or for an
arbitrary score with misses and dropped combo.
Pure Python, no dependencies.

Matches osu! calculation version **`20260706`** — the
["2026 Q2 SR & PP release"](https://github.com/ppy/osu/pull/37850) rebalance.

```bash
pip install git+https://github.com/precisef0x/osu-ppsim
```

Requires Python 3.11 or newer.

## Usage

```python
from osu_ppsim import pp_for_accuracy

result = pp_for_accuracy("map.osu", accuracy=0.98, mods="HDDT")

result.pp                      # 1156.20
result.difficulty.star_rating  # 9.845
result.difficulty.max_combo    # 2359
result.hit_counts              # HitCounts(great=1553, ok=59, meh=0, miss=0)
```

For several accuracies on one beatmap use `Simulator`: difficulty does not
depend on accuracy and is computed once.

```python
from osu_ppsim import Simulator

sim = Simulator("map.osu", mods="HDDT")
print(sim.star_rating, sim.max_combo)

for accuracy in (1.0, 0.99, 0.98, 0.95):
    print(accuracy, sim.pp(accuracy).pp)
```

The difference is worth having: building a `Simulator` on a 1600-object beatmap
takes about 280 ms, while each subsequent pp calculation takes around 12 µs.

### An arbitrary score

If you know the hit breakdown and not just the accuracy, compute straight
from it:

```python
from osu_ppsim import HitCounts, Score, Simulator

sim = Simulator("map.osu", mods="HDDT")
result = sim.score(Score(
    counts=HitCounts(great=1542, ok=65, meh=0, miss=5),
    max_combo=1800,          # None means the beatmap's max combo
    slider_tail_hits=667,    # None means every tail was held
    large_tick_misses=3,
))

result.pp
result.accuracy                          # derived from the breakdown
result.performance.effective_miss_count  # includes estimated sliderbreaks
```

Everything except `counts` is optional and describes an FC by default — which
is why `pp()` is a special case of `score()` rather than a separate code path.

`slider_tail_hits` and `large_tick_misses` exist only in lazer's mechanics:
under `CL` they are not judged at all and affect neither accuracy nor pp.

#### A score from stable: the score total

On a classic score a sliderbreak is indistinguishable from a dropped tail — the
statistics only say "combo below maximum, no misses". The score total tells
them apart: in ScoreV1 every hit is multiplied by the current combo multiplier,
so the outcome depends on **how** the combo was broken and not merely on how
much of it was lost.

```python
sim = Simulator("map.osu", mods="CL")
sim.score(Score(counts=HitCounts(great=1560, ok=7, meh=0, miss=0),
                max_combo=1500, legacy_total_score=45_000_000))
```

The difference is substantial: on one and the same score the combo-based and
score-based estimates diverge by up to 10% pp. Every score submitted from
stable has a score total, and it arrives together with the statistics from the
osu! API.

### About accuracy

Accuracy is given as a fraction or a percentage — `0.98` and `98` are read
identically.

Exactly 98% is usually unreachable with a whole number of hits, so the result
reports both the requested accuracy and the one actually attainable:

```python
r = pp_for_accuracy("map.osu", 0.98)
r.requested_accuracy  # 0.98
r.accuracy            # 0.979894
```

By default accuracy means what the player sees: under lazer it includes not
only hit circles but slider tails and ticks as well. The `raw_accuracy=True`
flag switches to the "raw" accuracy over circles alone — the form `osu-tools`
takes in its `-a` option.

Under `CL` accuracy counts hit circles only, so both readings coincide and the
flag makes no difference.

### A score from stable: the `CL` mod

Accuracy and pp in stable are computed differently than in lazer, and on the
website such scores go through the Classic mod. To get their number, add `CL`:

```python
pp_for_accuracy("map.osu", 0.99, "CL")        # 99% as stable sees it
```

The difference is not cosmetic. On beatmap `801165`, for an FC at 99%:

| | pp |
|---|---|
| lazer (`mods=None`) | 344.63 |
| stable (`mods="CL"`) | 334.67 |

It grows with the share of sliders and the number of 100s: from ~1% on an SS
to ~6% on slider-heavy beatmaps around 95–97%.

### Errors

```python
from osu_ppsim import BeatmapParseError, UnsupportedModError

try:
    pp_for_accuracy("map.osu", 0.98, "RX")
except UnsupportedModError as exc:
    print(exc)   # mod RX is not supported; supported: CL, DC, DT, EZ, FL, HD, HR, HT, NC, NF, NM
except BeatmapParseError as exc:
    print(exc)   # cannot parse beatmap: ...
```

Unsupported mods are not silently ignored: quietly returning a wrong number is
worse than refusing.

## Scope

| | |
|---|---|
| Ruleset | osu!standard only |
| Score | any: misses, dropped combo, unheld tails and ticks |
| Scores from stable | with a ScoreV1 total — misses are estimated from it |
| Mechanics | lazer and stable (the `CL` mod) |
| Mods | `NM`, `NF`, `DT`, `NC`, `HT`, `DC`, `HR`, `EZ`, `HD`, `FL`, `CL` |
| Accuracy | given as a value or as a hit breakdown (see [docs/ACCURACY.md](docs/ACCURACY.md)) |

## Why this, when rosu-pp exists

rosu-pp (checked on 4.0.2) has no `Reading` skill for osu!standard — the
attribute is `None` — although that skill arrived in the 2026 Q2 rebalance and
replaced the former AR and HD bonuses. So its formula is a pre-rebalance one,
and its numbers differ: on `diffcalc-test` it gives 6.6233★ against the
reference 6.5243★. It does have both scoring mechanics (`.lazer(false)` for
stable), so the disagreement holds in either mode. The official API is no help
either:
`BeatmapDifficultyAttributes` returns neither `reading_difficulty` nor
`flashlight_difficulty` nor `hit_circle_count`, and current pp cannot be
reconstructed without them.

## Accuracy of the calculation

The port is checked against the real C# code of osu!, not against published
values. Across every check against that reference the deviation stays within
the last bits of a `double`:

| Check | Volume | Result |
|---|---|---|
| Layered gates | 7 phases, from file parsing to pp for an arbitrary score | worst `3.8e-16` |
| Corpus comparison | 6900 beatmaps, 20 700 combinations | worst `5.8e-16` |
| Full scan | 231 780 files | no anomalies |

Both scoring mechanics are checked alike: 480 of the 1140 combinations in the
pp gate are classic, and half the mod sets in the corpus comparison are too.

Separately, the port was checked against the website itself — 495 real scores
from the osu! API that already have pp awarded. Median relative deviation
`1.4e-06`, maximum `1.1e-04`; 441 of the 495 matched character for character
after rounding to two decimals, which is the precision the API reports them
with. Four scores diverged further — the server holds its own star rating for
those beatmaps, different from a fresh calculation, and pp follows it; on the
same files the oracle agreed with the port bit for bit. That check is manual,
is not a gate, and needs osu! API credentials.

How this is arranged and what is left outside the scope —
[docs/ACCURACY.md](docs/ACCURACY.md). How the port relates to the C# original
and what to keep in mind at the next rebalance —
[docs/PORTING.md](docs/PORTING.md).

## Development

Two gates run right after cloning — what is in git is enough for them:

```bash
python3 tests/test_snapshot.py
python3 tests/test_public_api.py
```

The snapshot catches regressions (the calculation quietly drifted) but not
correctness: its values were taken from the port itself. The second gate holds
the public API contract described in this README. Correctness is proven by the
remaining gates, and those need the oracle — clones of `ppy/osu` and
`ppy/osu-tools` at pinned commits, plus .NET 8. Setting it up is described in
[oracle/PINNED.md](oracle/PINNED.md); after that:

```bash
python3 tests/run_all.py
```

The gates go bottom-up, and when something breaks the earliest failing one is
the one to look at.

Comments and docstrings inside the package are in Russian.

## License

[MIT](LICENSE).
