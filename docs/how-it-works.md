# How it works, and how it is checked

The README lists what the tool does and what it measures. This file explains the
machinery behind those numbers: the pipeline, the compilation path, how the bound
is verified, how loose it is, and what the search costs.

## How it works

1. The expression goes into an e-graph. Saturation applies 27 rewrite rules until the graph stops growing, so one e-class holds every equivalent form.
2. Every e-class gets an interval for its value. Forms inside a class are equivalent, so the class interval is the intersection of all estimates.
3. Every form gets two metrics: machine cost (work plus critical path) and an upper bound on the absolute error, using `fl(a∘b) = (a∘b)(1+δ)`, `|δ| ≤ 2^-53`.
4. Non-dominated (cost, error) pairs are collected bottom-up and the tree is reconstructed.
5. A policy picks a point: cheapest form within an accuracy budget, or most accurate form within a cost budget.

## Why this is not just `-ffast-math`

`-ffast-math` optimises for speed only. It never improves accuracy, and it never tells you what it cost you. Measured on real native code, `clang 23.1.2`, x86-64:

| case | form | best clang time | our form, same flags | error, clang | error, ours |
|---|---|---|---|---|---|
| `x*x - y*y` | difference of squares | 0.178 ms | **0.111 ms** | 1.21e-08 | **1.02e-51** |
| `log(a) - log(b)` | log ratio | 4.326 ms | **2.279 ms** | 2.29e-16 | **1.30e-16** |
| `(a/c) + (b/c)` | common divisor | 0.295 ms | 0.295 ms | 7.21e-17 | 7.21e-17 |
| `(a/b)/c` | division chain | 0.295 ms | 0.295 ms | 1.01e-16 | 1.01e-16 |
| `exp(a) * exp(b)` | exponent sum | 1.932 ms | 1.932 ms | 3.23e-16 | 3.23e-16 |
| `sqrt(x+1) - sqrt(x)` | root difference | 1.004 ms | 1.004 ms | 1.99e-07 | 1.99e-07 |
| poly | cubic polynomial | 0.179 ms | 0.179 ms | 9.45e-16 | 9.45e-16 |

All seven cases are in the table, including the four where we change nothing. Where the value is: on the difference of squares `-ffast-math` leaves the error at 1.21e-08 — it optimises for speed and never for accuracy — while our form is exact **and** 1.6x faster. On the log ratio the compiler does not do the rewrite at all, we are 1.9x faster with half the error. On the remaining three the compiler already performs the same transformation itself, and that is fine: the two are not competitors. Our rewrite makes `-ffast-math` safe instead of replacing it.

### Array summation

65 536 elements, three datasets, every scheme compiled in all three modes (`python pareto/run_fairreduce.py`):

| | naive at `-O2` | naive, best mode | our best scheme | speedup vs best clang | accuracy gain |
|---|---|---|---|---|---|
| uniform | 14.7 ms | 3.8 ms (`fast`) | **1.9 ms** | **2.00x** | 2.58x |
| mixed | 14.7 ms | 3.8 ms (`fast`) | **1.9 ms** | **1.99x** | 1.25x |
| alternating | 14.7 ms | 3.7 ms (`fast`) | **1.9 ms** | **1.94x** | 6.87x |

Against an ordinary `-O2` build the gain is 7.6–7.8x. Against the best the compiler can do on its own — vectorised and with reassociation allowed — it is still **2x**, and that 2x comes with better accuracy, not worse. Both numbers are in the table above, measured the same way.

One finding worth its own line: `-ffast-math` **destroys compensated summation**. Kahan and Neumaier drop from 5e-17 to 3.5e-15 error under `-ffast-math`, because the compiler proves the compensation term is algebraically zero and deletes it. Hand-written numerical safeguards silently stop working — ours do not, because the form is chosen before that stage.

## It runs through real MLIR, not only through a Python harness

Both forms are emitted into the `func` / `arith` / `math` dialects and lowered by stock `mlir-opt` and `mlir-translate` — no custom hacks in the pipeline:

```
MLIR_BIN=/path/to/llvm/build/bin python pareto/run_mlir.py sq_diff
python -m pareto.to_mlir "(x + y) * (x - y)" --name sq_diff_opt
```

