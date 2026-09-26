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

## A whole function, not one expression

Real numeric code is not a single expression. It declares intermediate values, it
guards the edge of a domain with an `if`, and it loops. Until the tool could read
that, it could not be pointed at anyone's file — only at a formula copied out of
one by hand.

**Local variables are inlined.** A value computed once and used three times becomes
three copies of the same subtree, and that is not a loss of information: identical
subtrees get the same rounding symbol in the symbolic error form, so the model still
sees one rounding rather than three. The cost model has always counted a shared
subexpression once (`cse_work`), and the printed C now shares it too — the rewritten
body comes out with `double t1 = ...` temporaries rather than one inlined monster.
Those three places finally agree with each other.

**Conditionals become paths.** Each path carries its set of decided comparisons and
its own return expression. The bound is computed per path and the maximum is taken,
which is legal because the maximum over a union is the maximum of the maxima. Each
path is also *optimised separately*, over its own reachable region: the arithmetic
that is best on `x > 1` is not the arithmetic that is best below it.

Which boxes a path can reach is decided by splitting the domain and evaluating each
comparison on each box. A box where a comparison comes out definitely false for this
path is dropped; a box where it is undecided is kept, so the estimate stays an upper
one.

**The unstable test is the part that makes this hard.** A comparison is decided on
the *computed* values, which carry rounding error. Where the two sides can be within
that error of each other, the program is free to take either branch — and if it
takes the wrong one, the error against the ideal value contains the whole **jump**
between the branches. A bound that is the maximum over the branches is simply wrong
on those inputs, and they are exactly the inputs a reviewer will try.

So the reported bound is the sum of two terms: rounding, the maximum over reachable
paths, plus the jump, the largest `|ideal of one branch − ideal of the other|` over
the boxes where the test can flip. Two details matter:

- the error of the *comparison* is the sum of the errors of its two sides, and
  nothing more. Modelling it as the error of a subtraction adds a rounding that the
  machine never performs — and with it, `x > 1` gets declared unstable, so a jump
  that cannot happen lands in the bound;
- when both branches compute the same expression the jump is exactly zero, and that
  is checked structurally rather than estimated. An interval estimate of the
  difference gives a small but non-zero number there, and a program with no
  discontinuity would be charged for one.

`tests/test_program_bound.py` does not assume a flip can happen, it makes one
happen: the guard is `(x + 1e16) - 1e16 > 0`, which is `x > 0` in real arithmetic and
a multiple of two in binary64, so for `x < 1` the machine really does take the other
branch. The test first asserts the flip occurs and only then that the bound covers
it. A previous attempt at that case — `x*x - y*y > 0` on neighbouring `x` and `y` —
turned out to never flip, and that is worth knowing too: one ulp of step in `x`
moves `x²` by about two ulps, which is more than the error of computing the squares.
Our analysis still marks such a comparison unstable, out of caution.

**Loops are unrolled** when the trip count is a constant, and refused out loud when
it is not. Unrolling is the only sound way this method knows to handle a loop: after
it the body is straight-line code and everything else applies without a single new
axiom.

## Mixed precision is a node in the tree, not a flag

Rounding to a narrower format is an explicit unary node: `('f32', subtree)`. That
choice buys three things at once.

For the e-graph it is an ordinary operator that appears in no rewrite rule, so no
rule ever moves anything across it, while the algebra *around* it keeps working.
Rewriting stays correct because for the algebra `f32(x)` is just some real number,
and an identity over real numbers holds for it.

It expresses mixed precision without a new concept. `f32(x*y + z)` is a multiply and
an add in binary64 with one rounding to binary32 at the end; `f32(f32(x*y) + z)` is a
different program; both are trees.

And it makes the central question a matter of where to put nodes. Here is the part
worth being precise about: **rounding nodes are not part of the mathematics, they are
part of the implementation.** An expression has an ideal real value, and it does not
depend on the formats the intermediates were kept in. The bound in this project has
always meant one thing — the distance from the printed program to that ideal value.
So the e-graph is seeded with the expression *without* rounding nodes, and the format
of the *result* is put back on the forms it finds, because the result format is a
contract: a function declared to return `float` must be answered with a form that
returns `float`. The inner roundings are the free choice, and usually the answer is
to drop them — which is the oldest advice in numerical code, now with the factor it
buys you attached.

`--target` walks the other way: it narrows subexpressions bottom-up while the proven
bound stays under a value you choose. The standard tools for tuning precision decide
by sampling inputs; here the check is a proven bound over the whole range, so the
answer is never "it was good enough on my tests".

What is deliberately *not* claimed is a speedup. On scalar x86 `mulss` and `mulsd`
have the same latency; the win of `float` is memory traffic and vector width, neither
of which this cost model measures. A rounding node therefore costs zero, and the
front never picks `float` "for speed". Charging for the node would penalise float
code, and crediting it would be a speed claim we have not measured.

## The bound lied twice, and how that was found

### Interval subtraction, coordinate-wise

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

