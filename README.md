# pareto-fp

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

**Read this section with that bias in mind.** Herbie optimises average error in bits over sampled inputs, while the table below scores its forms with our worst-case bound and our cost model. Winning under one's own metric is not winning. Treat this as "our front covers what Herbie returned, measured our way", not as a claim of superiority — a fair head-to-head needs Herbie's own metric as well, and that comparison is not done yet.

Comparing a single form against a whole front would be meaningless, so the question asked is different: **does our front contain a point that is no worse than Herbie's answer on both metrics at once?**

| case | Herbie's form | our front covers it |
|---|---|---|
| `sqrt(x+1) - sqrt(x)` | `1 / fma(x, 1/sqrt(x), sqrt(1+x))` | **yes, strictly better** |
| `2.5x³ + 3.5x² + …` (poly) | `fma(x, fma(4.5x, x, 2.5), fma(3.5x, x, 1.5))` | **yes, strictly better** |
| `(a/c) + (b/c)` | `fma(a, 1/c, b/c)` | **yes, strictly better** |
| `(a/b)/c` | `(a/b)/c` | equal point, nothing better |
| `log(a) - log(b)` | `log(a/b)` | **yes, strictly better** |
| `exp(a) * exp(b)` | `exp(b) / exp(-a)` | **yes, strictly better** |
| `x*x - y*y` | `fma(y, x-y, (x-y)*x)` | **yes, strictly better** |

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
- Interval arithmetic ignores correlation between occurrences of the same variable, so bounds are conservative.
- Saturation blows up on expressions with roots: 6 nodes become 6056 after saturation. Bounded by `node_limit`.
- Supported functions: `sqrt`, `exp`, `log`, `fma`, plus `+ - * /` and integer powers.
- On Windows, `mlir-opt` refuses paths containing non-ASCII characters, so the MLIR pipeline stages its files in a temporary directory.
- Timings come from a single laptop CPU (Ryzen 5 5500U, 15 W, thermally limited). Treat the speed ratios as orders of magnitude, not exact figures, and re-run them on your own machine.
- The cost model uses operation weights, not measured latency, and the `fma` weight is calibrated on that same laptop. On different hardware the ordering of points on the front can change.

## The bound is tested, not asserted

`tests/test_bound.py` rebuilds the Pareto front and checks, on every form it finds, that the error actually measured against a 60-digit `Decimal` reference never exceeds the bound the analysis proved. Inputs are drawn pseudo-randomly with a fixed seed plus the domain corners — deliberately not a uniform grid, since cancellation lives in narrow spots a grid can step over.

The margin today ranges from 1.6x to 400x, so the test is tight enough to notice a regression: halving any bound makes it fail.

## Status

Research prototype with reproducible numbers, not a product. Next steps, in order: the same rewriting as a real MLIR pass on the `arith` dialect instead of emitted modules, calibration of the cost model on the target machine, then an RFC on the LLVM Discourse.

Found a case where it helps, or where it fails? Open an issue with the expression and the input ranges. The first few real-world cases will be analysed and published here in full.

## License

Apache License 2.0 with LLVM Exceptions — same as LLVM itself, so the pass can go upstream without relicensing.
