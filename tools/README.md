# Running the rival tools

`pareto/run_rivals.py` compares our proven bounds against two established tools that also
*prove* a round-off bound: **FPTaylor** (symbolic Taylor forms) and **Daisy** (interval
ranges with an affine error model). Neither is bundled here. This is how the numbers in
`bench/rivals_report.txt` were produced.

Both are optional: with the environment variable missing, the tool is skipped and its
column reads `n/a`.

## FPTaylor

FPTaylor is written in OCaml. Its authors also publish a `js_of_ocaml` build that powers
[FPTaylorJS](https://monadius.github.io/FPTaylorJS), and that build is the practical way to
run FPTaylor on a machine without an OCaml toolchain — including Windows.

```sh
git clone --depth 1 -b gh-pages https://github.com/monadius/FPTaylorJS fptaylor-gh-pages
git clone --depth 1 https://github.com/monadius/FPTaylorJS fptaylor-src
cp fptaylor-gh-pages/fptaylor.js tools/fptaylor_js/fptaylor.js
node tools/fptaylor_js/extract_config.js fptaylor-src/src/default_config.js
```

`tools/fptaylor_js/run.js` drives that bundle from Node: the bundle is a Web Worker script,
so the driver supplies the `onmessage`/`postMessage` globals it expects and hides `process`
during initialisation, otherwise the js_of_ocaml runtime switches to the real filesystem
device and dies with `cannot register file`.

Sanity check — this must print `7.10542735761e-15`:

```sh
printf 'Variables\n  real x in [1, 20];\n\nExpressions\n  r rnd64= x + x;\n' > /tmp/t.txt
node tools/fptaylor_js/run.js /tmp/t.txt
```

Version used for the published table: FPTaylor 0.9.4+dev, default configuration
(`fp-power2-model = true`, `opt-exact = true`, `opt-approx = false`).

**Caveat worth stating plainly:** that bundle was built on 2020-12-23 (`monadius/FPTaylorJS`
gh-pages `e7a571f`), so it is FPTaylor as of then, not today's master. It is also the
branch-and-bound backend only — the optional Gelpia and Z3 backends are not in a browser
build, and on some problems they give FPTaylor a tighter bound than what we measured here.
If a number in our table matters to you, re-run it against a native FPTaylor build.

Then point the harness at it:

```sh
export FPTAYLOR_JS=tools/fptaylor_js
```

## Daisy

Daisy is Scala and builds with sbt. It needs a JDK (22–25), `cc` on `PATH` for the
tree-sitter frontend, and MPFR for some of its phases.

```sh
git clone --depth 1 https://github.com/malyzajko/daisy
cd daisy
sbt compile
sbt script            # produces ./daisy
./daisy --silent testcases/rosa/Doppler.scala
```

The last command must report `Absolute error: 4.1911988101104756e-13`, the number printed
in Daisy's own README. That is the check that the build is faithful.

**On Windows**, two things get in the way and `daisy-windows-lazy-mpfr.patch` fixes both:

* `lib/` ships the MPFR JNI natives for Linux and macOS only, and several analysis phases
  build MPFR constants in `object` initialisers, so *every* run dies in `<clinit>` before
  reaching any phase. The patch turns those constants into `lazy val`s. The phases we use
  (the default data-flow analysis with interval ranges and affine errors) never touch MPFR,
  so the numbers are unaffected — only the phases that genuinely need MPFR now fail, and
  only if you ask for them.
* sbt 1.9.9 predates these JDKs; the patch moves `project/build.properties` to 1.11.3.

Apply it against Daisy master (`6a6f47a`, 2026-09-03):

```sh
git apply /path/to/pareto-fp/tools/daisy-windows-lazy-mpfr.patch
```

`cc` on Windows: any C compiler under that name will do — we symlinked `clang.exe` from the
LLVM install as `cc.exe`. Without it the tree-sitter grammar fails to build; that only
disables the `--treesitter` frontend, and we feed Daisy Scala sources anyway.

Then:

```sh
export DAISY_HOME=/path/to/daisy
```

Version used for the published table: Daisy master `6a6f47a`, default analysis
(`--analysis=dataflow`, interval ranges, affine errors), Scala frontend.

## Running the comparison

```sh
FPTAYLOR_JS=tools/fptaylor_js DAISY_HOME=/path/to/daisy python pareto/run_rivals.py
```

Results land in `bench/rivals_report.txt` and `bench/rivals_results.json`.
