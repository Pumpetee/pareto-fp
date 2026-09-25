# -*- coding: utf-8 -*-
"""Фаззинг границы: случайные выражения, случайные домены, никакой пощады.

Прежние тесты проверяли те кейсы, которые автор сам и выбрал. Ровно поэтому ошибка
в вычитании интервалов прожила до внешнего разбора: она пряталась в домене, куда
никто не смотрел. Здесь выражения и домены генерируются случайно, а проверяется
единственное, но главное утверждение проекта:

    измеренная ошибка формы ≤ граница, которую напечатал анализ

Проверяется и для исходного выражения, и для каждой точки извлечённого фронта —
включая компенсированные формы и разложения в ряд, у которых своя модель ошибки.
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, refine_front, tree_cost, tree_cost_refined
from pareto.codegen import to_text
from pareto.egraph import EGraph
from pareto.rules import RULES
from pareto.run import exact_stable
from pareto.run_herbie_metric import eval_float

getcontext().prec = 60

SEED = int(__import__('os').environ.get('PARETO_FUZZ_SEED', 20260925))
EXPRESSIONS = int(__import__('os').environ.get('PARETO_FUZZ_N', 250))
POINTS = 120            # точек замера на форму
MAX_DEPTH = 3

BIN_OPS = ('+', '-', '*', '/')
UN_OPS = ('sqrt', 'exp', 'log', 'neg')


def random_tree(rng, names, depth=0):
    if depth >= MAX_DEPTH or rng.random() < 0.3:
        if rng.random() < 0.7:
            return ('var', rng.choice(names))
        return ('num', round(rng.uniform(-4.0, 4.0), 3))
    if rng.random() < 0.25:
        return (rng.choice(UN_OPS), random_tree(rng, names, depth + 1))
    return (rng.choice(BIN_OPS),
            random_tree(rng, names, depth + 1),
            random_tree(rng, names, depth + 1))


def random_domain(rng, names):
    """Домены намеренно разные: около нуля, большие, маленькие, смешанных знаков."""
    dom = {}
    for n in names:
        kind = rng.choice(('small', 'unit', 'large', 'tiny', 'shifted'))
        if kind == 'small':
            lo = rng.uniform(0.1, 3.0)
        elif kind == 'unit':
            lo = rng.uniform(0.5, 2.0)
        elif kind == 'large':
            lo = rng.uniform(1e5, 1e7)
        elif kind == 'tiny':
            lo = rng.uniform(1e-8, 1e-5)
        else:
            lo = rng.uniform(100.0, 1000.0)
        dom[n] = (lo, lo + abs(lo) * rng.uniform(0.001, 0.5))
    return dom


def measure(tree, expr, domain, rng):
    """Наибольшая абсолютная ошибка формы против точного значения выражения."""
    names = sorted(domain)
    pts = [{n: domain[n][0] for n in names}, {n: domain[n][1] for n in names}]
    pts += [{n: rng.uniform(*domain[n]) for n in names} for _ in range(POINTS)]
    worst = Decimal(0)
    scale = Decimal(0)          # порядок самого значения: нужен для допуска эталона
    for env in pts:
        try:
            got = eval_float(tree, env)
            ref = exact_stable(expr, {k: Decimal(v) for k, v in env.items()})
            if ref is None:
                continue        # эталон сам себе не доверяет — такую точку не судим
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            continue
        if not math.isfinite(got):
            continue
        worst = max(worst, abs(Decimal(got) - ref))
        scale = max(scale, abs(ref))
    return worst, scale


class FuzzBound(unittest.TestCase):
    def test_bound_holds_on_random_expressions(self):
        rng = random.Random(SEED)
        checked_forms = 0
        violations = []

        for _ in range(EXPRESSIONS):
            names = ['x', 'y'][:rng.choice([1, 2])]
            expr = random_tree(rng, names)
            domain = random_domain(rng, names)

            try:
                _, base_bound, base_iv, _, _ = tree_cost(expr, domain)
            except (ValueError, ZeroDivisionError, OverflowError, KeyError):
                continue
            if not math.isfinite(base_bound) or not all(math.isfinite(v) for v in base_iv):
                continue

            forms = [expr]
            try:
                eg = EGraph()
                root = eg.add_expr(expr)
                eg.saturate(RULES, iters=4, node_limit=4000, domain=domain)
                front, _ = pareto_extract(eg, root, domain, keep=5)
                forms += [p[2] for p in front]
                # Проверяем УТОЧНЁННЫЕ границы, а не только дешёвые: ветвление по
                # домену — это оптимизация, и звучит она безопасно ровно до того
                # момента, когда из-за неё напечатается граница ниже настоящей ошибки.
                front = refine_front(front, domain)
                bounds = [tree_cost_refined(expr, domain)[1]] + [p[1] for p in front]
            except Exception:
                bounds = [tree_cost_refined(expr, domain)[1]]

            for tree, bound in zip(forms, bounds):
                if not math.isfinite(bound):
                    continue
                worst, scale = measure(tree, expr, domain, rng)
                if worst == 0:
                    continue
                checked_forms += 1
                # Эталон сам не бесконечно точен: Decimal считает на 60 значащих
                # цифрах, и на малых значениях его собственная погрешность даёт
                # мнимые «нарушения» порядка 1e-66 у форм вроде -x, точных по
                # определению. Допуск берём с запасом над этим пределом.
                # Масштаб берём и от значения, и от домена: если выражение точно
                # равно нулю, |ref| = 0, и допуск обнулялся бы вместе с ним, хотя
                # погрешность самого Decimal никуда не девается.
                dom_scale = max(max(abs(a), abs(b)) for a, b in domain.values())
                tolerance = bound * 1.000001 + (float(scale) + dom_scale) * 1e-55
                if float(worst) > tolerance:
                    violations.append((to_text(expr)[:60], to_text(tree)[:60],
                                       bound, float(worst), dict(domain)))

        self.assertGreater(checked_forms, 50, 'фаззер почти ничего не проверил')
        msg = '\n'.join(
            f'  выражение {e}\n    форма {f}\n    граница {b:.3e} < измеренной {w:.3e}, домен {d}'
            for e, f, b, w, d in violations[:5])
        self.assertFalse(violations,
                         f'граница нарушена на {len(violations)} формах:\n{msg}')


if __name__ == '__main__':
    unittest.main()
