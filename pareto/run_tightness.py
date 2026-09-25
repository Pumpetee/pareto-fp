# -*- coding: utf-8 -*-
"""How loose is the proven bound, in numbers.

A worst-case bound is only useful if it is close to what actually happens. Interval
arithmetic ignores correlation between repeated occurrences of the same variable,
so the bound is guaranteed to be conservative — the open question is by how much,
and that question deserves a measurement rather than a disclaimer.

For every form on the front this script reports the ratio

    proven bound / largest absolute error observed

over random points plus the domain corners. Ratio 1 would mean the bound is tight;
large ratios mean the analysis is paying for ignorance about correlation. The last
group of rows is deliberately adversarial: expressions where the same variable
appears several times, which is the worst case for interval arithmetic.

    python pareto/run_tightness.py
"""
from __future__ import annotations

import json
import math
import random
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import to_text
from pareto.egraph import EGraph
from pareto.parser import parse, variables
from pareto.rules import RULES
from pareto.run import CASES, exact
from pareto.run_herbie_metric import eval_float

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 60

POINTS = 600
SEED = 20260925

# Same variable used more than once — exactly where interval arithmetic loses.
CORRELATED = [
    ('x_minus_x', 'x - x', {'x': (1.0, 2.0)}),
    ('x_over_x', 'x / x', {'x': (1.0, 2.0)}),
    ('sq_minus_sq', 'x*x - x*x', {'x': (1.0, 2.0)}),
    ('x_times_x_over_x', '(x * x) / x', {'x': (1.0, 2.0)}),
    ('diff_same_sqrt', 'sqrt(x) - sqrt(x)', {'x': (1.0, 4.0)}),
]


def measure(tree, ref_tree, domain, rng):
    names = sorted(domain)
    pts = [{n: domain[n][0] for n in names}, {n: domain[n][1] for n in names}]
    pts += [{n: rng.uniform(*domain[n]) for n in names} for _ in range(POINTS)]
    worst = Decimal(0)
    for env in pts:
        dec_env = {k: Decimal(float(v)) for k, v in env.items()}
        ref = exact(ref_tree, dec_env)
        try:
            got = Decimal(eval_float(tree, env))
        except (ValueError, OverflowError, ZeroDivisionError):
            continue
        worst = max(worst, abs(got - ref))
    return worst


def rows_for(name, tree, domain, rng, limit=4, with_original=False):
    """Forms from the front, optionally preceded by the expression as written.

    The original matters on the adversarial group: the front there collapses to a
    constant, and showing only the collapsed form would hide the very number the
    reader came for — how loose the bound is *before* rewriting.
    """
    out = []
    if with_original:
        _, bound, _, _, _ = tree_cost(tree, domain)
        err = measure(tree, tree, domain, rng)
        out.append({'case': name + ' (as written)', 'form': to_text(tree), 'cost': 0.0,
                    'bound': bound, 'measured': float(err),
                    'ratio': float(Decimal(str(bound)) / err) if err > 0 else float('inf')})

    eg = EGraph()
    root = eg.add_expr(tree)
    eg.saturate(RULES, iters=6, node_limit=20000, domain=domain)
    front, _ = pareto_extract(eg, root, domain, keep=limit)
    for point in front[:limit]:
        cost, bound, form = point[0], point[1], point[2]
        err = measure(form, tree, domain, rng)
        ratio = float(Decimal(str(bound)) / err) if err > 0 else float('inf')
        out.append({'case': name, 'form': to_text(form), 'cost': cost,
                    'bound': bound, 'measured': float(err), 'ratio': ratio})
    return out


def main():
    rng = random.Random(SEED)
    rows = []
    for name, case in CASES.items():
        rows += rows_for(name, case['expr'], case['domain'], rng)

    adversarial = []
    for name, text, domain in CORRELATED:
        tree = parse(text)
        adversarial += rows_for(name, tree, domain, rng, with_original=True)

    (BENCH / 'tightness_results.json').write_text(
        json.dumps({'cases': rows, 'correlated': adversarial}, ensure_ascii=False, indent=1),
        encoding='utf-8')

    def table(title, data):
        lines = ['', title, f'{"case":<18}{"bound":>12}{"measured":>12}{"loose by":>11}  form']
        for r in data:
            ratio = 'exact' if math.isinf(r['ratio']) else f'x{r["ratio"]:.3g}'
            lines.append(f'{r["case"]:<18}{r["bound"]:12.2e}{r["measured"]:12.2e}'
                         f'{ratio:>11}  {r["form"][:44]}')
        return lines

    finite = [r['ratio'] for r in rows if not math.isinf(r['ratio'])]
    out = ['How far the proven bound sits above the error that actually occurs.',
           f'{POINTS} random points per form plus both domain corners, fixed seed.']
    out += table('BENCHMARK CASES', rows)
    out += table('ADVERSARIAL: the same variable repeated (worst case for intervals)', adversarial)
    out += ['',
            f'benchmark cases: {len(rows)} forms, bound never below the measured error,',
            f'  looseness from x{min(finite):.3g} to x{max(finite):.3g}, median x{sorted(finite)[len(finite) // 2]:.3g}.',
            '"exact" means the form is exact on every point sampled, so the ratio is undefined.']
    text = '\n'.join(out)
    (BENCH / 'tightness_report.txt').write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
