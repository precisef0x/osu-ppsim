# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [SemVer](https://semver.org/).

## 0.4.0 — 2026-10-06

### Added and changed

- **Beatmaps from memory:** `Simulator` and `pp_for_accuracy` accept raw `.osu`
  `bytes` or `bytearray`. New public `decode_beatmap_bytes` and
  `decode_beatmap_string` entry points share the existing decoder and BOM handling.
- **Per-object difficulty series:** `Simulator.object_difficulties`,
  `ObjectDifficulties`, and `calculate_difficulty_with_strains` expose aim,
  aim without sliders, speed, reading, and optional flashlight values aligned
  with playback times. Empty beatmaps return empty series.
- **Score validation is enabled by default:** `Simulator.score` raises
  `InvalidScoreError` for counter bounds and necessary combo contradictions.
  `max_combo=None` still means the beatmap's maximum, so scores with combo
  losses need an explicit combo. In lazer, losses include missed ticks,
  repeats and tails; under `CL`, nested fields have their bounds checked but
  otherwise remain ignored. New public `validate_score` supports both mechanics.
  `score(..., validate=False)` retains the previous unchecked calculation.
- Documented the validation guarantees, memory growth of retained series,
  and the comparison with rosu-pp-py without claiming that it releases the GIL.

### Verification

Calculation version remains **`20260706`**. All nine gates pass:

- 80 autonomous validation cases through both public entry points, including
  exact result agreement with validation enabled and disabled.
- 70 beatmap-and-mod combinations, 160 812 public series values including
  times; reading uses tolerance `1e-12`, the other series compare exactly.
- 1140 FC combinations against the oracle, worst relative deviation `3.798e-16`.
- 1428 arbitrary scores, 15 708 compared values, exact agreement with the oracle.
- 528 snapshot combinations across 22 edge-case beatmaps.

## 0.3.0 — 2026-08-10

### Added and changed

- **pp for an arbitrary score**, not just an FC: misses, dropped combo,
  unheld slider tails, missed ticks. New `Score` and `Simulator.score()`.
  `pp()` became a special case of `score()` rather than a separate code path,
  so the old FC gate stayed valid as a regression test and passed unchanged.
- **Miss estimation from the score total** for scores set in stable:
  `Score.legacy_total_score`. In stable a sliderbreak leaves no trace in the
  statistics — a score with no misses and combo below maximum could be a dozen
  dropped tails or three breaks in the middle. The score total tells them
  apart, because ScoreV1 multiplies every hit by the current combo multiplier,
  so the outcome depends on *how* the combo was broken and not merely on how
  much of it was lost. The two estimates differ by up to 10% pp on one and the
  same score.
- The decoder now parses breaks from `[Events]`: the ScoreV1 multiplier
  subtracts them from the beatmap length.
- New performance attributes, each of which the oracle prints as well:
  `effective_miss_count`, `combo_based_estimated_miss_count`,
  `score_based_estimated_miss_count`, `aim_estimated_slider_breaks`,
  `speed_estimated_slider_breaks`.
- Three new difficulty attributes: `legacy_score_base_multiplier`,
  `nested_score_per_object`, `maximum_legacy_combo_score`. Their sources
  differ, exactly as in C#: the multiplier is taken from the original beatmap,
  the other two from the playable one.
- Difficulty calculation is **17.8% faster**: 737 -> 606 ms across four
  beatmaps, measured by alternating five runs of each version so machine drift
  cancels out. Almost all of it came from one substitution — `min(max(x, lo),
  hi)` written as conditionals. A built-in call in Python costs more than the
  comparison itself, and there are over three hundred thousand such clamps per
  beatmap. The substitution is only safe for the clamp *chain*, where both
  comparisons are false on NaN and the value passes through, as `Math.Clamp`
  requires in .NET; bare `min(a, b)` calls were left alone, because the code
  deliberately relies on `min(nan, 1.0)` returning nan.

### Fixed

`drain_length` divided with Python's `//`, whereas C# truncates **toward
zero**. The difference goes negative when breaks are longer than the span
between the first and last object, and anywhere in the -999..-1 ms window the
two disagree: C# yields 0, Python -1.

From there the discrepancy grows instead of cancelling. In
`CalculateDifficultyPeppyStars` a zero length is a separate branch with
`ratio = 16`, while a negative one clamps to 0 — so the ScoreV1 multiplier
changes twofold, and since miss estimation from the score total rests on it,
**pp itself diverges**: on the probe the port returned 47.61 where the oracle
gives 41.74 — the understated miss estimate made the score look better than it
was. Only classic scores with a known score total, on beatmaps whose breaks
outlast the beatmap, are affected.

Same class of bug as the one `c_int_div` fixed, just a second place where
integer division can receive a negative dividend. Found by re-reading the diff;
no gate caught it.

### Verification against the oracle

Every number is compared against the real C# code, not against published
values.

| Check | Volume | Result |
|---|---|---|
| Layered gates | 7 phases, from file parsing to pp for an arbitrary score | worst `3.8e-16` |
| Phase 7 | 1428 scores, 15 708 values | `0.000e+00` |
| Corpus | 6900 beatmaps, 20 700 combinations | worst `5.8e-16` |
| Full scan | 231 780 files | no anomalies |

Both corpus checks were repeated on this release rather than carried over: the
clamp rewrite and the new `[Events]` parsing are exactly the kind of broad,
low-level change the gates alone have never been enough to clear.

The phase 7 gate also counts how many scores hit each branch and fails if any
of them never fired; score-total estimation fired in 756 of 1428. It gained a
sixth beatmap, `legacy-drain-underflow`, a synthetic probe aimed exactly at the
division bug above — and it was checked to go red on the reintroduced bug
before being added.

Mod sets `HRDT` and `CLHRDT` were added to the phase 5 gate. Under them the
`Reading` evaluator drifts by 1 ulp on `diffcalc-test` and that reaches pp,
so the worst deviation of phase 5 is now `3.798e-16` rather than zero. Hiding
a known deviation by dropping the mod set would be worse than keeping it
visible under a tolerance.

## 0.2.0 — 2026-07-28

- Mod `CL` (Classic): pp for a score set in stable, where accuracy counts hit
  circles only. Without it the scoring is lazer's.
- Under `CL` both readings of accuracy coincide, so `raw_accuracy` has no
  effect on the result.
- The mod changes neither difficulty nor `max_combo` — it only branches the
  accuracy computation.

Verification: pp gate — 1020 combinations (420 classic) at `0.000e+00`;
corpus — 600 beatmaps and 1800 combinations on a fresh seed, no mismatches,
worst deviation `2.1e-16`.

## 0.1.0 — 2026-07-28

First release.

- pp for an FC of an osu!standard beatmap at a given accuracy.
- Matches osu! calculation version `20260706` ("2026 Q2 SR & PP release"),
  taken from `ppy/osu @ 52461f1b` and `ppy/osu-tools @ f8bc6aa7`.
- Mods `NM`, `NF`, `DT`, `NC`, `HT`, `DC`, `HR`, `EZ`, `HD`, `FL`;
  anything else raises `UnsupportedModError`.
- No external dependencies.

Bit-for-bit against the reference: 6 layered gates, 15 000 combinations over
5000 corpus beatmaps, a full scan of 231 780 files with no anomalies.
