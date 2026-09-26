# pareto-fp (research)

Rewrites a floating-point expression into an equivalent one that is more accurate, often faster, and comes with a **proven upper bound on the error**.

Today a compiler gives you two options: keep the exact order of operations and stay slow, or turn on `-ffast-math` and get speed with no guarantees at all. There is nothing in between. This tool builds the full Pareto front over *cost* and *provable error bound*, and lets you pick a point on it.

```
$ python -m pareto.cli "x*x - y*y" --domain x=1000..1000.001 --domain y=999.999..1000

input expression: ((x * x) - (y * y))
model cost: 5.0 | proven error bound: 2.220e-10

Pareto front (every non-dominated form):
     cost        bound  form
      5.0    1.332e-15  ((x - y) * (y + x))
      9.8    1.110e-15  fma(x, (x - y), (y * (x - y)))

cheapest form   : ((x - y) * (y + x))
                  1.00x cheaper, error bound 1.332e-15
most accurate   : ((x - y) * (y + x))
                  bound 1.67e+05x smaller, costs 1.00x

paste into code : ((x - y) * (y + x))
```

The domain matters as much as the expression. On `x, y ∈ [1, 2]` this same rewrite buys nothing: the bound stays around 1e-15 either way, because absolute rounding error does not care about cancellation when the operands are far apart. The gain above comes from `x` and `y` being close, which is exactly when the difference of squares destroys significant digits.

## Install and run

No dependencies, Python 3.10+:

```
git clone https://github.com/Pumpetee/pareto-fp && cd pareto-fp
python -m pareto.cli "sqrt(x+1) - sqrt(x)" --domain x=1e6..1e9
```

Ranges are mandatory: without knowing the inputs there is no error bound to prove.

## What it measures about itself

Numbers below are produced by CI on a clean runner and published as artifacts.
None of them is a comparison with another tool; those live in
[docs/comparison.md](docs/comparison.md).

**Proven bounds on the FPBench rosa cases.** The bound for the expression as
written, and for the form this tool returns:

| case | as written | rewritten |
|---|---|---|
| verhulst | 1.587e-16 | 1.587e-16 |
| predatorPrey | 9.519e-17 | 9.432e-17 |
| sine | 4.071e-16 | 3.005e-16 |
| sqroot | 4.857e-16 | 2.784e-16 |
| rigidBody1 | 2.132e-13 | 1.155e-13 |
| rigidBody2 | 2.231e-11 | 1.504e-11 |
| turbine1 | 1.239e-14 | 1.132e-14 |
| turbine2 | 1.335e-14 | 1.321e-14 |
| turbine3 | 7.125e-15 | 5.874e-15 |
| carbonGas | 5.712e-09 | 3.925e-09 |

**How loose the bound is.** Proven bound divided by the largest error measured
over 600 random points plus the domain corners, across the 15 forms on the
benchmark fronts: never below the measured error, from x1.44 to x664, **median
x2.58**. A factor of two or three is the ordinary price of a worst-case
guarantee; the outlier is a form whose real error is near zero.

**What the analysis costs.** On the ten cases above, nine finish within a second
end to end; `predatorPrey` takes 7.5 s and `carbonGas` 215 s.

**How the bound is checked.** 33 tests, plus property-based interval tests and a
fuzzer that generates random expressions and random domains and requires the
measured error to stay under the printed bound. The fuzzer is what found the last
nine defects in the bound, including two on the same day it was extended.

**Supported input.** Straight-line scalar expressions and array reductions over
`+ - * /`, `sqrt`, `exp`, `log`, `fma`, `expm1`, `log1p`, `hypot` and integer
powers. No loops, no conditionals, no matrices.

## Where to read further

- [docs/how-it-works.md](docs/how-it-works.md) — the pipeline, the compilation
  path through MLIR, how the bound is verified, how loose it is and why, what the
  search costs, and the one time the bound lied.
- [docs/comparison.md](docs/comparison.md) — measurements next to Herbie,
  FPTaylor and Daisy, and an explicit list of what in this project is not new.

## Limitations

- Scalar expressions and array reductions only. No loops, no matrices, no memory effects — which is where most of the real-world win lives.
- Supported functions: `sqrt`, `exp`, `log`, `fma`, plus `+ - * /` and integer powers.
- On Windows, `mlir-opt` refuses paths containing non-ASCII characters, so the MLIR pipeline stages its files in a temporary directory.
- Timings in the tables were taken on a single laptop CPU (Ryzen 5 5500U, 15 W, thermally limited). CI re-runs the same benchmark on two architectures — Linux x86-64 and macOS arm64 — and publishes the raw numbers as artifacts, so the ratios can be checked on hardware that is not mine. Accuracy figures are bit-for-bit identical everywhere, as IEEE arithmetic requires; only the speed ratios move.
- The cost model uses operation weights, not measured latency, and the `fma` weight is calibrated on that same laptop. On different hardware the ordering of points on the front can change.
- Interval arithmetic ignores correlation between repeated variables, so bounds are conservative — measured at median x2.58, see [docs/how-it-works.md](docs/how-it-works.md).
- Saturation on expressions with roots reaches thousands of nodes and extraction takes seconds, see [docs/how-it-works.md](docs/how-it-works.md).

## Status

Research prototype with reproducible numbers, not a product. Next steps, in order: the same rewriting as a real MLIR pass on the `arith` dialect instead of emitted modules, calibration of the cost model on the target machine, then an RFC on the LLVM Discourse.

Found a case where it helps, or where it fails? Open an issue with the expression and the input ranges. The first few real-world cases will be analysed and published here in full.

## License

Apache License 2.0 with LLVM Exceptions — same as LLVM itself, so the pass can go upstream without relicensing.
