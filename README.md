# pareto-fp

**It finds places where your program quietly returns a wrong number, and it proves how wrong the number can get.**

Not "probably wrong". Not "wrong in our tests". A proven upper bound: the error cannot exceed this value for any input in the range you give.

[Та же страница по-русски](README.ru.md)

---

## The thing it finds, in one example

This is real code from [raylib](https://github.com/raysan5/raylib), a popular game library. It measures the length of a vector:

```c
float Vector2Length(Vector2 v)
{
    return sqrtf((v.x*v.x) + (v.y*v.y));
}
```

It looks correct. It is the formula from school. Compile it and run it:

| you call | it returns | the right answer |
|---|---|---|
| `Vector2Length({1e-30, 1e-30})` | `0` | `1.414e-30` |
| `Vector2Length({1e20, 1e20})` | `inf` | `1.414e+20` |

Not "slightly off". Zero instead of a real length. Infinity instead of a number that fits in the format with room to spare.

The reason: `v.x*v.x` is computed first, and for small numbers that square vanishes to zero, while for large ones it overflows — long before the final answer would. The one-line fix is the standard library function `hypotf`, which gives the right answer in both cases.

This tool found that by itself, in three separate libraries, written by three different authors who did not know each other. **You can check the table above in about two minutes** — the code is public and so is the compiler.

---

## What it does for you

You give it a function and the range its inputs actually take. You get back:

1. **The worst error possible** — a number you can put in a safety document.
2. **A rewritten version** of the same function that is more accurate, and often not slower.
3. **A plain warning** when the code can return something that is not a number at all.
4. **A yes/no answer for your build**: "the error stays under 1e-13" — pass or fail, as an exit code.

Today a compiler offers two choices: keep every operation in the written order and stay slow, or switch on `-ffast-math` and get speed with no guarantee whatsoever. There is nothing in between. This fills that gap.

---

## What was found in real libraries

Three libraries, none of them prepared for us, none of them written by us:

| library | what it does | functions checked | same defect found |
|---|---|---:|---|
| [raylib](https://github.com/raysan5/raylib) | graphics and games | 111 | yes |
| [box2d](https://github.com/erincatto/box2d) | 2-D physics | 37 | yes |
| [Chipmunk2D](https://github.com/slembcke/Chipmunk2D) | 2-D physics | 19 | yes |

Eight functions across the three return **not a single correct digit** on ordinary input ranges — coordinates between −1000 and 1000, the kind any game or simulation uses. Each one comes with the exact numbers to reproduce it: [the list is here](docs/witnesses.md).

It is one defect, not eight. The same mistake, repeated independently by three teams, and found automatically. That is the point.

---

## Why you can believe the numbers

This is the part to hand to your engineers.

**The tool is checked against a real compiler, bit for bit.** Every function is compiled by clang exactly as written, called on random inputs, and its answer is compared with our analysis — not approximately, but every single bit. If our reading of your code differed from your compiler's by one rounding, the comparison fails loudly.

167 functions across the three libraries. **Zero disagreements.**

That check does not run on the author's laptop. It runs on a clean machine in continuous integration: the three libraries are downloaded fresh and compared there, every time the code changes.

**The bound is attacked, not assumed.** Random expressions are generated, evaluated both by the real machine arithmetic and by an independent high-precision reference, and the measured error must stay under the printed bound. This has caught ten real defects in the bound itself over the project's life; each one was fixed at the cause and has a test guarding it. 128 tests run on three operating systems.

**Against other tools, measured, not claimed.** On the standard benchmark set used by this field:

| | tighter bound than ours | tighter bound than theirs |
|---|---|---|
| FPTaylor | 1 case out of 10 | we win 10 out of 10 for the user |
| Daisy | 0 cases out of 10 | we win 10 out of 10 for the user |

The full tables, including the one case we lose, are in [docs/comparison.md](docs/comparison.md).

---

## What it cannot do

An honest list, because you will find this out anyway:

- **It does not read every function.** On these three libraries it accepts half of the ones that do real arithmetic (raylib 50%, Chipmunk2D 35%, box2d 29%). The rest use arrays with computed indices, loops with an unknown number of steps, or system calls — and for those there is no bound to prove, not just no support.
- **It is not a rewriting champion.** [Herbie](https://herbie.uwplse.org/) is a free tool that searches numerically instead of proving. On deliberately hard cases it finds better forms than we do in six out of nine. It gives no guarantee; we do. These are different products, and we say so.
- **It needs you to state the ranges.** "Any float at all" is usually unprovable and also untrue of your code. If you cannot say what range an input takes, this tool cannot help you, and neither can any other.
- **It does not run your preprocessor.** Where a type changes with a build flag, it refuses rather than guesses — because a bound proved for the wrong build is worse than no bound.

---

## Try it

Nothing to install. Download one file — `pareto-fp.pyz` from [Releases](https://github.com/Pumpetee/pareto-fp/releases) — and run it with any Python 3.10 or newer:

```
python pareto-fp.pyz "x*x - y*y" --domain x=1000..1000.001 --domain y=999.999..1000
```

The same page has standalone binaries for Linux, macOS and Windows: one file, no Python needed.

On your own file:

```
python pareto-fp.pyz --file kernel.c --function energy --domain v=1..2 --require 1e-13
```

Exit code 0: your requirement holds as the code is written. 1: it does not hold as written, but the rewritten form the tool prints does hold. 2: no form found holds it. That is what makes it usable inside a build and not only in a terminal.

The ranges are not optional. Without knowing what the inputs are, there is no error to bound — for us or for anyone.

More: [how it works](docs/how-it-works.md) · [run on real libraries](docs/real-projects.md) · [side by side with other tools](docs/comparison.md) · [reproducible defects](docs/witnesses.md)

License: Apache 2.0 with the LLVM exception.

---

**Check it yourself in one line** (needs `git`, `clang` and Python; run it inside a clone of this repository): `git clone --depth 1 https://github.com/raysan5/raylib.git && python tools/difftest_c.py raylib` — it compiles every function of that library with clang, calls both it and our analysis on the same random inputs, and reports bit for bit whether the two agree.
