# -*- coding: utf-8 -*-
"""Граница для арксинуса и арккосинуса.

Через них считаются крен и тангаж, то есть главные величины автопилота. Новая
операция без проверки против эталона — это новый способ соврать, поэтому здесь
измеренная ошибка сравнивается с напечатанной границей на точках, прижатых к
концам отрезка: там производная бесконечна и работает только гёльдерова оценка.
"""
import math
import random
import unittest
from decimal import Decimal

from pareto.analysis import ASIN_HOLDER, tree_cost_refined
from pareto.evalfp import eval_float
from pareto.exactref import exact_stable


class TestAsinHolder(unittest.TestCase):

    def test_holder_constant_is_the_tight_one(self):
        # |asin(x) - asin(y)| <= C*sqrt|x-y| при C = pi/sqrt(2), и на паре
        # (-1, 1) это равенство. Если константу уменьшить, правило сломается
        # именно там, поэтому проверка смотрит на концы.
        self.assertAlmostEqual(ASIN_HOLDER, math.pi / math.sqrt(2.0), places=12)
        d = 2.0
        self.assertAlmostEqual(abs(math.asin(1.0) - math.asin(-1.0)),
                               ASIN_HOLDER * math.sqrt(d), places=12)

    def test_bound_is_finite_inside_the_interval(self):
        for src in (('asin', ('var', 'x')), ('acos', ('var', 'x'))):
            with self.subTest(src[0]):
                b = tree_cost_refined(src, {'x': (-0.5, 0.5)})[1]
                self.assertTrue(math.isfinite(b))

    def test_out_of_range_argument_is_refused(self):
        # Выход за отрезок означает NaN в живом коде. Отвечать на это конечной
        # границей нельзя: ровно так угол ориентации и становится NaN.
        for src in (('asin', ('var', 'x')), ('acos', ('var', 'x'))):
            with self.subTest(src[0]):
                b = tree_cost_refined(src, {'x': (-2.0, 2.0)})[1]
                self.assertFalse(math.isfinite(b))

    def test_measured_error_stays_under_the_bound(self):
        rng = random.Random(20261007)
        trees = [
            ('asin', ('*', ('var', 'x'), ('var', 'y'))),
            ('acos', ('*', ('var', 'x'), ('var', 'y'))),
            ('*', ('asin', ('var', 'x')), ('var', 'y')),
        ]
        for tree in trees:
            dom = {'x': (-1.0, 1.0), 'y': (-1.0, 1.0)}
            bound = tree_cost_refined(tree, dom)[1]
            self.assertTrue(math.isfinite(bound), 'граница обязана быть конечной')
            for _ in range(600):
                # Точки нарочно у концов: там производная бесконечна.
                pick = rng.random()
                if pick < 0.3:
                    x = rng.choice([1.0, -1.0, 1 - 1e-16, -1 + 1e-16])
                elif pick < 0.5:
                    x = rng.choice([1 - 1e-8, -1 + 1e-8, 1e-18, 0.0])
                else:
                    x = rng.uniform(-1, 1)
                pt = {'x': x, 'y': rng.uniform(-1, 1)}
                got = eval_float(tree, pt)
                if got != got:
                    continue           # вне области: такую точку не судим
                ref = exact_stable(tree, {k: Decimal(v) for k, v in pt.items()})
                if ref is None:
                    continue
                err = abs(Decimal(got) - ref)
                self.assertLessEqual(
                    err, Decimal(bound) * Decimal('1.000001'),
                    'на точке {} измеренная ошибка {} вышла за границу {}'.format(
                        pt, err, bound))


if __name__ == '__main__':
    unittest.main()
