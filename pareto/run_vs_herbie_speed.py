# -*- coding: utf-8 -*-
"""Where this project actually beats Herbie: the same accuracy, less work.

Accuracy is a dead heat — on the seven benchmark cases both tools land at one or two
ulps and neither can do better in double precision. But Herbie has no cost model at
all: it returns whatever form happens to be numerically good, and that form is often
more expensive. `fma(a, 1/c, b/c)` carries two divisions where `(a+b)/c` has one,
`exp(b)/exp(-a)` swaps a multiply for a divide and a negation.

This script compiles Herbie's form and ours into one binary per case and times both
under identical flags, so the comparison is machine time, not a model estimate.

    python pareto/run_vs_herbie_speed.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.codegen import to_text
from pareto.parser import parse
from pareto.run import CASES, exact, rel_error
from pareto.run_fairbench import gen_source, run_mode
from pareto.toolchain import find_clang

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 60

# One honest mode: -O3 without fast-math. Both forms get exactly the same treatment.
MODE = ('o3', ['-O3', '-ffp-contract=off'])
POINTS = 1024
REPS = 400


def main():
    find_clang()
    stored = json.loads((BENCH / 'herbie_results.json').read_text(encoding='utf-8'))
    rows = []

    for rec in stored:
        name = rec['name']
        case = CASES[name]
        herbie_t = parse(rec['herbie_form'])
        ours_t = parse(rec['our_form'])
        pts = [case['points'](i, POINTS) for i in range(POINTS)]

        # base = Herbie's form, opt = ours: one binary, one run, identical flags
        cpath = gen_source('vsherbie_' + name, case, herbie_t, ours_t, pts, REPS)
        res = run_mode(cpath, 'vsherbie_' + name, MODE[0], MODE[1])

        herbie_ns, ours_ns = res['base_ns'], res['opt_ns']
        err_h = max_err_of(case['expr'], pts, res['vals'], 'base')
        err_o = max_err_of(case['expr'], pts, res['vals'], 'opt')
        rows.append({
            'name': name,
            'herbie_form': to_text(herbie_t), 'ours_form': to_text(ours_t),
            'herbie_ms': herbie_ns / 1e6, 'ours_ms': ours_ns / 1e6,
            'speedup': herbie_ns / ours_ns if ours_ns else float('nan'),
            'herbie_err': float(err_h), 'ours_err': float(err_o),
        })

    (BENCH / 'vs_herbie_speed_results.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')

    out = ['Same accuracy, different price: Herbie\'s form versus ours, one binary per case,',
           f'clang {MODE[1]}, {POINTS} points x {REPS} reps.', '',
           f'{"case":<12}{"herbie ms":>11}{"ours ms":>10}{"speedup":>9}'
           f'{"herbie err":>13}{"our err":>12}']
    for r in rows:
        out.append(f'{r["name"]:<12}{r["herbie_ms"]:11.3f}{r["ours_ms"]:10.3f}'
                   f'{r["speedup"]:9.2f}{r["herbie_err"]:13.3e}{r["ours_err"]:12.3e}')
    wins = [r for r in rows if r['speedup'] > 1.05]
    losses = [r for r in rows if r['speedup'] < 0.95]
    geo = 1.0
    for r in rows:
        geo *= r['speedup']
    geo **= 1.0 / len(rows)
    out += ['',
            f'faster than Herbie on {len(wins)} of {len(rows)} cases, slower on {len(losses)}, '
            f'geometric mean {geo:.2f}x.',
            '', 'Forms:']
    for r in rows:
        out.append(f'  {r["name"]}')
        out.append(f'    herbie: {r["herbie_form"]}')
        out.append(f'    ours  : {r["ours_form"]}')
    text = '\n'.join(out)
    (BENCH / 'vs_herbie_speed_report.txt').write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


def max_err_of(expr, pts, vals, key):
    worst = Decimal(0)
    for p, v in zip(pts, vals):
        env = {k: Decimal(float(val)) for k, val in p.items()}
        worst = max(worst, rel_error(v[key], exact(expr, env)))
    return worst


if __name__ == '__main__':
    sys.exit(main())
