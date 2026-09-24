# pareto-fp (research)

Rewrites a floating-point expression into an equivalent one that is more accurate, often faster, and comes with a **proven upper bound on the error**.

Today a compiler gives you two options: keep the exact order of operations and stay slow, or turn on `-ffast-math` and get speed with no guarantees at all. There is nothing in between. This tool builds the full Pareto front over *cost* and *provable error bound*, and lets you pick a point on it.

```
$ python -m pareto.cli "x*x - y*y" --domain x=1..2 --domain y=1..2

input expression : ((x * x) - (y * y))
model cost: 5.0 · error bound: 8.882e-16

Pareto front (non-dominated forms):
     cost        error  form
      5.0    0.000e+00  ((x + y) * (x - y))

fastest        : ((x + y) * (x - y))
most accurate  : ((x + y) * (x - y))
paste into code: ((x + y) * (x - y))
```

## Why this is not just `-ffast-math`

`-ffast-math` optimises for speed only. It never improves accuracy, and it never tells you what it cost you. Measured on real native code, `clang 23.1.2`, x86-64:

| case | form | best clang time | our form, same flags | error, clang | error, ours |
|---|---|---|---|---|---|
| `x*x - y*y` | difference of squares | 0.153 ms | **0.095 ms** | 1.2e-08 | **1.0e-51** |
| `log(a) - log(b)` | log ratio | 3.866 ms | **2.036 ms** | 2.3e-16 | **1.3e-16** |
| `(a/c) + (b/c)` | common divisor | 0.253 ms | 0.253 ms | 7.2e-17 | 7.2e-17 |
| `(a/b)/c` | division chain | 0.253 ms | 0.253 ms | 1.0e-16 | 1.0e-16 |
| `exp(a) * exp(b)` | exponent sum | 1.702 ms | 1.725 ms | 3.2e-16 | 3.2e-16 |

Where the value is: on the difference of squares `-ffast-math` leaves the error at 1.2e-08 — it optimises for speed and never for accuracy — while our form is exact **and** 1.6x faster. On the log ratio the compiler does not do the rewrite at all, we are 1.9x faster with half the error. On the remaining three the compiler already performs the same transformation itself, and that is fine: the two are not competitors. Our rewrite makes `-ffast-math` safe instead of replacing it.

### Array summation

65 536 elements, three datasets, every scheme compiled in all three modes (`python pareto/run_fairreduce.py`):

| | naive at `-O2` | naive, best mode | our best scheme | speedup vs best clang | accuracy gain |
|---|---|---|---|---|---|
| uniform | 14.7 ms | 3.8 ms (`fast`) | **1.9 ms** | **2.00x** | 2.58x |
| mixed | 14.7 ms | 3.8 ms (`fast`) | **1.9 ms** | **1.99x** | 1.25x |
| alternating | 14.7 ms | 3.7 ms (`fast`) | **1.9 ms** | **1.94x** | 6.87x |

Against an ordinary `-O2` build the gain is 7.6–7.8x. Against the best the compiler can do on its own — vectorised and with reassociation allowed — it is still **2x**, and that 2x comes with better accuracy, not worse. Both numbers are in the table above, measured the same way.

One finding worth its own line: `-ffast-math` **destroys compensated summation**. Kahan and Neumaier drop from 5e-17 to 3.5e-15 error under `-ffast-math`, because the compiler proves the compensation term is algebraically zero and deletes it. Hand-written numerical safeguards silently stop working — ours do not, because the form is chosen before that stage.

## Install and run

No dependencies, Python 3.10+:

```
git clone https://github.com/Pumpetee/pareto-fp && cd pareto-fp
python -m pareto.cli "sqrt(x+1) - sqrt(x)" --domain x=1e6..1e9
```

Ranges are mandatory: without knowing the inputs there is no error bound to prove.

Reproduce the benchmark table above (needs `clang` in `PATH`, or set `PARETO_CLANG`):

```
python pareto/run_fairbench.py            # all cases, three compilation modes each
python pareto/run_fairbench.py sq_diff    # one case
```

Raw numbers land in `bench/fair_results.json`, the report in `bench/fair_report.txt`. CI runs the same benchmark on a clean Ubuntu runner and uploads both files as artifacts.

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

## How it works

