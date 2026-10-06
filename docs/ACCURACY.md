# Accuracy of the calculation, and how it is verified

The reference is the real C# code of osu!, built from pinned commits
(see [oracle/PINNED.md](../oracle/PINNED.md)), rather than published values:
the literals in ppy's own tests sit on a loose `1e-5` tolerance and drift
behind master.

## Layered gates

The port is checked against the reference layer by layer — from parsing the
file to the final pp. A discrepancy in an early layer inevitably drags
everything after it along, so when something breaks the earliest failing phase
is the one to look at.

| Gate | What it checks | Volume |
|---|---|---|
| Phase 1 | `.osu` parsing, slider geometry, stacking, `MaxCombo` | 17 beatmaps |
| Phase 2 | `OsuDifficultyHitObject` | 562 884 values |
| Phase 3 | 7 evaluators, 4 skills, and the per-object series the public API exposes | 7 mod sets; 160 812 values in the public series, including times |
| Phase 4 | difficulty attributes and star rating | 70 combinations, 17 attributes |
| Phase 5 | **pp** | 1140 combinations, deviation `3.798e-16` |
| Phase 6 | edge cases | 21 beatmaps, 2 of them under `CL` |
| Phase 7 | **pp for an arbitrary score** | 1428 scores, 15 708 values |

```bash
python3 tests/run_all.py
```

Phases 5–7 need the oracle built (see below); phases 1–4 run off stored
fixtures.

## Corpus verification

The gates check carefully chosen beatmaps, so the port is additionally run over
a real corpus of `.osu` files. The two checks answer different questions.

**Do the numbers agree** — a random sample against a live osu-tools:

```bash
python3 tools/sweep.py ~/Downloads/osu_files --count 500
```

The sample is reproducible from its seed; changing the seed yields new
beatmaps. Half the mod sets are classic, so a single run exercises both
scoring mechanics.

| Run | Volume | Result |
|---|---|---|
| Lazer mod sets, cumulative | 5000 beatmaps, 15 000 combinations | no mismatches, worst `4.1e-16` |
| With random scores, seed `70707` | 400 beatmaps, 1200 combinations (584 non-FC) | no mismatches, worst `3.6e-16` |
| With score totals, seed `8080` | 400 beatmaps, 1200 combinations (146 with a total) | no mismatches, worst `5.8e-16` |
| With classic sets, seed `20260728` | 600 beatmaps, 1800 combinations | no mismatches, worst `2.1e-16` |
| After the clamp rewrite, seed `20260810` | 500 beatmaps, 1500 combinations (704 non-FC, 183 with a total) | no mismatches, worst `4.0e-16` |

The last run exists because release 0.3.0 rewrote the clamp as conditionals —
a change touching hundreds of thousands of calls per beatmap across the whole
difficulty calculation. The gates covered it, but the gates are not where this
project's bugs have historically been caught.

**What the port rejects and where it falls over** — a full scan without the
oracle:

```bash
python3 tools/corpus_scan.py ~/Downloads/osu_files
```

The oracle is not needed here, so the whole corpus is covered in 45 minutes.
Last run: 231 780 files, no anomalies — 150 796 beatmaps computed, 80 984 not
osu!standard, 30 parsed while skipping broken lines. It was repeated for 0.3.0,
because that release taught the decoder to parse `[Events]`, and the decoder is
where nine of the numbered bugs lived; the tallies came out identical to the
previous run, file for file.

[PORTING.md](PORTING.md) numbers twenty-one bugs, and they break down by how
each was found: **twelve** by reading the port against C#, **four** by the
sampled comparison, **three** by the full scan, and **two** by re-reading the
port's own diff — the last of which changed pp by 14%. Not one of those last
nine was caught by any gate. Six earlier bugs, described in the sections
preceding the numbering, are not part of that count.

The full scan was not repeated under `CL`, and that is justified: the mod takes
part neither in parsing the file nor in computing difficulty, and this run
answers exactly the questions "what failed to parse" and "where did it fall
over". Numbers under `CL` are covered by the sampled comparison, where half the
mod sets are classic.

## Arbitrary scores

Phase 7 compares not only the final pp but every intermediate value the oracle
prints separately: both miss estimates, both sliderbreak estimates, and all
five pp components. A broken layer names itself.

The gate additionally counts how many scores hit each branch and fails if any
of them never fired. This is not belt-and-braces: the first run of the matrix
passed on the first attempt only because the mod sets contained no `FL`, so the
flashlight penalty branch never executed at all.

