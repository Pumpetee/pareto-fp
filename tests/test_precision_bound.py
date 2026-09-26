# -*- coding: utf-8 -*-
"""Фаззинг границы при смешанной точности.

Проверяется то же единственное утверждение, что и для binary64:

    измеренная ошибка ≤ напечатанная граница

но теперь выражения содержат узлы округления к binary32 и binary16, расставленные
случайно. Смысл именно в случайной расстановке: однородный float ловит одни
дефекты модели, а смесь — совсем другие, потому что в ней ошибка узкого узла
дальше протаскивается через широкую арифметику и наоборот.

Эталон один и тот же для всех расстановок: идеальное вещественное значение
выражения, посчитанное на Decimal. Узлы округления в эталоне прозрачны — они выбор
реализации, а не часть математики.
"""
from __future__ import annotations

import math
import os
import random
import sys
import unittest
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import combined_bound, tree_cost, tree_cost_refined
from pareto.codegen import to_c, to_text
from pareto.mixed import strip_rounding, tune, uniform_candidates
from pareto.parser import parse
from pareto.precision import FLOAT16, FLOAT32, FLOAT64
from pareto.exactref import exact_stable
from pareto.evalfp import eval_float

getcontext().prec = 60

SEED = int(os.environ.get('PARETO_FUZZ_SEED', 20260926))
EXPRESSIONS = int(os.environ.get('PARETO_PREC_FUZZ_N', 200))
POINTS = 80
MAX_DEPTH = 3

BIN_OPS = ('+', '-', '*', '/')
UN_OPS = ('sqrt', 'exp', 'log', 'neg')


class PrecisionFormats(unittest.TestCase):
    def test_ulp_matches_the_standard(self):
        # улпа единицы: 2^-52 в double, 2^-23 в float, 2^-10 в half
        self.assertEqual(FLOAT64.ulp(1.0), 2.0 ** -52)
        self.assertEqual(FLOAT32.ulp(1.0), 2.0 ** -23)
        self.assertEqual(FLOAT16.ulp(1.0), 2.0 ** -10)
        # и она монотонна по величине
        for fmt in (FLOAT64, FLOAT32, FLOAT16):
            self.assertLessEqual(fmt.ulp(1.0), fmt.ulp(2.0))
            self.assertLessEqual(fmt.ulp(0.4), fmt.ulp(1.0))

    def test_rounding_is_nearest(self):
        # 0.1 в binary32 — известное значение, его печатает любой отладчик
        self.assertEqual(FLOAT32.round(0.1), 0.10000000149011612)
        self.assertEqual(FLOAT32.round(0.0), 0.0)
        # и сама операция округления — половина улпы, не больше
        rng = random.Random(7)
        for _ in range(2000):
            v = rng.uniform(-1e6, 1e6)
            r = FLOAT32.round(v)
            self.assertLessEqual(abs(r - v), FLOAT32.half_ulp(max(abs(v), abs(r))))

    def test_overflow_is_infinity_not_a_clamp(self):
        self.assertEqual(FLOAT32.round(1e40), float('inf'))
        self.assertEqual(FLOAT32.round(-1e40), float('-inf'))
        # и анализ обязан назвать такую границу бесконечной, а не напечатать число
        err = combined_bound(('f32', ('*', ('var', 'x'), ('var', 'x'))), {'x': (1e30, 2e30)})
        self.assertFalse(math.isfinite(err))

    def test_double_rounding_to_the_same_format_is_free(self):
        dom = {'x': (1.0, 2.0), 'y': (1.0, 2.0)}
        once = ('f32', ('+', ('var', 'x'), ('var', 'y')))
        twice = ('f32', once)
        self.assertEqual(tree_cost(once, dom)[1], tree_cost(twice, dom)[1])

    def test_narrow_bound_is_worse_than_wide(self):
        dom = {'x': (1.0, 2.0), 'y': (1.0, 2.0)}
        expr = ('*', ('+', ('var', 'x'), ('var', 'y')), ('var', 'x'))
        wide = combined_bound(expr, dom)
        narrow = combined_bound(('f32', expr), dom)
        half = combined_bound(('f16', expr), dom)
        self.assertLess(wide, narrow)
        self.assertLess(narrow, half)

    def test_round_trip_through_the_parser(self):
        for text in ('f32(x + y)', 'f16(x * f32(y))', 'f32(sqrt(x)) - y'):
            tree = parse(text)
            again = parse(to_text(tree))
            self.assertEqual(tree, again, text)

    def test_c_output_keeps_the_cast(self):
        self.assertEqual(to_c(parse('f32(x + y)')), '(float)((x + y))')


