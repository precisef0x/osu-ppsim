# Porting: how it is built and what to watch at the next update

The target version is **`OsuDifficultyCalculator.Version = 20260706`**, the
"2026 Q2 SR & PP release" rebalance
([ppy/osu#37850](https://github.com/ppy/osu/pull/37850)).

## Key decisions

**The structure mirrors the C# one to one.** Files, classes and methods carry
the same names as in `ppy/osu`; each module header names its source and the
commit hash. This is not aesthetics but a maintenance strategy: ppy rebalances
roughly twice a year, and with a mirrored structure an update comes down to
reading a diff rather than doing archaeology.

**`float32` where, and only where, C# has it.** osu! keeps `Vector2` in
`float`, while all of the difficulty math runs in `double`. The `Vec2` class
rounds its components to float32 on every operation — on every step rather than
once at the end, because the intermediate values in C# are truncated too.

```python
_PACK_F32 = struct.Struct("<f").pack
_UNPACK_F32 = struct.Struct("<f").unpack

def f32(x: float) -> float:
    return _UNPACK_F32(_PACK_F32(x))[0]
```

Tens of thousands of calls per beatmap; about 22% of the total runtime goes
into this emulation, and without it the deviations accumulate on long beatmaps.

**`Erf`/`ErfInv` are ported from `DiffUtils` rather than taken from `math`.**
osu! uses one specific rational approximation; the standard implementation has
different low-order digits, and the deviation estimate in the pp formula
depends on them directly.

**The version is baked into the code.** `SUPPORTED_DIFFCALC_VERSION` is exposed
from the public API so that a consumer can check what exactly is being
computed.

## What maps to what

```
osu_ppsim/
  __init__.py            public API: Simulator, pp_for_accuracy, Score, Result
  f32.py                 Vec2 and f32 — single precision where osu! has it
  mods.py                mods: clock rate, HR/EZ difficulty adjustments
  beatmap/
    decoder.py           LegacyBeatmapDecoder + ConvertHitObjectParser
    objects.py           HitCircle / Slider / Spinner, PathType, nested objects
    difficulty_info.py   CS/AR/OD -> Scale, Radius, TimePreempt, hit windows
    path_approx.py       PathApproximator: bezier, Catmull, arc, linear
    slider_path.py       SliderPath: length, PositionAt, fit to ExpectedDistance
    slider_events.py     SliderEventGenerator: ticks, repeats, tail
    defaults.py          ApplyDefaults: scale, preempt, slider geometry
    processor.py         OsuBeatmapProcessor: stacking (new and old-style), MaxCombo
  difficulty/
    hitobject.py         OsuDifficultyHitObject
    utils.py             DiffUtils
    legacy_score.py      LegacyScoreUtils + OsuLegacyScoreSimulator (combo score)
    skills/              Strain / VariableLengthStrain / Harmonic + Aim, Speed, Reading, Flashlight
    evaluators/          snap, flow, agility (Aim), speed, rhythm, reading, flashlight
    calculator.py        OsuDifficultyCalculator -> OsuDifficultyAttributes,
                         plus ObjectDifficulties (Skill.ObjectDifficulties per skill)
  performance/
    accuracy.py          accuracy -> a 300/100/50 breakdown (from osu-tools)
    legacy_miss.py       OsuLegacyScoreMissCalculator
    calculator.py        OsuPerformanceCalculator
```

The single deliberate departure from mirroring: `PERFORMANCE_BASE_MULTIPLIER`,
`PERFORMANCE_NORM_EXPONENT` and `aim_difficulty_to_performance` live in
`OsuPerformanceCalculator` in C#, and in `difficulty/calculator.py` here.
Otherwise there would be a circular import: the difficulty calculator reads
those constants, and the performance calculator reads difficulty attributes.

## Scope

osu!standard only. Both scoring mechanics are supported — lazer's and classic
(the `CL` mod) — and so is any score, not only an FC: misses, dropped combo,
unheld slider tails, missed ticks, and classic scores with a known ScoreV1
total, for which misses are estimated from that total.

Deliberately left outside: mods beyond the supported list, the other rulesets,
and `CalculateTimed`.

Releases 0.1.0 and 0.2.0 covered FCs only, and several sections below were
written under that scope — they are marked where it matters.

## Updating after the next rebalance

1. Update the pins in [oracle/PINNED.md](../oracle/PINNED.md), rebuild the oracle.
2. Regenerate fixtures: `python3 tools/oracle.py fixtures`.
3. Run the gates: `python3 tests/run_all.py`.
4. Fix whatever went red — bottom-up, starting from the earliest failing phase.
5. Run the corpus through both checks (see [ACCURACY.md](ACCURACY.md)).

The "Pitfalls" section below is worth re-reading first: it collects everything
that has already cost time once.

---

# Porting pitfalls

Concrete ones, verified on real data. Every one of them would have quietly
spoiled the result.

**Division by zero.** In C# `x / 0.0` yields `Infinity`; in Python it is a
`ZeroDivisionError`. Not an abstraction: on the `nan-slider` beatmap the
`tick_distance` of the fifth object really is `Infinity`, and the tick
generator has to digest that and emit zero ticks. The resulting star rating is
still meaningful (0.8705817579435355). The port must reproduce that behaviour
rather than "fix" it — hence also the requirement on fixture comparison: `NaN`
and `Infinity` are stored in the dump as strings rather than replaced with
`null`.

**`StrainTime` was renamed to `AdjustedDeltaTime`.** That is part of the 2026
rebalance. In rosu-pp the field is still called `strain_time` — a handy marker
of which version somebody else's code sits on.

**`GetCurrentStrainPeaks()` mutates state.** On `VariableLengthStrainSkill` it
lazily seals the last section and appends it to `strainPeaks`. Calling it
mid-processing closes a section early and poisons everything downstream. So
peaks may only be taken after `Calculate()` has finished, and for the same
reason a cumulative `DifficultyValue()` curve over objects cannot be taken —
section peaks and per-object `ObjectDifficulties` are used to localise
discrepancies instead.

That constraint is also why `ObjectDifficulties`, and not the section peaks, is
what the library exposes publicly as `Simulator.object_difficulties`. Of the
four skills only `Flashlight` still has fixed-length sections; `Aim` keeps its
peaks sorted by magnitude rather than by time, and `Speed` and `Reading` are
`HarmonicSkill` and have no sections at all. The per-object list is the only
series that exists for every skill, is ordered by time, and survives a rebalance
that reshuffles the accumulation schemes.

**Checking mods gives independent confirmation of the analysis.** HD moves only
`reading_difficulty` (1.780 → 2.211), FL only `flashlight_difficulty`, and
together they move both (0.998 → 1.299), because `FlashlightEvaluator` accounts
for opacity and `hidden_bonus`. There is no separate HD bonus in the pp
formula.

### Found in phase 1

**Order of operations beats algebra.** `Duration` in C# is
`EndTime - StartTime`, where `EndTime = StartTime + SpanCount * Distance /
Velocity`. Algebraically that is the same as computing the duration directly;
in floating point it is not: with a `start_time` in the tens of thousands of
milliseconds the subtraction eats the low-order digits. All 674 sliders on
`801165` diverged until the order was reproduced literally.

**osuTK normalises by multiplying by the reciprocal length**, not by dividing:

```csharp
float scale = 1.0f / Length;
X *= scale; Y *= scale;
```

`x * (1/L)` is not equal to `x / L`, and the difference carries into the last
point of the path when fitting to `ExpectedDistance`. Caught on 90 nested
objects out of 1829.

**`0.7f` promoted to double equals 0.699999988079071.** In the scale formula
`0.7f * DifficultyRange(cs)` the float is promoted to double exactly like that,
not to 0.7.

**Comparing against fixtures requires knowing each field's precision.** The
dump writes osu!'s `float` fields as float32: the literal `283.402` in JSON is
really `283.4020080566406`. Python reads it as a double, and a direct
comparison produces false mismatches. So `tests/compare.py` records a precision
for every field, and float32 fields are coerced to float32 on both sides. The
first run of the gate reported 938 "mismatches", of which 90 were real.

### Found in phase 2

**Float constants must not be computed in double.** C# says
`NORMALISED_RADIUS * 2.4f`, where the multiplier is **already** float32.
Computing `50 * 2.4` in double and rounding the result is not the same thing:

| | value |
|---|---|
| `f32(50 * 2.4)` (wrong) | `120.0` |
| `f32(50 * f32(2.4))` (right) | `120.00000762939453` |

A difference of 7.6e-06 carries straight into `MinimumJumpDistance` and is
caught on 15 of the 123 objects on `diffcalc-test`. The rule is simple: if a
literal in C# has the `f` suffix, rounding to float32 happens **before** the
arithmetic, not after.

**`DiffUtils.Pow` has two overloads, and the compiler picks by literal type.**

```csharp
Pow(x, 0.3)  // -> Pow(double, double) -> Math.Pow
Pow(x, 5)    // -> Pow(double, int)    -> x*x*x*x*x by repeated multiplication
```

For exponents 0–5 the integer version multiplies by hand, and the result
differs from `Math.Pow` in the low-order digits. In
`osu_ppsim/difficulty/utils.py` these are two distinct functions, and at every
call site one has to look at the source to see which one fired.

**The variable's type decides where the rounding happens.** In
`MinimumJumpDistance = Max(0, Min(LazyJumpDistance - delta, tailJumpDistance - maximum_slider_radius))`
the left argument is a double (a promoted float) while the right one is
`float - float`, so that subtraction runs in float32. One has to look at each
variable's declaration, not at the shape of the expression.

### Found in phase 3

**The built-in `sum()` cannot be used.** Since Python 3.12 it applies Neumaier
compensated summation and produces a result **more accurate** than
`Enumerable.Sum` in C#, which simply adds in order:

```
sum(peaks)        -> 286.9632310019827   (matches math.fsum)
explicit loop     -> 286.9632310019824   <- as in C#
```

The difference is in the last digits, but it is real: on `diffcalc-test` under
FL the final difficulty diverged in the 17th digit. `sequential_sum` was added
to `osu_ppsim/difficulty/utils.py`, and every summation goes through it. This
is the rare case where the more accurate implementation is the wrong one.

**osu! has its own `ErfInv` and `Erf`.** `Erf` is the Abramowitz and Stegun
approximation (formula 7.1.26) and differs from `math.erf` by roughly 1e-07.
The deviation estimate in the pp formula depends on those digits directly, so
the standard one cannot be used.

**The HD mod changes data, not just visuals.** `OsuModHidden.ApplyToBeatmap`
rewrites `TimeFadeIn` on every object **except sliders**:

```csharp
if (osuObject is not Slider)
    osuObject.TimeFadeIn = osuObject.TimePreempt * FADE_IN_DURATION_MULTIPLIER;  // 0.4
```

`OpacityAt` takes the start of the fade from there, so under HD opacity is
computed differently — and the whole Reading skill with it. The hint was in the
code itself: the comment "equal to TimeFadeIn **minus any adjustments from the
HD mod**" explains why the fade-in duration is recomputed by hand there while
the start of the fade is not.

That mistake cost 1569 mismatches on `801165` under HD. It also exposed a hole
in the phase 2 gate: the test only ran NM and DT, where `TimeFadeIn` does not
change, so the branch was never exercised at all. HD and HDDT are in the set
now.

**The evaluators are declared `public static`.** The dump calls them directly
and writes per-object values before any strain accumulates. Without that there
is no way to localise a discrepancy inside a skill: only the accumulated result
is visible, where the error is smeared across every subsequent object.

**The `StrainPeak` constructor rounds the section length.**

```csharp
public StrainPeak(double value, double sectionLength)
{
    Value = value;
    SectionLength = Math.Round(sectionLength);   // ← easy to miss
}
```

Under rate mods the times are fractional, and without the rounding the lengths
diverge: 190.667 against 191. It only shows with DT — on NM the times are whole
and the rounding is invisible. A separate subtlety: the `totalLength`
accumulator adds the **raw** length but subtracts the rounded one.

**`List<T>.BinarySearch` returns an arbitrary match among equal elements**,
whereas `bisect_left` returns the leftmost. The order of equal peaks in
`AddInPlace` depends on it. Right now it only shows on zero strains, which are
filtered out anyway, so `base.py` reproduces .NET's algorithm exactly: on
non-zero equal values the difference would land straight in the difficulty.

### Found in phase 5

**The HR mod flips the beatmap vertically.** Besides raising CS/AR/OD,
`OsuModHardRock` implements `IApplicableToHitObject`:

```csharp
osuObject.Position = new Vector2(osuObject.Position.X, OsuPlayfield.BASE_SIZE.Y - osuObject.Y);
// sliders additionally: control points are reflected as y -> -y
```

The reflection changes distances and angles, so aim and reading diverge while
speed does not — it depends only on time. That trail is how the bug was found:
`speed_difficulty` matched exactly while everything else drifted by 1e-5.

The order in which mods are applied in `WorkingBeatmap.GetPlayableBeatmap`
turned out to matter:

1. `PreProcess` — forced new combo;
2. `ApplyDefaults` — scale, preempt, geometry, nested objects;
3. `IApplicableToHitObject` — **the HR reflection**;
4. `PostProcess` — stacking;
5. `IApplicableToBeatmap` — **the TimeFadeIn adjustment under HD**.

So the HD adjustment is applied after preempt has already been computed, and
the HR reflection before stacking. The port performs the reflection earlier
still, before geometry is computed, and that is equivalent: the reflection is
an isometry, so path length and all timings are unaffected.

**Two different accuracies.** Under lazer accuracy is not counted over hit
circles alone: slider tails enter with weight 3, ticks with weight 0.6.

```
acc = (6*300 + 2*100 + 50 + 3*tails + 0.6*ticks) / (6*n + 3*sliders + 0.6*ticks)
```

On an FC the tails and ticks are all collected, so they pull the accuracy up.
osu-tools takes the "raw" accuracy over circles in its `-a` option, while the
player sees the final one. The library reads accuracy the way the player sees
it by default and converts it internally; the `raw_accuracy=True` flag switches
to osu-tools' behaviour and is needed for comparing against the reference.

### Found by testing outside the training sample

The port had been declared finished: 13 beatmaps, 300 combinations, pp matching
bit for bit. A run over four **real beatmaps that had never taken part in
debugging** found two genuine bugs. Both of the same class as all the previous
ones: rounding in the wrong place.

**The number of arc approximation points was computed in double.** C# says
`1f - (0.1f / Radius)`: both the tolerance and the radius are `float`, so the
division and the subtraction run in float32 and only then widen for
`Math.Acos`.

| | 1 − tol/r | points |
|---|---|---|
| in double (wrong) | `0.998791740951879` | 12 |
| in float32 (right) | `0.9987917542457581` | 13 |

The extra point changes the shape of the polyline and shifts the end of the
path. A star rating discrepancy of 1e-7 — three orders of magnitude above
anything the known deviations produced.

**The stack offset was rounded once instead of twice.** In C#
`new Vector2(StackHeight * Scale * -6.4f)` is two consecutive float32
multiplications, not one expression in double. It only surfaces at a large
scale, i.e. under EZ: a 1e-10 discrepancy on two beatmaps out of four.

**The lesson about method.** Neither bug would have been found by adding one
more mod or one more synthetic beatmap: both require specific geometry (an arc
of the right radius, a stack at a particular CS). ppy's test set is chosen for
their own edge cases, not for somebody else's port. The real beatmaps stayed in
the set for good — `REAL_WORLD_BEATMAPS` in `tests/reference.py`.

It also turned out that the phase 2 and 3 gates were assembling the beatmap
themselves and had drifted from the production path: the HR and EZ adjustments
to CS/AR/OD were applied in only one of the two places. Preparation was
extracted into a shared `prepare_beatmap`.

### Found on the real corpus (231 thousand beatmaps)

Running random samples from the full beatmap corpus found four more bugs —
after the port already passed 540 combinations bit for bit.

**Not one test beatmap contained a Catmull spline.** Not a single segment
across 17 beatmaps: only Linear, PerfectCurve and BSpline. In
`_catmull_find_point` the rounding was only on the products, whereas in C# the
whole expression consists of float operands and **every** binary operation is
rounded.

**A spinner's position does not come from the file.**
`createSpinner(new Vector2(512, 384) / 2, ...)` always places it at the centre
of the playfield. Some older beatmaps have different coordinates written in the
file. In the test beatmaps the spinners happened to sit at the centre anyway.

**Division by zero on a zero-duration slider.** `ZeroDivisionError` instead of
`Infinity`. `c_div` was added with C#'s semantics; further down the chain the
infinity turns into a NaN, and `PositionAt(NaN)` returns the first point of the
path — the result matches.

**The oracle was configured wrongly for old beatmaps.** The most instructive of
the four. osu-tools calls, in its own `Program.cs`,

```csharp
LegacyDifficultyCalculatorBeatmapDecoder.Register();
```

That decoder differs in having `ApplyOffsets = false`: beatmaps in formats
below v5 are **not** shifted by 24 ms. My dump had its own `Main` and skipped
the registration, so it did apply the shift — and the port had been fitted to a
wrong reference.

The symptom was extremely narrow: only `flashlight_difficulty` diverged. A
uniform time shift changes neither deltas nor distances nor angles, but
`Flashlight` is the only skill whose sections are pinned to absolute multiples
of 400 ms. Objects get redistributed across sections, and the peaks change.

This survived to the real corpus because the single v3-format beatmap
(`old-stacking`) sat in `GEOMETRY_EDGE_CASES`, and the phase 5 gate — the one
comparing against `simulate` — does not iterate that list. The dump and
`simulate` disagreed with each other, and no gate checked that. The beatmap is
in the main set now, and both oracle paths are compared against each other.

### Found by a code audit (2026-07-28)

Reading the whole port against the pinned C# found **nine** discrepancies — all
in the decoder, none in the evaluators, skills or the pp formula. Not one
showed up on the 17 test beatmaps or on 500 corpus beatmaps: they all require
inputs that simply are not there. Each is confirmed by a synthetic beatmap; all
nine live in `tests/edge_cases/` and are checked by the phase 6 gate.

The first four share one cause: **osu! clamps values rather than trusting
them.**

| What | Where | Cost of the bug |
|---|---|---|
| Difficulty settings were not clamped to valid ranges (`applyDifficultyRestrictions`) | HP/CS/OD/AR in [0,10], SliderMultiplier in [0.4,3.6], TickRate in [0.5,8] | CS=12 gave a star rating **five times** higher |
| `beatLength` was not clamped to [6, 60000] (`BeatLengthBindable`) | `TimingPoint` | slider velocity three times higher; a crash at `beatLength=0` |
| `SliderVelocity` was not clamped to [0.1, 10] (`SliderVelocityBindable`) | `DifficultyPoint` | a different tick count, hence a different MaxCombo |
| Coordinates and length were not checked against `Parsing.MAX_COORDINATE_VALUE` | `_parse_float` | the port computed beatmaps osu! rejects |

The other five each stand alone:

**A spurious `f32` in the tick multiplier.** C# says `1f / SliderVelocity`, and
a comment in the port confidently explained that this was a float32 division.
But `SliderVelocity` is declared `double`, so the one widens and the division
runs in double. Exactly the same trap as everywhere else in the port, only
inverted: here rounding was missing and got added.

**Old-style stacking took the wrong end of the slider.** C# takes literally
`Path.PositionAt(1)`, while the port substituted `EndPosition`, i.e.
`PositionAt(SpanCount % 2)`. On a slider with an even number of spans those are
opposite ends of the path.

**An object's position was not truncated to an integer.** Before format v128
both the path points and the object's own position are truncated. The port
truncated only the former.

**The two kinds of control point have different fallbacks.** Before the first
point, `TimingPointAt` returns that very first point while `DifficultyPointAt`
returns a default with velocity 1. The port used a shared helper and returned
the first point for both.

**Priority among simultaneous points.** The least obvious one.
`addControlPoint` accumulates points until the time changes, then flushes the
group from the end of the list, keeping the first one encountered of each type.
Red lines are put at the front of the list and green ones at the back, from
which it follows: the tempo comes from the **first red** line of the group and
the slider velocity from the **last green** one, falling back to the first red
only in its absence. That is, a green line beats a red one with the same time
even if it is written earlier in the file.

**The tenth was found not by reading but by a fresh sample seed.** After all
nine fixes, a corpus run on a new seed produced a `1.07e-09` discrepancy on
beatmap 35995 — three orders of magnitude above the known ulp residue, i.e. a
real bug. The cause:

```csharp
public override Vector2 StackOffset => Vector2.Zero;   // Spinner.cs
```

A spinner is not displaced by stacking, and the port applied the general
formula to it. The condition is doubly rare: new-style stacking skips spinners,
so a format below v6 is needed, where `applyStackingOld` gives the spinner a
non-zero `StackHeight` following a slider; and the spinner's position has to be
read by somebody — the `Aim` evaluators take it as a neighbour's position when
computing overlap.

The synthetic probe for it had to be built twice. The first reproduced the
stack (`StackHeight = -1`) but did not change the star rating:
`_calculate_overlap_factor` saturates to one for anything closer than the
radius, and the neighbours were right on top of each other. The probe was saved
only because I tested it for sensitivity — temporarily reintroduced the bug and
confirmed the probe went red. **A probe not checked against a negative result
guarantees nothing**; the second version places the neighbours at a distance on
the order of the radius, where the overlap factor is sensitive, and catches a
`4.9e-05` discrepancy.

**The eleventh closed the "known residue".** After the spinner fix, comparing
attributes across forty beatmaps showed one with a `5.6e-13` discrepancy in
`aim_difficulty` — thousands of ulp, so not noise. A layered comparison led to
`SnapAimEvaluator`, into the rare "back-and-forth through one point" branch:

```csharp
float distance = (last2BaseObject.StackedPosition - lastBaseObject.StackedPosition).Length;
if (distance < 1)
    wideAngleBonus *= 1 - 0.55 * (1 - distance);
```

`distance` is declared `float`, so the bracket `(1 - distance)` is computed in
**float32** and only the multiplication by `0.55` goes to double. The port
computed the whole bracket in double. The branch requires two objects one apart
to be less than a pixel apart while the angle is wide — hence it never came up.

This is the last of the "where does C# round" pitfalls, and it also closed a
residue that had been considered irreducible: a 500-beatmap corpus run on the
original seed now gives **0 mismatches** at a worst deviation of `2.1e-16`,
instead of the previous five at `1.6e-12`. The analysis below ("The Reading
residue") still holds at the evaluator level, but the residue no longer reaches
the final pp or star rating.

**The twelfth was found by a two-thousand-beatmap sample** — and it is the
first that lives not in the difficulty calculation but in the hit breakdown.
The symptom differed from all the previous ones: the star rating matched
**exactly** while pp diverged by 0.4%. Two beatmaps out of six thousand
combinations, both only at 95%.

The cause is in `OsuSimulateCommand.generateHitResults`:

```csharp
int relevantResultCount = totalResultCount - countMiss;
double relevantAccuracy = accuracy * totalResultCount / relevantResultCount;
```

On an FC there are no misses, the denominator equals the numerator, and the
port skipped that round trip as an identity. In floating point it is not an
identity: `0.95 * 543 / 543` gives `0.9500000000000001`. Usually that decides
nothing, but on 543 objects the estimated number of 100s lands **exactly** on
`40.5` — and the last-bit difference is enough to send banker's rounding from
40 to 41. One 100 turns into a 50, and that is already 0.4% pp.

The moral is the same as with `Duration = EndTime - StartTime`: **an
algebraically redundant operation must not be elided** if the reference
performs it. The probe is built on a 21-object beatmap — the smallest count at
which the estimate again lands exactly on a half (4.5 at 85%).

Separately: `_index_of_distance` used `bisect_left`, and its docstring
justified that by the cumulative lengths being strictly increasing. They are
not strictly increasing — the length repeats at coincident path points, which
is routine for Catmull. Experiment showed the result did not change on the
remaining corpus beatmaps, but the justification was wrong, and right next
door, in `_add_in_place`, .NET's `BinarySearch` had already been reproduced
honestly. Now it is reproduced in both places.

### Second audit (2026-07-28): degenerate inputs

The first audit read the code against C# and looked at ordinary beatmaps. The
second walked the boundary of the scope: 31 synthetic beatmaps for inputs that
appear neither in the gates nor in the corpus. Three discrepancies turned up —
all beyond what occurs in practice (scanning 12 952 std beatmaps of the corpus
gives **zero** hits for each of the three), but each breaks the calculation
outright rather than in the low-order digits.

**Thirteenth. A zero-length slider kept its repeats.**

```csharp
if (Precision.AlmostEquals(path.Distance, 0))
{
    repeatCount = 0;
    nodeSamples = [nodeSamples[0], nodeSamples[^1]];
}
```

This is not an optimisation but a guard against an exploit: ppy's comment names
the ranked set `1258033`, where such a slider inflated combo — in stable its
repeats were created as objects but never judged. MaxCombo came out too high
(13 against 10 on the probe, 59 against 10 with fifty repeats), and the extra
`SliderRepeat`s also landed in the tick count that lazer accuracy is computed
from.

The `Precision.AlmostEquals` threshold had to be established by experiment,
because osu-framework's sources are not in the pin: the oracle treats a slider
of length exactly `1e-7` as zero, and one of `1.1e-7` as not. So the comparison
is **non-strict**, `<=`. The same comparison was fixed in
`SliderPath._interpolate_vertices`, where it read `<`.

In C# the reset happens during parsing, but the path is built there too; here
the path appears in `apply_defaults`, so the reset lives there.

**Fourteenth. A beatmap without a single speed note crashed the calculation.**

`OsuPerformanceCalculator.computeSpeedValue` divides by `SpeedDifficulty`:

```csharp
double effectiveHitWindow = 20 * DiffUtils.Pow(4 / attributes.SpeedDifficulty, 0.35);
```

At zero difficulty C# gets an infinite window, `Erf(inf) = 1`, a multiplier with
no effect — and an honest zero as the result. Python raised
`ZeroDivisionError`. A beatmap of one object or of spinners only is enough; the
oracle answers sensibly on both (a lone circle gives `pp = 11.4677`, not zero).
Cured by `c_div`.

Same class as `_progress_to_distance` with NaN: **where C# continues with an
infinity, Python falls over.** It is worth checking every division whose
denominator can become zero on a degenerate beatmap.

**Fifteenth. Coordinates were truncated to integers through a double rather
than a float32.**

```csharp
: new Vector2((int)Parsing.ParseFloat(split[0], ...), (int)Parsing.ParseFloat(split[1], ...));
```

`ParseFloat` returns a `float`, and that is what gets truncated. The port
parsed into a double and truncated that. For `255.999995` that is 255 instead
of 256: a value within one ulp of an integer is rounded to `256.0f` during
parsing already. A probe on eight circles gives a star rating of `1.850948`
against `1.850359` — a `3.2e-4` discrepancy, not low-order digits. It affects
every format below v128, i.e. practically every beatmap.

**Also fixed in the same pass (not discrepancies):**

* `classifiers` in `pyproject.toml` sat after the
  `[project.optional-dependencies]` heading and therefore ended up inside it:
  the package had no classifiers at all, but did have a bogus `[classifiers]`
  extra, installing which would send pip looking for a package named
  `Programming Language :: Python :: 3.11`. **In TOML, everything belonging to
  a section must sit above the first nested heading.**
* `BeatmapParseError` was not exported from the package root, although it is
  one of the two public API exceptions (`sweep.py` pulled it from an internal
  module).
* `tools/sweep.py` did not catch failures inside `simulator.pp` — a run over
  thousands of beatmaps would abort entirely instead of recording a mismatch.
  That is exactly what the fourteenth finding would have caused.
* `processor._end_position` repeated the `end_position` property word for word;
  C# has the property itself in those places.
* The zero-velocity branch in `Slider.end_time` became unreachable after the
  decoder clamps — removed, along with the `math` import.
* `apply_to_difficulty` worked through a `dict[str, float]`: replaced with
  `Difficulty`, which removed eight lines of packing and unpacking from
  `prepare_beatmap`.
* Three `assert ... is not None` narrowing statements were dropped: they vanish
  under `-O`, and the justification is better expressed by the structure of the
  code and a comment.

### Sixteenth: a broken line rejected the whole beatmap

Found by a comparison over 5000 beatmaps — and not by a mismatch but by the
summary line `'failed to parse': 1`, which had not appeared in earlier runs.

`LegacyDecoder.ParseStreamInto` wraps the parsing of **each line**:

```csharp
try { ParseLine(output, section, line, isPrimaryStream); }
catch (Exception e) { Logger.Log($"Failed to process line \"{line}\" into \"{output}\": {e.Message}"); }
```

That is, an unusable line is skipped and parsing continues. The port, however,
raised `BeatmapParseError` and gave up on the beatmap entirely. On `2052199`
that is sixteen sliders of length `141975` against a limit of `131072`: osu!
loses sixteen objects out of 826 and produces 6.126★ / 392 pp, while the port
produced nothing.

Exactly two things remain fatal, and both are checked outside the loop: a
missing format line (read by `Decoder.GetDecoder`, outside the try) and a
ruleset other than osu!standard — which is our scope, not C#'s behaviour.

The ordering inside `handleTimingPoint` was checked separately: the `throw` on
NaN comes **before** the first `addControlPoint`, so skipping a line adds
nothing to the accumulated group — which is what our `continue` does.

A `Beatmap.unparsed_lines` field was added. Silently losing objects is exactly
the failure mode this whole project stands against: a counter does not make the
result more correct, but it stops a broken file from looking intact.

**Separately, on why the finding waited so long.** `sweep.py` counted a parse
failure as a *skip* rather than a mismatch, and three earlier runs (500, 400,
2000 beatmaps) simply never met such a beatmap, so the counter sat at zero the
whole time and never caught the eye. Now, on a parse failure, sweep asks the
oracle: if the oracle accepts the beatmap, that is a mismatch. **A "skipped"
category in any comparison tool must be either empty or explained** — otherwise
it hides exactly what you are looking for.

Verifying the fix did not require repeating the whole corpus: the change is a
pure widening and by construction does not touch beatmaps without broken lines.
Every beatmap in the sample with `unparsed_lines > 0` (two out of 20 000 files)
was checked across four mod sets and three accuracies — 24 combinations, no
mismatches.

### A full corpus run without the oracle (231 778 files)

Random samples check whether the numbers agree. A full scan answers something
else: **what the port refuses to parse, and where it falls over while C#
carries on.** The oracle is not needed for that, which is why it is feasible in
full — 70 ms per beatmap, six processes, 45 minutes.

First run: 150 795 computed, 80 983 not std, **two** anomalies. Behind those two
lines were **three** bugs — one line in a summary can easily hide several
distinct defects.

Separately, on how I nearly lost the nineteenth. The first anomaly —
`191276.osu`, "no osu file format vN line found" — I filed as a legitimate
rejection without checking. Checking took one command and showed the opposite:
the oracle accepts the beatmap. **The category "legitimate rejection" cannot be
assigned by the look of the message; it has to be confirmed by the oracle just
like a mismatch.**

Second run on the fixed code: 231 780 files, 150 796 computed, 80 984 not std,
zero anomalies, 30 beatmaps parsed while skipping broken lines.

**Seventeenth. An arc of a radius in the millions divided by zero.**

```csharp
int subPoints = (2f * Radius <= 0.1f) ? 2 : Math.Max(2, (int)Math.Ceiling(ThetaRange / (2.0 * Math.Acos(1f - (0.1f / Radius)))));
```

At a radius above ~3.4 million, `0.1f/Radius` falls below the ulp of one
(2⁻²⁴ ≈ 5.96e-8), `1f - …` yields exactly `1.0f`, and `Acos(1)` is zero.

C# divides by zero, gets an infinity, and the `(int)` cast **saturates** it to
`int.MaxValue`; the 1000-point threshold then sends the arc to bezier. That is
.NET Core 3.0+ behaviour: the float→int conversion became saturating rather
than undefined. I did not check that from memory — I ran both variants,
`int.MaxValue` and the old `int.MinValue`, against the oracle, and exactly the
first one matched. A zero `ThetaRange` would give `NaN`, and `(int)NaN` is 0,
which `Math.Max` turns into 2; written out explicitly.

**Eighteenth. An empty path segment: the port was MORE PERMISSIVE than the
original.**

Not found on its own: after the seventeenth was fixed the beatmap stopped
crashing, but the combo diverged — 938 against the oracle's 929.

The slider begins with `D|I|C|K|S|B|82:226|…` — six letter tokens in a row,
each opening a segment from the same position, so segments two through six come
out empty. C# writes `vertices[0].Type = type` with no check at all, catches an
`IndexOutOfRange`, and `ParseStreamInto` skips the line entirely. The port
neatly returned an empty list and kept the slider — along with nine nested
objects osu! does not have.

**Being more permissive than the original is as much a bug as being stricter.**
The sixteenth finding was about excessive strictness, the eighteenth about
excessive leniency, and both change combo. Whenever the port "neatly handles" a
case on which C# crashes, one has to look at what the caller does with that
crash: here it means losing an object, not a harmless guard.

Noticed separately and **deliberately not reproduced**: `convertPathString`
takes `pointsBuffer[endIndex]` from an array rented from `ArrayPool`, and with
two letter tokens at the end of the string (`L|1:1|B|C`) it reads a cell beyond
the filled region, i.e. garbage from a previous tenant. There is no way to
reproduce a non-deterministic read; the port substitutes `None`. No such path
occurred in the corpus.

**Nineteenth. A UTF-16 file was rejected outright.** That very "first anomaly",
`191276.osu`: the beatmap is saved in UTF-16 LE, the port read it as UTF-8 and
could not even find the format line — that is, it rejected a beatmap osu!
computes without a single complaint. C# opens the file through a `StreamReader`
with defaults, and that has `detectEncodingFromByteOrderMarks = true`: UTF-16
and UTF-32 are recognised by the byte order mark. The checks and their order
are reproduced from `StreamReader.DetectEncoding`; the probe is
`utf16-beatmap.osu` in `tests/edge_cases/`.

### The Classic mod: what was left out at first, and why

*Written for the 0.2.0 scope, which covered FCs only. Release 0.3.0 implements
all five branches; the reasoning is kept because it shows how the boundary was
established rather than assumed.*

Porting `CL` was the inverse of every section above: the search was not for a
bug but for a boundary. In `OsuPerformanceCalculator` the
`usingClassicSliderAccuracy` flag branches the calculation in five places, and
it was tempting to carry all five over. On an FC exactly **one** stays alive:

| Branch | Fate on an FC |
|---|---|
| `OsuLegacyScoreMissCalculator` | unreachable: needs `LegacyTotalScore`, which a simulated score does not have |
| classic combo-based miss estimate | `fullComboThreshold` is at most `MaxCombo`, and combo equals it → the estimate is zero |
| `calculateEstimatedSliderBreaks` | gated behind `effectiveMissCount > 0` |
| classic aim nerf for sliders | the estimate `Min(imperfect, MaxCombo − combo)` = 0, so the multiplier equals lazer's |
| **which objects carry accuracy** | **alive: under CL only hit circles enter the accuracy component, without sliders** |

The practical consequence at the time: along with the second branch the legacy
attributes `Aim/SpeedTopWeightedSliderFactor` were not needed either — outside
it they are read nowhere. Three attributes and hundreds of lines of C# went
unported not because they were "hard" but because they provably could not
affect the result within the declared scope.

The other half of the difference lies not in the formula but in the semantics
of the input: under CL `GenerateHitResults` **does not put** slider tails or
ticks into the statistics, so `GetAccuracy` adds up hit circles alone. Hence a
consequence worth remembering: under CL the raw and the displayed accuracy
coincide, and the `raw_accuracy` flag means nothing.

**Twentieth finding: a redundant multiply-divide round trip, this time our
own.** Converting the "displayed" accuracy into the "raw" one is not a mirror
of C# but our own convenience layer, and under CL it is algebraically an
identity: there are no tails or ticks, so `(acc * 6n − 0) / 6n`. The temptation
to drop the `classic` branch from the condition was immediate — and it was a
mistake. Algebraically an identity, that conversion adds a **second**
multiply-divide round trip to the one the breakdown itself performs, while the
oracle performs exactly one. The twelfth finding again, only now the port could
have inflicted it on itself, out of nothing.

The difference is not theoretical: a sweep found three inputs within 400
objects where the breakdown diverges — for instance 181 objects at 95%, where a
100 turns into a 50. In pp that is 1%: `33.0047` against `32.6493`, with the
oracle giving the former. The probe is `cl-acc-roundtrip.osu`.

**And a lesson about the probe itself, worth more than the finding.** The first
version of the probe was wired into the phase 6 gate, which calls the
calculation with `raw_accuracy=True`. Under that flag the accuracy conversion
is not invoked at all — so the probe was not testing the very thing it was
created for, and with the guard removed the gate stayed **green**. Only the
mandatory negative-result check caught that. Classic probes now run in both
readings, and the "under CL raw_accuracy has no effect" check in the public API
gate was moved from a beatmap where the equality holds identically to one where
it could break.

The rule behind this is broader than the Classic mod: **a probe must pass
through the code it is testing.** A beatmap chosen for a bug guarantees nothing
if the gate calls the calculation down a branch where the bug is absent.

**The mod does not touch difficulty, and that is verified by code rather than
by measurement alone.** Every occurrence of `ClassicSliderBehaviour` is in
`CreateJudgement`: what changes is the judgement types, not the geometry or the
set of nested objects. Combo is preserved for a non-obvious reason — the
contribution **migrates**: the slider tail gets a `LegacyTailJudgement` with
`SmallTickHit`, which gives no combo, while the slider itself gets an
`OsuJudgement` instead of an `OsuIgnoreJudgement`, which does. The sum is the
same, so `MaxCombo` under CL equals lazer's — but not identically, only up to
that compensation.

### The Reading residue

The `Reading` evaluator deviates from the reference by 1–4 ulp on roughly 2–20%
of objects. The other six evaluators and all four skills match exactly.
Checked and ruled out: degree-to-radian conversion, summation order, a lost
digit in `Norm`. The most likely cause is a difference between the
implementations of `Math.Pow` in .NET and `math.pow` in CPython on particular
inputs.

**Phase 4 showed what this amounts to: nothing.** Of the seventeen attributes
exactly one is affected — `reading_difficult_note_count`, by 1 ulp (relative
error 2.2e-16), and it is the gate's single entry in `KNOWN_ULP_DRIFT`. Both
`reading_difficulty` and `star_rating` match exactly across all 70 combinations
of beatmaps and mods.

Moreover, that attribute appears in the pp formula in exactly one place:

```csharp
if (effectiveMissCount > 0)
    readingValue *= calculateMissPenalty(..., attributes.ReadingDifficultNoteCount);
```

On an FC `effectiveMissCount` is zero, so the branch never executes. On scores
with misses the attribute does participate — and the 1 ulp deviation with it. A
sensitivity measurement: a 1 ulp shift in `difficultStrainCount` moves the miss
penalty by at most 1–2 ulp (usually not at all) at values from single digits to
hundreds. The only dangerous range is within ~1e-7 of one, where `log` is close
to zero: there the relative sensitivity rises to 2e-9.

On the phase 7 gate's scores it does not reach pp through the miss penalty.

**But `reading_difficulty` itself does reach pp.** Discovered while working on
legacy scoring: on `diffcalc-test` under `HRDT` it drifts by 1 ulp and pp
diverges by `3.8e-16`. It survived this long because the `HRDT` combination was
absent from `FIXTURE_MOD_SETS` — the phase 4 gate had never seen it, and the
corpus comparison holds a `1e-12` tolerance. `HRDT` and `CLHRDT` are in the
phase 5 gate now.

The moral: **a fixture set is coverage too.** A missing mod combination is no
different from an untaken code branch.

### Found while implementing arbitrary scores

None of this would have surfaced on an FC: all four places live behind the
`effectiveMissCount > 0` gate or in the classic branch.

**Integer division where you expect a fractional one.**

```csharp
int maxPossibleSliderBreaks = Math.Min(attributes.SliderCount, (attributes.MaxCombo - scoreMaxCombo) / 2);
```

Both operands are `int`, so the division is integral. Python needs `//`. The
trap is predictable but easy to miss: the expression looks like an ordinary
fraction.

**A ratio used as a count.**

```csharp
if (scoreMaxCombo < fullComboThreshold)
    missCount = fullComboThreshold / Math.Max(1.0, scoreMaxCombo);
```

The threshold is divided by the combo, and the result is stored as the number
of misses. By meaning that is a ratio, not a count; it looks like a bug in the
original. Reproduce it literally.

**Order in the sliderbreak estimate.** `nonMissMistakeAdjustment` is computed
from the estimate *before* smoothing, while the multiplication by `Smoothstep`
comes after:

```csharp
double nonMissMistakeAdjustment = (nonMissMistakes - estimatedSliderBreaks + 4.5) / (nonMissMistakes + 4);
estimatedSliderBreaks *= DiffUtils.Smoothstep(effectiveMissCount, 1, 2);
return estimatedSliderBreaks * nonMissMistakeAdjustment * DiffUtils.Logistic(missedComboPercent, 0.33, 15);
```

Swap the two lines and you get a different number, and no algebraic argument
will catch it.

**`Math.Log(Math.Max(1, x))` — division by zero.**

```csharp
private double calculateMissPenalty(double missCount, double difficultStrainCount)
    => 0.93 / (missCount / (4 * Math.Log(Math.Max(1, difficultStrainCount))) + 1);
```

At `difficultStrainCount <= 1` the logarithm is zero: C# divides by it, gets an
infinity and hence a zero multiplier, while Python falls over. Not theory — on
four of the seven test-set beatmaps `ReadingDifficultNoteCount` is zero. Cured
by `c_div`; verified by reverting the fix.

**Under classic, `countSliderEndsDropped` equals the entire slider count.**
`GetValueOrDefault(SliderTailHit)` yields zero for a missing key, and the
subtraction leaves the full `SliderCount`. The value is read nowhere in the
classic branch, but it must still be computed as the original does.

**A gate that never entered the code says nothing about it.** The first run of
the score matrix passed on the first attempt — because the mod sets contained
no `FL`, so the flashlight penalty branch never executed at all. Since then the
phase 7 gate counts hits per branch and fails if any of them stayed at zero.

---

### Found while porting legacy scoring

**Integer division legitimised by a comment.**

```csharp
// ReSharper disable once PossibleLossOfFraction (intentional to match osu-stable...)
attributes.ComboScore += (int)(Math.Max(0, combo - 1) * (scoreIncrease / 25 * scoreMultiplier));
```

`scoreIncrease` is an `int`, so `300/25 = 12`, `30/25 = 1`, and `10/25 = 0`:
**a slider tick does not enter the combo part of the score at all.**

**`decimal` instead of double — emulating 80-bit registers.** Stable computed
the ScoreV1 multiplier on x87, where the registers are wider than both float
and double. .NET computes on SSE, so ppy moved to `decimal`, and the comment
opens with "DO NOT TOUCH IF YOU DO NOT KNOW WHAT YOU ARE DOING". A subtlety
that is easy to miss: the cast `(decimal)(double)float` keeps **15 significant
digits**, not every digit of the double.

**Combo arrives unclamped.** The rest of the calculator works with
`Math.Clamp(score.MaxCombo, 0, MaxCombo)`, while `OsuLegacyScoreMissCalculator`
reads `score.MaxCombo` directly. On a combo above the beatmap's maximum it
produces a **negative** estimate, and the oracle prints it honestly.

**Negative integer division.** A direct consequence of the previous point: `//`
in Python rounds down while C# truncates toward zero, so `-1261/2` gives -631
against -630. It does not affect pp — a negative estimate is clamped to zero
anyway — but the value is exposed publicly, and the intermediate-value gate
catches it.

**The three legacy attributes have different sources, and they must not be
confused.** The multiplier is taken from `WorkingBeatmap.Beatmap` — the
original beatmap, before mod adjustments; `CalculateNestedScorePerObject` and
the score simulator receive the playable version, with mods already applied.
Our `prepare_beatmap` edits HP/OD/CS in place, so the multiplier is taken
*before* it and the other two after. On the supported mods this makes no
difference (HR and EZ change neither timing nor slider length), but writing
"all three from the original beatmap" would be a lie about two of them.

**The `[Events]` section stopped being optional.** Breaks are subtracted from
the beatmap length, which enters the ScoreV1 multiplier. The decoder ignored
Events entirely — it had to start parsing them.

---

### Twenty-first: the same division, a second place

Found by re-reading the diff before publication — like the sixteenth, not by a
gate and not by the corpus.

`drain_length` computes `(span - breaks) // 1000`. The difference is negative
when the breaks are longer than the beatmap's span, and anywhere in the
-999..-1 ms window the two forms disagree: C# truncates toward zero and gives
0, Python rounds down and gives -1.

The lesson is not about the division — we had already fixed that with
`c_int_div` — but that **a fix for a class of bug has to be carried to every
place of that class**. Back then one place where the dividend goes negative was
dealt with, and that was that. The second was right next door, in a file added
by the same commit.

The cost turned out higher than the first one's: there a negative estimate was
clamped to zero anyway, whereas here zero and minus one land in *different*
branches of `CalculateDifficultyPeppyStars` — at zero length `ratio = 16`, at a
negative one it clamps to 0. The ScoreV1 multiplier changes twofold, the miss
estimate comes out understated, and pp on the probe diverged by 14% in the
flattering direction: 47.61 from the port against the oracle's 41.74.

The probe is `tests/edge_cases/legacy-drain-underflow.osu`, wired into the
phase 7 gate. Before adding it, it was checked to go red on the reintroduced
bug: 28 mismatches, including pp itself.

After the fix, EVERY integer division in the port was reviewed — there are
four, and the other three are safe for distinct reasons rather than by eye:

* `score_increase // 25` — the dividend is the literal `300` at all three call
  sites;
* `total_half_spins // 2` and `full_spins // 2` — the decoder clamps the
  spinner with `max(end_time, start_time)`, so the duration is non-negative;
* `(total_half_spins - half_spins_before_bonus) // 2` — this dividend *can* go
  negative, and the two forms differ by one. But `Math.Max(0, bonus_spins -
  full_spins / 2)` follows it, and the subtrahend is non-negative: both versions
  yield zero. The difference is absorbed by the clamp rather than absent.

---

### On speed

Measurement has to alternate runs of the old and the new version: single
measurements on this machine wander by 4–5%, and the very first estimate of the
gain came out over a percentage point too high. Five alternating passes give a
spread below one percent.

The profile shows the work is spread out: the most expensive function is the
rhythm evaluator at 11% of the time, and another 22% goes into emulating
float32, without which the calculation is wrong. There is no single bottleneck,
so no large trivial gains remain.

One was found and was worth 17.8%: **`min(max(x, lo), hi)` replaced by
conditionals**. In Python a built-in call costs more than the comparison itself
— 128 ns against 9.6 ns — and there are over three hundred thousand clamps per
beatmap.

The replacement is correct **only for the clamp chain**. A bare `min(a, b)`
behaves differently from a conditional expression on NaN:

```python
min(nan, 1.0)                  # nan
nan if nan < 1.0 else 1.0      # 1.0  — DIFFERENT
```

In the chain both comparisons are false, the value falls through to the "return
as is" branch, and the NaN passes straight through — exactly as `Math.Clamp`
requires in .NET (see the comment in `SliderPath._progress_to_distance`).
Verified by enumerating `nan`, `±inf`, `-0.0` and values on both sides of the
bounds.

Bare `min`/`max` calls were therefore left alone, although they would have
given roughly another 10%: that would be a silent change of semantics in
hundreds of places for a few percent, in a library that is two orders of
magnitude slower than the native alternative regardless.

---

# Verified facts about the ecosystem

- `ppy/osu` @ `52461f1b` — `Version => 20260706`.
- `rosu-pp` 4.0.2 has **no `Reading` skill for osu!standard**: the `reading`
  attribute comes back `None`, alongside the other rulesets' fields in the same
  union type ([issue #76](https://github.com/MaxOhn/rosu-pp/issues/76)). Its
  star rating for `diffcalc-test` is 6.6233 against the reference 6.5243, so it
  is not at `20260706`. It does carry part of the same era's work — its
  `legacy_score_base_multiplier`, `nested_score_per_object` and
  `maximum_legacy_combo_score` match ours exactly — so its pin sits somewhere
  between the legacy-scoring change and the Reading rebalance. Checking that
  attribute is a more reliable version probe than any date.
- **The official API is not a workaround.** For std,
  `BeatmapDifficultyAttributes` returns `star_rating, max_combo, aim_difficulty,
  aim_difficult_slider_count, speed_difficulty, speed_note_count, slider_factor,
  aim_difficult_strain_count, speed_difficult_strain_count` — and that is all.
  Neither `reading_difficulty` nor `flashlight_difficulty` nor
  `hit_circle_count`, without which pp cannot be reconstructed.