Measured through that path (`clang -O2`, no fast-math flags on the emitted IR):

| case | speedup over the original form | accuracy gain |
|---|---|---|
| `x*x - y*y` | 1.00x | **1.2e+43x** |
| `exp(a) * exp(b)` | **2.14x** | 0.76x |
| `log(a) - log(b)` | **1.70x** | 1.76x |
| `(a/c) + (b/c)` | **1.67x** | 1.88x |
| `(a/b)/c` | **1.64x** | 1.63x |
| `sqrt(x+1) - sqrt(x)` | 1.01x | 1.00x |
| poly | 1.00x | 1.02x |

Reports: `bench/mlir_report.txt`, `bench/mlir_results.json`. Generated modules are kept in `bench/mlir_*.mlir` so the lowering can be re-run by hand.

## The harder benchmark

The seven cases above are too easy for a comparison: after rewriting both tools sit at one or two ulps, which is the floor of double precision, and any difference there is noise. `pareto/hard_cases.py` adds nine problems from actual numerical analysis, where the error as written runs into tens of bits. Scoreboard, both rulers at once (`python pareto/run_hard_scoreboard.py`):

| case | as written | ours | Herbie | our bound | his bound | our cost | his cost |
|---|---|---|---|---|---|---|---|
| `sq_expand` — `(a+b)²−(a−b)²` | 34.01 | **0.00** | 0.00 | 8.88e-15 | 8.88e-15 | 4.0 | 4.0 |
| `hypot` — `sqrt(x²+y²)` | 61.09 | **0.00** | 0.00 | 1.57e+139 | 1.57e+139 | 24.0 | 24.0 |
| `exp_minus_one` — `exp(x)−1` | 19.46 | **0.00** | 0.24 | **1.11e-22** | 2.22e-22 | 44.0 | 15.6 |
| `log_one_plus` — `log(1+x)` | 19.49 | **0.00** | 0.27 | 1.11e-22 | 1.11e-22 | 44.0 | 44.0 |
| `cube_diff` — `x³−y³` | 16.39 | **0.15** | 0.16 | 2.44e-12 | 2.44e-12 | 15.6 | 15.6 |
| `ratio_cancel` — `(x²−1)/(x−1)` | 8.02 | 0.25 | 0.25 | 2.22e-16 | 2.22e-16 | 2.0 | 2.0 |
| `exp_series` — Taylor, 8 terms | 0.53 | 0.28 | 0.28 | 1.03e-14 | 1.03e-14 | 73.0 | 35.2 |
| `quadratic_root` | 47.14 | 0.51 | **0.26** | 5.55e-22 | 4.44e-23 | 60.6 | 27.0 |
| `variance` — `E[x²]−E[x]²` | 43.74 | 43.11 | n/a | 7.52e-04 | — | 49.8 | — |

**Two wins, five ties, one loss, and one case where his answer uses operations we do not have.** Numbers are mean bits of error over 1500 sampled inputs, same reference for both.

Three things carry those results. `expm1`, `log1p` and `hypot` as first-class operations — real identities, not approximations, and they close three cases outright. Constant folding, without which the tool emitted `(2+2)*(b*a)` and lost on price to `a*(4*b)` for no reason at all. And Taylor series as **certified** candidates: the Lagrange remainder, bounded by the maximum derivative over the domain, is added to the rounding error of the polynomial itself, so the printed bound covers the method error too. Herbie substitutes series as well, but ships no guarantee with them.

The remaining loss is honest: on the quadratic formula he expands in the small parameter and then cancels the division symbolically, ending at `−c/b − ac²/b³`. We reach 0.51 bits from 47.14 by the same kind of expansion, but keep a common factor we cannot cancel — that needs a conditional rewrite rule, and conditional rules are where this project has already broken correctness once.

## The bound once lied, and how that was found

On 25.09.2026 an outside reviewer ran the CLI on the very example this README opened with and reported that the printed bound was **zero** while the real error reached 6.7e-16. He was right, and the cause was three characters of code:

```python
def iv_sub(a, b): return (a[0] - b[0], a[1] - b[1])   # wrong
def iv_sub(a, b): return (a[0] - b[1], a[1] - b[0])   # correct
```