### Interval endpoints, with no directed rounding

The second one had been there since the first commit, and it was found on
26.09.2026 by the work on conditionals rather than by a fuzzer. Intervals were
computed with ordinary binary64 arithmetic. That means every operation on an
interval **endpoint** was itself rounded — and could round *inward*, making the
interval narrower than the true range of values. Since the rounding error of an
operation is scaled by the magnitude taken from the interval, an interval narrower
than the truth gives a bound lower than the truth, which is not a bound.

What made it visible was a comparison, not a bound: on `(x + 1e16) - 1e16 > 0` with
`x ∈ [0.1, 1.9]` the true value is `x`, strictly positive. But `1e16 + 0.1` is exactly
`1e16` in binary64, and after the subtraction the interval collapsed to the single
point `(0, 0)`. The analysis then declared the `> 0` branch **unreachable**, while it
is in fact taken always. The wrong conclusion about the program was loud; the same
defect inside a bound had been silent.

The proper cure is directed rounding — the lower endpoint computed rounding down, the
upper rounding up. Python does not expose the FPU rounding mode, so the equivalent is
done instead: compute as usual, then step each endpoint outward with `math.nextafter`.
One double operation is off by at most half an ulp, so one step suffices; two are
taken, to cover the library functions that the standard does not promise to round
correctly.

Affine arithmetic needed more than a step outward, because its centre and
coefficients accumulate their own rounding across many operations — and affine forms
exist precisely in order to *narrow* the interval, so their own error works against
their purpose. Each form now carries a third field bounding the error of its own
computation, propagated by the same rules the rest of the analysis uses, and the
interval it reports is widened by it.

The price of the fix, measured by regenerating the whole FPBench table: the bounds as
written are unchanged on twelve of thirteen cases and 0.8% worse on `turbine3`; the
rewritten bounds are unchanged except `sqroot`, 1.9% worse. Search time grew about
13%. Under two percent, for the difference between a bound and a number that looks
like one.

## Reading C, and refusing to

The front-end accepts a deliberately narrow subset: `double` and `float` locals and
parameters, `if`/`else` with `&&` and `||`, loops with a constant trip count, and the
functions we have a rounding bound for. Everything else — pointers, arrays with a
computed index, `while`, integer arithmetic as part of the computation, a call to
`sin` — is refused **with the line number**.

That ratio is the point. A parser that quietly pretends to understand an unfamiliar
construct produces a bound for a program other than the one in the file, and the
reader has no way to tell. That is worse than having no tool: a number you trust
which describes different code. Sixteen of the forty-one front-end tests check that
something is refused rather than approximated.

Types follow the rules of C rather than intuition, because getting them wrong is the
same failure in a quieter form. In `float a, b; a*b` the multiply happens in
binary32; in `a*2.0` it happens in binary64, because the literal is a double;
`a*2.0f` is back in binary32; assigning into a `float` rounds; returning from a
`float` function rounds. `0.1f` is not one tenth, it is the nearest binary32 value,
and it is stored as that.

## Back into the file, and a verdict instead of a report

Two steps separate a tool that tells you something from a tool you can use. Both are
small and neither is interesting, which is exactly why they get skipped.

**The result goes back into the source.** `pareto/apply.py` replaces the bytes between
the opening brace of the chosen function and its matching brace, and nothing else in
the file. The span comes from the same parse that produced the analysis
(`cfront.body_span`), so the text that is replaced is the text that was analysed — not
a second guess made by a regular expression over the file. The signature is not
touched, so the change does not propagate to callers, and comments, includes,
neighbouring functions and line endings survive byte for byte.

The one thing that is added is `#include <math.h>`, when the rewritten body calls
something from it and the file has no direct include of its own. The first version of
this rule was narrower — add it only if the rewrite *introduced* a call the original
body did not have — and it was wrong: a file that already called `sqrt` with no header
came out of the tool as broken as it went in, and it read as our defect rather than
its own. The check that caught it is now in CI: every example is rewritten and the
result is compiled. A file we hand someone has to build, and that is a machine's
question, not a judgement call.

There is a round-trip test for the substance rather than the formatting
(`tests/test_apply.py`): analyse, rewrite, write out, read the written file back, and
require the bound of what was written to equal the bound that was promised. Without
it, "the rewritten form holds 1e-16" would be a statement about a tree in memory, and
the file on disk would be taking it on faith.

**The answer is an exit code.** `--require X` asks whether the error provably stays
under `X` and says so with the code it exits with: `0` the code as written already
does, `1` it does not but a form we found does, `2` nothing found does, `64` the
arguments made no sense. Three verdicts rather than two, because the middle one is the
common case and carries a fix — collapsing it into "failed" throws away the only part
that is actionable. And `64` rather than `1` for a bad flag, because a typo must not
be able to impersonate a statement about someone's code; every user error in the CLI
goes through one function (`cli.die`) for that reason alone.

An infinite bound is a `2`, not a large number. "Could not prove it" and "the error is
huge" are different claims, and only one of them is ours.

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