class Tuning(unittest.TestCase):
    def test_uniform_candidates_are_ordered_by_format_width(self):
        dom = {'x': (1.0, 2.0), 'y': (1.0, 2.0)}
        tree = parse('(x + y) * x')
        got = {name: err for name, _, err in uniform_candidates(tree, dom)}
        self.assertLess(got['float64'], got['float32'])
        self.assertLess(got['float32'], got['float16'])

    def test_tune_narrows_what_it_can_and_keeps_the_promise(self):
        dom = {'x': (1.0, 2.0), 'y': (1.0, 2.0)}
        tree = parse('(x + y) * (x - y) + x * y')
        # Цель должна быть слабее не самой границы в double, а ЦЕНЫ одного узла в
        # float: на значениях порядка единиц половина улпы binary32 это около
        # 2.4e-07, и цель в 1e-10 не пропустила бы ни одного сужения, сколько бы
        # запаса ни было против double.
        target = 1e-5
        self.assertLess(combined_bound(tree, dom), target)
        best, err, narrowed = tune(tree, dom, target)
        self.assertGreater(narrowed, 0, 'ни один узел не сужен, хотя запас огромный')
        self.assertLessEqual(err, target)
        # и обещание проверяется замером, а не только моделью
        worst = measure_worst(best, dom, random.Random(3), points=400)
        self.assertLessEqual(worst, err * 1.000001 + 1e-300)

    def test_tune_refuses_an_impossible_target(self):
        dom = {'x': (1.0, 2.0)}
        tree = parse('x * x')
        best, err, narrowed = tune(tree, dom, 1e-300)
        self.assertEqual(narrowed, 0)
        self.assertGreater(err, 1e-300)


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


def sprinkle_rounding(rng, tree, chance=0.35):
    """Случайно обернуть узлы округлением к узкому формату."""
    if tree[0] in ('num', 'var'):
        node = tree
    else:
        node = (tree[0],) + tuple(sprinkle_rounding(rng, k, chance) for k in tree[1:])
    if rng.random() < chance:
        return (rng.choice(('f32', 'f16')), node)
    return node


def random_domain(rng, names):
    dom = {}
    for n in names:
        kind = rng.choice(('small', 'unit', 'large', 'tiny'))
        if kind == 'small':
            lo = rng.uniform(0.1, 3.0)
        elif kind == 'unit':
            lo = rng.uniform(0.5, 2.0)
        elif kind == 'large':
            lo = rng.uniform(1e3, 1e5)
        else:
            lo = rng.uniform(1e-4, 1e-2)
        dom[n] = (lo, lo + abs(lo) * rng.uniform(0.001, 0.5))
    return dom


def measure_worst(tree, domain, rng, points=POINTS):
    """Наибольшая измеренная ошибка формы против идеального значения."""
    ideal = strip_rounding(tree)
    names = sorted(domain)
    pts = [{n: domain[n][0] for n in names}, {n: domain[n][1] for n in names}]
    pts += [{n: rng.uniform(*domain[n]) for n in names} for _ in range(points)]
    worst = 0.0
    for env in pts:
        try:
            got = eval_float(tree, env)
            ref = exact_stable(ideal, {k: Decimal(v) for k, v in env.items()})
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            continue
        if ref is None or not math.isfinite(got):
            continue
        worst = max(worst, float(abs(Decimal(got) - ref)))
    return worst


class FuzzMixedBound(unittest.TestCase):
    def test_bound_holds_with_random_format_placement(self):
        rng = random.Random(SEED)
        checked = 0
        violations = []
        for _ in range(EXPRESSIONS):
            names = ['x', 'y'][:rng.choice([1, 2])]
            plain = random_tree(rng, names)
            tree = sprinkle_rounding(rng, plain)
            if tree == plain:
                continue                       # без узкого формата это другой тест
            domain = random_domain(rng, names)
            try:
                bound = tree_cost_refined(tree, domain)[1]
            except (ValueError, ZeroDivisionError, OverflowError, KeyError):
                continue
            if not math.isfinite(bound):
                continue
            worst = measure_worst(tree, domain, rng)
            if worst == 0.0:
                continue
            checked += 1
            dom_scale = max(max(abs(a), abs(b)) for a, b in domain.values())
            tolerance = bound * 1.000001 + dom_scale * 1e-55
            if worst > tolerance:
                violations.append((to_text(tree)[:70], bound, worst, dict(domain)))

        self.assertGreater(checked, 40, 'фаззер почти ничего не проверил')
        msg = '\n'.join('  форма {}\n    граница {:.3e} < измеренной {:.3e}, домен {}'
                        .format(f, b, w, d) for f, b, w, d in violations[:5])
        self.assertFalse(violations,
                         'граница нарушена на {} формах:\n{}'.format(len(violations), msg))


if __name__ == '__main__':
    unittest.main()
