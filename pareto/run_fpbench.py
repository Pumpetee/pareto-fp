# -*- coding: utf-8 -*-
"""Прогон на стандартном наборе FPBench: границы, время поиска и что нашлось.

Это те же задачи, на которых публикуют свои числа FPTaylor, Daisy, Gappa и Salsa.
Отчёт печатает нашу границу для исходной записи, лучшую границу с фронта, реальную
ошибку на выборке и время поиска — всё, что понадобится, чтобы поставить нас в одну
таблицу с ними и не выглядеть при этом смешно.

    python pareto/run_fpbench.py
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import to_text
from pareto.egraph import EGraph
from pareto.fpbench_cases import FPBENCH_CASES
from pareto.rules import RULES
from pareto.run import exact
from pareto.run_herbie_metric import eval_float

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 60

POINTS = 400
SEED = 20260925


def measured_error(tree, expr, domain, rng):
    names = sorted(domain)
    pts = [{n: domain[n][0] for n in names}, {n: domain[n][1] for n in names}]
    pts += [{n: rng.uniform(*domain[n]) for n in names} for _ in range(POINTS)]
    worst = Decimal(0)
    for env in pts:
        try:
            got = eval_float(tree, env)
            ref = exact(expr, {k: Decimal(v) for k, v in env.items()})
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            continue
        if not math.isfinite(got):
            continue
        worst = max(worst, abs(Decimal(got) - ref))
    return float(worst)


def main(names=None):
    names = names or list(FPBENCH_CASES)
    rows = []
    for name in names:
        case = FPBENCH_CASES[name]
        rng = random.Random(SEED)
        domain = case['domain']

        t0 = time.perf_counter()
        base_cost, base_bound, _, _, _ = tree_cost(case['expr'], domain)
        eg = EGraph()
        root = eg.add_expr(case['expr'])
        eg.saturate(RULES, iters=case.get('iters', 6),
                    node_limit=case.get('node_limit', 20000), domain=domain)
        front, _ = pareto_extract(eg, root, domain, keep=8)
        elapsed = time.perf_counter() - t0

        best = min(front, key=lambda p: (p[1], p[0])) if front else None
        rows.append({
            'name': name,
            'base_bound': base_bound,
            'best_bound': best[1] if best else None,
            'best_form': to_text(best[2]) if best else None,
            'best_cost': best[0] if best else None,
            'measured_base': measured_error(case['expr'], case['expr'], domain, rng),
            'measured_best': measured_error(best[2], case['expr'], domain, rng) if best else None,
            'seconds': elapsed,
            'nodes': eg.size()[0],
            'front': len(front),
        })
        print(f"{name:<14} {elapsed:6.1f} c  граница {base_bound:.3e} -> "
              f"{(best[1] if best else float('nan')):.3e}")

    (BENCH / 'fpbench_results.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=1, default=float), encoding='utf-8')

    out = ['FPBench (набор rosa): наши границы, реальная ошибка и время поиска.',
           'Те же задачи используют FPTaylor, Daisy, Gappa и Salsa — числа сравнимы напрямую.',
           '',
           f'{"case":<14}{"bound as written":>18}{"best bound":>13}{"measured":>12}'
           f'{"nodes":>8}{"sec":>7}  best form']
    for r in rows:
        bb = r['best_bound'] if r['best_bound'] is not None else float('nan')
        out.append(f'{r["name"]:<14}{r["base_bound"]:18.3e}{bb:13.3e}'
                   f'{(r["measured_best"] or 0.0):12.3e}{r["nodes"]:8d}{r["seconds"]:7.1f}  '
                   f'{(r["best_form"] or "")[:48]}')
    finite = [r for r in rows if r['best_bound'] and math.isfinite(r['best_bound'])]
    improved = [r for r in finite if r['best_bound'] < r['base_bound'] * 0.999]
    out += ['',
            f'улучшено по границе: {len(improved)} из {len(rows)}',
            f'суммарное время поиска: {sum(r["seconds"] for r in rows):.1f} c '
            f'на {len(rows)} задач']
    text = '\n'.join(out)
    (BENCH / 'fpbench_report.txt').write_text(text + '\n', encoding='utf-8')
    print()
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or None))