Interval subtraction was written coordinate-wise. On `[1,2] − [1,2]` it returned `(0, 0)` instead of `(−1, 1)`, the interval collapsed to a point, and since rounding error is computed as `U · max|interval|`, the "proven bound" collapsed with it. Every number this project printed as a guarantee was suspect for as long as that line existed.

Worse than the bug is why the tests stayed green. `test_bound.py` exercised `sq_diff` on a narrow domain around 1000, where the broken interval was still non-zero and happened to cover the measurement. The README example was in no test at all. A test suite that only visits the places you already trust is decoration.

What changed, beyond the one-line fix:

- `tests/test_intervals.py` is property-based — random intervals per operation, a grid of points inside, and the result must lie within the interval the analysis returned. It fails on **any** operation that is wrong, not only on the one someone remembered.
- The README example is now its own regression test, on the exact domain where the bound used to be zero.
- One old test had to be rewritten: it demanded that `x*x − y*y` be rewritten on `[1,2]×[1,2]`, and it only ever passed because the bound there was zero. On that domain the rewrite genuinely buys nothing.
- Every benchmark table below was regenerated from scratch afterwards. The conclusions held; several bounds grew, which is what a fix in this direction should do.

## The bound is tested, not asserted

`tests/test_bound.py` rebuilds the Pareto front and checks, on every form it finds, that the error actually measured against a 60-digit `Decimal` reference never exceeds the bound the analysis proved. Inputs are drawn pseudo-randomly with a fixed seed plus the domain corners — deliberately not a uniform grid, since cancellation lives in narrow spots a grid can step over.

### How loose is the bound

Interval arithmetic ignores correlation between repeated occurrences of the same variable, so the bound is conservative by construction. That is a real weakness and it deserves a number rather than a disclaimer, so `pareto/run_tightness.py` measures it: proven bound divided by the largest error observed over 600 random points plus both corners.

Across the 15 forms on the benchmark fronts: **never below the measured error, looseness from x1.44 to x664, median x2.58.** A factor of two or three is the normal price of a worst-case guarantee; the x664 outlier is the most accurate `diff_sqrt` form, where the real error is near zero and any bound looks huge next to it.

The adversarial group is the interesting one — expressions where the same variable repeats, the worst case for intervals:

| expression | bound as written | real error | after rewriting |
|---|---|---|---|
| `x / x` | 2.22e-16 | 0 | `1`, bound 0 |
| `x*x - x*x` | 8.88e-16 | 0 | `0`, bound 0 |
| `(x*x) / x` | 1.33e-15 | 2.22e-16 (x6 loose) | `x`, bound 0 |
| `sqrt(x) - sqrt(x)` | 4.44e-16 | 0 | `0`, bound 0 |

So the criticism lands and then partly answers itself: the analysis alone cannot see that `x/x` is exactly one, but rewriting collapses exactly those self-cancelling patterns, and the bound on the resulting form is not conservative — it is zero. What remains conservative is the case of a variable repeated in a form that cannot be collapsed, and there the measured price is around x6.

The test is tight enough to catch a regression: halving any bound makes it fail.

## What the search costs

E-graph saturation blows up, and the number worth knowing is how much. Measured on the seven benchmark cases:

| case | nodes after saturation | classes | front | saturate | extract |
|---|---|---|---|---|---|
| `diff_sqrt` | 6056 | 714 | 2 | 1.75 s | 6.18 s |
| `sq_diff` | 434 | 100 | 2 | 0.05 s | 0.04 s |
| `poly` | 236 | 29 | 6 | 0.04 s | 0.03 s |
| the other four | 9–10 | 6–7 | 1–2 | < 0.01 s | < 0.01 s |

Roots are the blow-up: six nodes become 6056, a thousandfold, and extraction — not saturation — becomes the bottleneck, since every class carries its own front of non-dominated points. The `node_limit` of 60000 was not reached on any case, so nothing here is truncated.

Eight seconds to analyse one numerical kernel is acceptable. Eight seconds per expression inside a compiler pass over a whole translation unit is not, and that is the honest reason this is a tool you point at a hot kernel today rather than a pass you enable globally.
