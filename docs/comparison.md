# Comparison with other tools

Kept out of the README on purpose. The README states what this tool measures about
itself; this file states how those measurements sit next to other people's work.
Every number here comes from a CI run on a clean machine, and each rival is built
from a source its own authors publish.

## Against Herbie

[Herbie](https://herbie.uwplse.org/) solves half of the same problem: it finds a numerically stable form, but it does not model cost and does not give a proven bound. Both tools were run on the same seven cases, and the forms Herbie produced were scored with our own model.

### First, the comparison on Herbie's own ruler

Scoring someone else's tool with your own metric proves nothing, so the honest test is the reverse: measure both on **Herbie's** metric — average bits of error over sampled inputs, `log2(1 + ulp distance)` against a 60-digit reference rounded to double, 4000 points per case, fixed seed.

| case | original | ours | Herbie | verdict |
|---|---|---|---|---|
| `diff_sqrt` | 27.197 | **0.319** | 0.370 | ours better |
| `poly` | 0.328 | 0.283 | 0.267 | tie |
| `two_div` | 0.254 | 0.254 | 0.220 | tie |
| `div_chain` | 0.267 | 0.267 | 0.267 | tie |
| `log_ratio` | 0.344 | 0.064 | 0.064 | tie |
| `exp_sum` | 0.352 | 0.352 | **0.216** | **Herbie better** |
| `sq_diff` | 16.476 | 0.122 | 0.122 | tie |

**On Herbie's metric this project is level with him: one win, five ties, one loss.** Both tools take the two catastrophic cases from tens of bits of error down to a fraction of a bit, and on the rest they land within noise of each other. Anyone told that this tool "beats Herbie on accuracy" was told something false.

### Where the difference actually shows up: price

Herbie has no cost model. It returns a numerically good form and stops there, so the form it picks is often the more expensive one — `fma(a, 1/c, b/c)` carries two divisions where `(a+b)/c` has one, `exp(b)/exp(-a)` trades a multiply for a divide plus a negation. Both forms compiled into one binary per case, identical flags, `-O3 -ffp-contract=off`:

| case | Herbie | ours | speedup | Herbie error | our error |
|---|---|---|---|---|---|
| `diff_sqrt` | 5.461 ms | **3.763 ms** | **1.45x** | 2.759e-16 | 2.201e-16 |
| `poly` | **1.680 ms** | 2.182 ms | 0.77x | 7.115e-16 | 7.115e-16 |
| `exp_sum` | 6.764 ms | **5.434 ms** | **1.24x** | 2.188e-16 | 2.465e-16 |
| `two_div` | 1.676 ms | 1.676 ms | 1.00x | 1.282e-16 | 1.353e-16 |
| `div_chain` | 1.676 ms | 1.676 ms | 1.00x | 1.646e-16 | 1.646e-16 |
| `log_ratio` | 3.955 ms | 3.968 ms | 1.00x | 1.314e-16 | 1.302e-16 |
| `sq_diff` | 0.841 ms | 0.841 ms | 1.00x | 3.064e-09 | 3.064e-09 |

**Faster on two of seven, slower on one, geometric mean 1.05x, accuracy level throughout.** That is the honest size of the win: not an order of magnitude, a few percent on average and 1.45x on the best case — earned by having a cost model where the other tool has none. On `poly` we lose: his form is 1.3x faster at identical accuracy, and our cost model preferred the wrong point. Reproduce with `python pareto/run_vs_herbie_speed.py`.

Worth stating plainly: on these seven cases both tools have hit the floor of double precision. Worst observed error is one to two ulps on either side, so "more accurate than Herbie" is not a thing that can be won here by anyone.

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

## Against FPTaylor and Daisy

Herbie rewrites but proves nothing. FPTaylor proves but cannot rewrite. Daisy does
both, and is therefore the closest thing to a direct competitor. All three publish
on the same FPBench cases, so the comparison runs on exactly those, with the same
domains and the same source form.

Every number below was produced by the `rivals` job in CI on a clean Ubuntu runner:
FPTaylor from the js_of_ocaml build its authors publish, Daisy built from source
with sbt and checked against the reference number in its own README before being
believed. Raw output lives in `bench/rivals_report.txt` and `bench/rivals_results.json`,
both committed straight from the run artifact. Reproduce with
**Actions -> tests -> Run workflow**; nothing here needs my machine.

### Analysis against analysis - the bound for the original form

| case | ours | FPTaylor | Daisy |
|---|---|---|---|
| verhulst | **1.587e-16** | 2.275e-16 | 3.719e-16 |
| predatorPrey | **9.519e-17** | 1.530e-16 | 1.749e-16 |
| sine | 4.071e-16 | **3.864e-16** | 1.130e-15 |
| sqroot | **4.857e-16** | 4.875e-16 | 5.707e-16 |
| rigidBody1 | **2.132e-13** | 2.949e-13 | 2.949e-13 |
| rigidBody2 | **2.231e-11** | 3.544e-11 | 3.553e-11 |
| turbine1 | **1.239e-14** | 1.639e-14 | 8.648e-14 |
| turbine2 | **1.335e-14** | 1.928e-14 | 1.307e-13 |
| turbine3 | **7.125e-15** | 9.111e-15 | 6.231e-14 |
| carbonGas | **5.712e-09** | 6.606e-09 | 1.652e-07 |

Nine of ten against FPTaylor, ten of ten against Daisy. The one loss is `sine`, by
five percent.

### What the user actually gets

The table above compares analysers. It is not what a user takes home, because a
user is free to ship a different form of the same expression - and rewriting is the
thing neither rival can do at all. So: our bound for our rewritten form against
their bound for the original.

| case | ours, rewritten | vs FPTaylor | vs Daisy |
|---|---|---|---|
| sine | 3.005e-16 | 1.29x | 3.76x |
| verhulst | 1.587e-16 | 1.43x | 2.34x |
| turbine1 | 1.132e-14 | 1.45x | 7.64x |
| turbine2 | 1.321e-14 | 1.46x | 9.89x |
| turbine3 | 5.874e-15 | 1.55x | 10.61x |
| predatorPrey | 9.432e-17 | 1.62x | 1.85x |
| carbonGas | 3.925e-09 | 1.68x | 42.11x |
| sqroot | 2.784e-16 | 1.75x | 2.05x |
| rigidBody2 | 1.504e-11 | 2.36x | 2.36x |
| rigidBody1 | 1.155e-13 | 2.55x | 2.55x |

Ten of ten, against both. `sine` wins here despite losing above, which is the whole
point: the tool that can change the formula does not have to win on the formula it
was handed.

Search time on the same run: nine of the ten cases finish inside a second,
`predatorPrey` takes 7.5s, and `carbonGas` takes 215s. That last one is the honest
outlier and the next thing to fix.

## What is not new here

Being explicit about this, because it is the first question any compiler person asks:

- Pairwise and blocked summation, Kahan compensation — decades old.
- E-graphs and equality saturation — the `egg` library and the work around it.
- Improving accuracy of floating-point formulas — [Herbie](https://herbie.uwplse.org/) does exactly that, and does it well.

What is new is the combination: **both metrics computed together**, an error budget given as a number and *proven* rather than hoped for, and a cost model calibrated against the actual machine. The last one came from practice — the analytical model was off by 3x on pairwise summation, and without calibration the tool would have picked a strictly worse form.
