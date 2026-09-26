# pareto-fp (research)

Takes a floating-point expression, or a whole function out of a C file, and rewrites it into an equivalent one that is more accurate, often faster, and comes with a **proven upper bound on the error**.

Today a compiler gives you two options: keep the exact order of operations and stay slow, or turn on `-ffast-math` and get speed with no guarantees at all. There is nothing in between. This tool builds the Pareto front over *cost* and *provable error bound*, and lets you pick a point on it.

```
$ pareto-fp "x*x - y*y" --domain x=1000..1000.001 --domain y=999.999..1000

input expression: ((x * x) - (y * y))
model cost: 5.0 | proven error bound: 1.164e-10

Pareto front (every non-dominated form):
     cost        bound  form
      5.0    4.494e-16  ((y + x) * (x - y))
      9.8    3.331e-16  fma(x, (x - y), ((x - y) * y))

cheapest form   : ((y + x) * (x - y))
                  1.00x cheaper, error bound 4.494e-16
most accurate   : ((y + x) * (x - y))
                  bound 2.59e+05x smaller, costs 1.00x

paste into code : ((y + x) * (x - y))
```

The ranges matter as much as the expression. On `x, y ∈ [1, 2]` this same rewrite buys nothing: the bound stays around 1e-15 either way, because absolute rounding error does not care about cancellation when the operands are far apart. The gain above comes from `x` and `y` being close, which is exactly when the difference of squares destroys significant digits.

## Install and run