1. The expression goes into an e-graph. Saturation applies 27 rewrite rules until the graph stops growing, so one e-class holds every equivalent form.
2. Every e-class gets an interval for its value. Forms inside a class are equivalent, so the class interval is the intersection of all estimates.
3. Every form gets two metrics: machine cost (work plus critical path) and an upper bound on the absolute error, using `fl(a∘b) = (a∘b)(1+δ)`, `|δ| ≤ 2^-53`.
4. Non-dominated (cost, error) pairs are collected bottom-up and the tree is reconstructed.
5. A policy picks a point: cheapest form within an accuracy budget, or most accurate form within a cost budget.

## Against Herbie

[Herbie](https://herbie.uwplse.org/) solves half of the same problem: it finds a numerically stable form, but it does not model cost and does not give a proven bound. Both tools were run on the same seven cases, and the forms Herbie produced were scored with our own model.

### First, the comparison on Herbie's own ruler

Scoring someone else's tool with your own metric proves nothing, so the honest test is the reverse: measure both on **Herbie's** metric — average bits of error over sampled inputs, `log2(1 + ulp distance)` against a 60-digit reference rounded to double, 4000 points per case, fixed seed.

| case | original | ours | Herbie | verdict |
|---|---|---|---|---|
| `diff_sqrt` | 27.197 | 0.319 | 0.331 | tie |
| `poly` | 0.328 | 0.283 | **0.214** | **Herbie better** |
| `two_div` | 0.254 | 0.254 | 0.216 | tie |
| `div_chain` | 0.267 | 0.267 | 0.267 | tie |
| `log_ratio` | 0.344 | 0.064 | 0.064 | tie |
| `exp_sum` | 0.352 | 0.352 | 0.348 | tie |
| `sq_diff` | 16.476 | 0.122 | 0.130 | tie |

**On Herbie's metric this project wins nothing: six ties and one loss.** Both tools take the two catastrophic cases from tens of bits of error down to a fraction of a bit, and on the rest they land within noise of each other. Anyone told that this tool "beats Herbie on accuracy" was told something false.

The actual difference is not accuracy, it is what you get back. Herbie returns one form with an empirical improvement and no guarantee. This returns the whole cost-versus-error front with a **statically proven worst-case bound** on every point, so a caller can say "give me the cheapest form that loses at most one bit" and have that hold for every input in the range, not on average over a sample. Reproduce with `python pareto/run_herbie_metric.py`, raw numbers in `bench/herbie_metric_report.txt`.

Caveat worth naming: Herbie samples inputs over the floating-point representation, this script samples uniformly over the value range. On a wide domain like `1e6..1e9` those distributions differ.

### Second, the front-coverage question

A single form against a whole front is not a fair pairing either, so the other question asked is: **does our front contain a point no worse than Herbie's answer on both of our metrics at once?** Scored with our bound and our cost model — our ruler, stated plainly, so read it as coverage and not as superiority.

| case | Herbie's form | covered by our front (our metrics) |
|---|---|---|
| `sqrt(x+1) - sqrt(x)` | `1 / fma(x, 1/sqrt(x), sqrt(1+x))` | yes, dominated on both |
| `2.5x³ + 3.5x² + …` (poly) | `fma(x, fma(4.5x, x, 2.5), fma(3.5x, x, 1.5))` | yes, dominated on both |
| `(a/c) + (b/c)` | `fma(a, 1/c, b/c)` | yes, dominated on both |
| `(a/b)/c` | `(a/b)/c` | equal point, nothing better |
| `log(a) - log(b)` | `log(a/b)` | yes, dominated on both |
| `exp(a) * exp(b)` | `exp(b) / exp(-a)` | yes, dominated on both |
| `x*x - y*y` | `fma(y, x-y, (x-y)*x)` | yes, dominated on both |

Note how differently the two tables read. Under our worst-case bound Herbie's forms look dominated everywhere; under his average-bits metric the same forms are level with ours. Both statements are true, and that gap is exactly what "choose your own metric" buys — which is why the first table is the one that counts.

Reproduce: `raco pkg install --auto herbie`, then `python pareto/run_herbie.py`. Raw output in `bench/herbie_report.txt`, the FPCore files Herbie produced are kept in `bench/herbie_improved.fpcore`.

Herbie leans heavily on `fma`, and the first run of this comparison lost two cases because our rules did not generate it. They do now — and that turned into the most instructive finding in the project, see below.

### The `fma` trap

Adding `fma` to the rules made the model happy and the machine unhappy. `fma` rounds once instead of twice, so on paper it is both cheaper and more accurate, and the search immediately started preferring it. On the actual CPU the opposite happened:

| case | form chosen | `-O3` | `-O3 -mfma` |
|---|---|---|---|
| poly | `fma(fma(x,4.5,3.5), x*x, fma(x,2.5,1.5))` | 1.43 ms | 0.34 ms |
| poly | plain Horner scheme | 0.36 ms | 0.31 ms |

Hardware FMA is not part of baseline x86-64. Without `-mfma` or `-march=native` the call goes to libm — correctly rounded and four times slower. Worse, on the difference of squares the search happily traded our best result away: it picked an `fma` form with error 5.8e-09 over the exact `(x+y)(x-y)` with 1e-51, because the model said `fma` was cheaper.

Fix: `fma` now costs *more* than a multiply-add pair by default (`PARETO_FMA_COST`, set it to `1.0` on a machine where FMA is enabled). The exact form came back, and the accuracy result with it. This is the same lesson the project already learned once on summation — an analytical cost model without calibration will confidently pick a worse form.

## What is not new here

Being explicit about this, because it is the first question any compiler person asks:

- Pairwise and blocked summation, Kahan compensation — decades old.
- E-graphs and equality saturation — the `egg` library and the work around it.
- Improving accuracy of floating-point formulas — [Herbie](https://herbie.uwplse.org/) does exactly that, and does it well.

What is new is the combination: **both metrics computed together**, an error budget given as a number and *proven* rather than hoped for, and a cost model calibrated against the actual machine. The last one came from practice — the analytical model was off by 3x on pairwise summation, and without calibration the tool would have picked a strictly worse form.

## Limitations

- Scalar expressions and array reductions only. No loops, no matrices, no memory effects — which is where most of the real-world win lives.
- Supported functions: `sqrt`, `exp`, `log`, `fma`, plus `+ - * /` and integer powers.
- On Windows, `mlir-opt` refuses paths containing non-ASCII characters, so the MLIR pipeline stages its files in a temporary directory.
- Timings in the tables were taken on a single laptop CPU (Ryzen 5 5500U, 15 W, thermally limited). CI re-runs the same benchmark on two architectures — Linux x86-64 and macOS arm64 — and publishes the raw numbers as artifacts, so the ratios can be checked on hardware that is not mine. Accuracy figures are bit-for-bit identical everywhere, as IEEE arithmetic requires; only the speed ratios move.
- The cost model uses operation weights, not measured latency, and the `fma` weight is calibrated on that same laptop. On different hardware the ordering of points on the front can change.
- Interval arithmetic ignores correlation between repeated variables, so bounds are conservative — measured at median x2.42, see "How loose is the bound".
- Saturation on expressions with roots reaches thousands of nodes and extraction takes seconds, see "What the search costs".

## The bound is tested, not asserted

`tests/test_bound.py` rebuilds the Pareto front and checks, on every form it finds, that the error actually measured against a 60-digit `Decimal` reference never exceeds the bound the analysis proved. Inputs are drawn pseudo-randomly with a fixed seed plus the domain corners — deliberately not a uniform grid, since cancellation lives in narrow spots a grid can step over.

### How loose is the bound

Interval arithmetic ignores correlation between repeated occurrences of the same variable, so the bound is conservative by construction. That is a real weakness and it deserves a number rather than a disclaimer, so `pareto/run_tightness.py` measures it: proven bound divided by the largest error observed over 600 random points plus both corners.

Across the 15 forms on the benchmark fronts: **never below the measured error, looseness from x1.44 to x664, median x2.42.** A factor of two or three is the normal price of a worst-case guarantee; the x664 outlier is the most accurate `diff_sqrt` form, where the real error is near zero and any bound looks huge next to it.

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

## Status

Research prototype with reproducible numbers, not a product. Next steps, in order: the same rewriting as a real MLIR pass on the `arith` dialect instead of emitted modules, calibration of the cost model on the target machine, then an RFC on the LLVM Discourse.

Found a case where it helps, or where it fails? Open an issue with the expression and the input ranges. The first few real-world cases will be analysed and published here in full.

## License

Apache License 2.0 with LLVM Exceptions — same as LLVM itself, so the pass can go upstream without relicensing.