**Classic scores with a known score total are supported.** For them misses are
estimated from the score total rather than from combo: in stable a sliderbreak
is indistinguishable from a dropped tail by the statistics, but distinguishable
by the total, because ScoreV1 multiplies every hit by the current combo
multiplier. The branch fired in 756 of the gate's 1428 scores.

**Impossible inputs are part of the verified surface.** osu-tools clamps
neither combo nor misses — `misses: 1000` on a 4-object beatmap produces
negative hit counts, and the oracle computes right through them. The gate's
matrix deliberately includes such inputs and requires the port to return the
oracle's garbage bit for bit: those are exactly the runs that exercise the
clamps inside the pp formula. The phase 7 gate always uses `validate=False`
so that numerical correspondence is checked independently of input validation:
every one of its 15 708 compared values matches the oracle.

Validation has its own autonomous gate in `tests/test_public_api.py`: 80 cases
with explicit acceptance or rejection expectations, checked through both
`validate_score` and `Simulator.score`. They cover defaults, counter bounds,
combined combo losses, nested hits with zero combo, empty beatmaps, and both
scoring mechanics. Accepted scores are also checked for identical results with
validation enabled and disabled, and for ignored nested fields under `CL`.
Passing validation establishes the documented necessary conditions; it does
not reconstruct the order of hits or prove full reachability of the score.

## Known deviations

**Under `CL` the `raw_accuracy` flag does nothing.** This is not an oversight:
classic mechanics count accuracy over hit circles alone, so the raw and the
displayed accuracy coincide by construction. The property is pinned down by the
public API gate and by the snapshot.

**Accuracy below 16.67% is unreachable.** Below that threshold 300s and 100s
run out and osu-tools makes up the rest with misses — which an FC cannot do by
definition. Requesting 15% yields the maximum number of 50s, i.e. an actual
25%. The `Result.accuracy` field will show the true value, but the result will
not agree with `osu-tools -a 15`. That is a boundary of the scope, not a
calculation error — and it belongs to deriving an FC breakdown from a target
accuracy. Passing an explicit breakdown to `score()` has no such limit.

**The `Reading` evaluator** deviates from the reference by 1–4 ulp on roughly
2–20% of objects. Two attributes are affected.

`reading_difficult_note_count` — unused entirely on an FC (in the pp formula it
appears only inside `if (effectiveMissCount > 0)`); it participates on scores
with misses, but there is no amplification: a 1 ulp shift moves the miss
penalty by 1–2 ulp at most.

`reading_difficulty` — on certain mod combinations it drifts by 1 ulp and
**reaches pp**. Measured on `diffcalc-test` under `HRDT`: `1197.3941449623242`
against the oracle's `1197.3941449623246`, i.e. `3.8e-16`. Star rating still agrees — the cube root saves
it. The combination is deliberately included in the phase 5 gate, which is why
its worst deviation is `3.798e-16` rather than zero: hiding a known deviation
by dropping a mod set would be worse than keeping it visible under a tolerance.

The same 1–4 ulp deviation reaches `ObjectDifficulties.reading`, the per-object
series the public API exposes. The phase 3 gate compares all 31 269 reading
values with `abs(actual - expected) <= 1e-12 * max(abs(expected), 1)` and
rejects non-finite values or a length mismatch. The other series and times
are compared exactly: 129 543 values. All 70 expected beatmap-and-mod
combinations must be checked; a missing fixture fails the gate.
Anyone plotting a reading graph is
looking at numbers that are right to about fifteen significant digits rather
than to the last bit; nothing downstream of a graph can notice, but saying so is
cheaper than having someone rediscover it.

**Timing sections with out-of-order times.** Grouping of simultaneous points
reproduces `flushPendingPoints` for consecutive lines — that is, for everything
editors actually write. A file with unordered duplicate times would require the
logic of `ControlPointInfo.Add`; judged not worth the complexity.

## The oracle

The phase 5–7 gates compare against a live osu! calculation, so they need the
C# toolchain. The library itself does **not** require it.

.NET 8 is needed — not newer; `global.json` in `ppy/osu` limits roll-forward to
the 8.0 branch:

```bash
brew install --cask dotnet-sdk@8
```

From there follow [oracle/PINNED.md](../oracle/PINNED.md): it records the
`ppy/osu` and `ppy/osu-tools` commits the reference was taken from, and the
build commands.

Fixtures for phases 1–4 are generated from the same place:

```bash
python3 tools/oracle.py fixtures
```