**No installation.** Download `pareto-fp.pyz` from [Releases](https://github.com/Pumpetee/pareto-fp/releases) and run it with any Python 3.10 or newer:

```
python pareto-fp.pyz "sqrt(x+1) - sqrt(x)" --domain x=1e6..1e9
```

**No Python either.** The same release page has standalone binaries for Linux, macOS and Windows — one file, nothing to install.

**From source**, no dependencies:

```
git clone https://github.com/Pumpetee/pareto-fp && cd pareto-fp
python -m pareto.cli "sqrt(x+1) - sqrt(x)" --domain x=1e6..1e9
```

Ranges are mandatory: without knowing the inputs there is no error bound to prove.

## On your own file

Point it at a C file instead of typing the expression out:

```
$ pareto-fp --file examples/branch_sqrt.c --function safe_diff_sqrt

function        : safe_diff_sqrt -> float64
input ranges    : x in [0, 1e+09]
execution paths : 2 (32 boxes used to decide the conditions)

path       as written    rewritten  condition
T           4.926e-12    1.176e-16  x > 1
F           3.006e-16    3.006e-16  x > 1   (false)

proven bound as written : 4.926e-12
proven bound rewritten  : 3.006e-16
  improvement           : 1.64e+04x tighter

rewritten body:
    if ((x) > (1.0)) {
        return (1.0 / (sqrt(x) + sqrt((x + 1.0))));
    }
    return (sqrt((x + 1.0)) - sqrt(x));
```

Ranges live in a comment next to the function, because they are part of its contract:

```c
// @domain x: 0.0 .. 1000000000.0
double safe_diff_sqrt(double x) { ... }
```

`--list` shows the functions and the ranges it found. `--domain x=1..2` overrides them.

Each execution path is analysed and rewritten **on its own**: the arithmetic that is best on `x > 1` is not the arithmetic that is best below it, which is exactly why a compiler specialises inside a branch.

### Conditionals are where a bound can quietly become a lie

A comparison is decided on the **computed** values, and those carry rounding error. Near the boundary the program can take the other branch, and then the error against the ideal value is not rounding at all — it is the whole **jump** between the branches. A tool that takes the maximum over the branches and prints it is wrong on exactly the inputs that matter.

So the bound is reported in two parts:

```
proven bound as written : 1.000e+00
  of which rounding     : 3.331e-16 as written, 2.220e-16 rewritten
  of which branch jump  : 1.000e+00
```

When the branches meet at the boundary the jump term is zero and does not appear. When they do not, no rewriting of the arithmetic will help, and saying so is more useful than a pretty number.

### Mixed precision, read out of the declarations

Types are followed by the rules of C, not guessed. In `float a, b; a*b` the multiply happens in binary32; in `a*2.0` it happens in binary64, because the literal is a double; assigning into a `float` rounds, and so does returning from a `float` function. All of that changes the bound.

```
$ pareto-fp --file examples/mixed_precision.c --function energy
proven bound as written : 3.912e-04
proven bound rewritten  : 1.221e-04      3.21x tighter
rewritten body:
    return (float)(fma((h * 9.8100004196167), m, ((0.5 * v) * (m * v))));
```

That is the oldest fix in numerical code — keep the intermediates wide, round once at the end — except here you get the factor it bought you, proven rather than hoped for.

The question in the other direction has its own flag. `--target` asks for the **narrowest formats whose proven bound still stays under a value you choose**:

```
$ pareto-fp "0.5*m*v*v + m*9.81*h" --domain m=1..2 --domain v=0..30 --domain h=0..100 --target 1e-3

Precision:
    format        bound  note
   float64    1.164e-13  every operation in this format
   float32    6.256e-05  every operation in this format
   float16    3.223e-02  every operation in this format

narrowest mix that still meets 1.000e-03: bound 6.256e-05, 6 subexpression(s) narrowed
```

Tools that tune precision usually decide by running a sample of inputs. Here the check is a proven bound over the whole range, so the answer is never "it was good enough on my tests".

What the tool does **not** claim: that narrow precision is faster. On scalar x86 `mulss` and `mulsd` have the same latency; the win of `float` lives in memory traffic and vector width, and the cost model measures neither. So the front never picks `float` "for speed", and the precision question is answered by the bound instead.

### Loops

A loop with a known trip count is unrolled and then analysed as straight-line code. Unrolling is the only sound way this method knows to handle a loop, so the trip count has to be constant — a loop bounded by a variable is refused out loud rather than guessed at. Array reductions are a separate mode with bounds from Higham (`pareto/reductions.py`).

### Time

`--time-budget` (30 seconds by default, `0` for no limit) puts a clock on the whole analysis. Cutting it short **only loosens the bound, it never makes it wrong**: every stage it skips would have offered a better candidate or a tighter estimate, never a valid one. Skipped stages are named in the output, so a loose answer never looks like a complete one. The clock is checked between stages and never in the middle of one, so the real time can exceed the target by the length of the stage in flight.

## What it measures about itself

Numbers below are produced by `pareto/run_fpbench.py`, which CI re-runs on clean Linux and macOS machines and publishes as an artifact.

**Proven bounds on the FPBench rosa cases.** The bound for the expression as written, and for the form this tool returns:

| case | as written | rewritten |
|---|---|---|
| verhulst | 1.587e-16 | 1.587e-16 |
| predatorPrey | 9.519e-17 | 9.432e-17 |
| sine | 4.071e-16 | 2.935e-16 |
| sqroot | 4.857e-16 | 1.882e-16 |
| rigidBody1 | 2.132e-13 | 1.155e-13 |
| rigidBody2 | 2.231e-11 | 1.458e-11 |
| turbine1 | 1.239e-14 | 1.092e-14 |
| turbine2 | 1.335e-14 | 1.148e-14 |
| turbine3 | 7.182e-15 | 5.440e-15 |
| carbonGas | 5.712e-09 | 2.538e-09 |

Twelve of the thirteen get a tighter bound; `verhulst` is already in its best form. The whole run takes 310 s, of which `carbonGas` alone is 272 — every other case is under nine seconds.

**How loose the bound is.** Proven bound divided by the largest error measured over 600 random points plus the domain corners, across the 15 forms on the benchmark fronts: never below the measured error, from x1.44 to x664, **median x2.58**. A factor of two or three is the ordinary price of a worst-case guarantee; the outlier is a form whose real error is near zero.

**How the bound is checked.** 102 tests. Property-based interval tests, and three separate fuzzers that generate random inputs and require the measured error to stay under the printed bound: one for plain binary64 expressions, one for expressions with binary32 and binary16 rounding sprinkled in at random, and one for whole programs with branches. Fuzzing found nine defects in the bound, two of them on the same day the bound was extended. The tenth was found by the work on conditionals rather than by a fuzzer, and it had been there since the first commit: the interval endpoints were computed in ordinary binary64 with no directed rounding, so an interval could come out **narrower** than the true range of values — and a narrow interval scales the error down, which is how a bound stops being a bound. Fixing it cost under 2% on the published bounds; the details are in [docs/how-it-works.md](docs/how-it-works.md).

**Supported input.** Scalar `double` and `float` code over `+ - * /`, `sqrt`, `exp`, `log`, `fma`, `expm1`, `log1p`, `hypot`, integer `pow`, plus local variables, `if`/`else` with `&&` and `||`, and loops with a constant trip count. Array reductions are a separate mode. No pointers, no arrays with a computed index, no `while`, no calls to functions we have no bound for — those are refused with the line number, not approximated.

## Where to read further

- [docs/how-it-works.md](docs/how-it-works.md) — the pipeline, the compilation path through MLIR, how conditionals and mixed precision are handled, how the bound is verified, how loose it is and why, what the search costs, and the two times the bound lied.
- [docs/comparison.md](docs/comparison.md) — measurements next to Herbie, FPTaylor and Daisy, and an explicit list of what in this project is not new.
- [examples/](examples/) — four C files to run it on, each one making a different point.

## Limitations

- Scalar expressions, straight-line code, conditionals and constant-trip-count loops. No matrices, no memory effects, no data-dependent loops — which is where a lot of the real-world win lives.
- The cost model uses operation weights, not measured latency, and the `fma` weight is calibrated on one laptop. On different hardware the ordering of points on the front can change. Narrow precision is given no speed credit at all (see above).
- Interval arithmetic ignores correlation between repeated variables; affine arithmetic recovers part of that, and what is left is measured at median x2.58 — see [docs/how-it-works.md](docs/how-it-works.md).
- Iterated division blows the interval up: three Newton steps over `t ∈ [0.25, 4]` already put zero inside a denominator, and the analysis then answers "infinity", which means "could not prove it", not "the error is huge". Narrower ranges give a bound.
- Saturation on expressions with roots reaches thousands of nodes; `carbonGas` is the current worst case at minutes rather than seconds. The time budget bounds the wall clock, but a pass inside a compiler needs microseconds, and this is not that yet.
- On Windows, `mlir-opt` refuses paths containing non-ASCII characters, so the MLIR pipeline stages its files in a temporary directory.
- Timings were taken on a single laptop CPU (Ryzen 5 5500U, 15 W, thermally limited). CI re-runs the benchmarks on Linux x86-64 and macOS arm64 and publishes the raw numbers, so the ratios can be checked on hardware that is not mine. Accuracy figures are bit-for-bit identical everywhere, as IEEE arithmetic requires; only the speed ratios move.

## Status

Research prototype with reproducible numbers. It now reads a real file, which is the difference between a demo and something you can point at your own code, but it is not a compiler pass. Next, in order: the same rewriting as a real MLIR pass on the `arith` dialect instead of emitted modules, calibration of the cost model on the target machine, then an RFC on the LLVM Discourse.

Found a case where it helps, or where it fails? Open an issue with the function and the input ranges. The first few real-world cases will be analysed and published here in full.

## License

Apache License 2.0 with LLVM Exceptions — same as LLVM itself, so the pass can go upstream without relicensing.
