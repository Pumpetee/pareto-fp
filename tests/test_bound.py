# -*- coding: utf-8 -*-
"""The central claim of this project, guarded by a test.

Every form on the Pareto front carries a statically derived bound on its absolute
error. If the bound can be smaller than the error that actually occurs, the whole
project is worthless. Until now that claim was only checked by hand on a report.

Points are drawn pseudo-randomly with a fixed seed plus the domain corners, on
purpose: catastrophic cancellation lives in narrow spots, and a uniform grid can
step straight over them.
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract
from pareto.egraph import EGraph
from pareto.rules import RULES
from pareto.run import CASES, exact

getcontext().prec = 60

# три кейса разной природы: сокращение на корнях, деление, катастрофическое сокращение
CHECKED = ('diff_sqrt', 'two_div', 'sq_diff')
POINTS = 200
SEED = 20260924


def eval_float(tree, env):
    """Считает дерево в обычном double — ровно так, как это сделает машина."""
    op = tree[0]
    if op == 'num':
        return float(tree[1])
    if op == 'var':
        return float(env[tree[1]])
    if op == 'neg':
        return -eval_float(tree[1], env)
    if op == 'sqrt':
        return math.sqrt(eval_float(tree[1], env))
    if op == 'exp':
        return math.exp(eval_float(tree[1], env))
    if op == 'log':
        return math.log(eval_float(tree[1], env))
    a = eval_float(tree[1], env)
    b = eval_float(tree[2], env)
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if op == '/':
        return a / b
    if op == 'fma':
        return math.fma(a, b, eval_float(tree[3], env)) if hasattr(math, 'fma') \
            else a * b + eval_float(tree[3], env)
    raise AssertionError('unknown node: ' + op)


def sample_points(case, rng):
    """Случайные точки домена плюс его углы — углы часто и есть худший случай."""
    domain = case['domain']
    names = sorted(domain)
    pts = [{n: domain[n][0] for n in names}, {n: domain[n][1] for n in names}]
    for _ in range(POINTS):
        pts.append({n: rng.uniform(*domain[n]) for n in names})
    return pts


class BoundHolds(unittest.TestCase):
    def test_measured_error_never_exceeds_bound(self):
        rng = random.Random(SEED)
        checked_forms = 0
        for name in CHECKED:
            case = CASES[name]
            eg = EGraph()
            root = eg.add_expr(case['expr'])
            eg.saturate(RULES, iters=case.get('iters', 6), node_limit=20000, domain=case['domain'])
            front, _ = pareto_extract(eg, root, case['domain'], keep=10)
            self.assertTrue(front, f'{name}: empty Pareto front')

            for point in front:
                cost, bound, tree = point[0], point[1], point[2]
                worst = Decimal(0)
                for env in sample_points(case, rng):
                    dec_env = {k: Decimal(float(v)) for k, v in env.items()}
                    ref = exact(case['expr'], dec_env)
                    got = Decimal(eval_float(tree, env))
                    worst = max(worst, abs(got - ref))
                checked_forms += 1
                self.assertLessEqual(
                    worst, Decimal(str(bound)) * Decimal('1.0'),
                    f'{name}: measured error {worst:.3E} exceeds the proven bound '
                    f'{bound:.3E} for form with cost {cost}')
        self.assertGreater(checked_forms, 3, 'too few forms checked to mean anything')


if __name__ == '__main__':
    unittest.main()
