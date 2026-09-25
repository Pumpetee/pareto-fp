# -*- coding: utf-8 -*-
"""Интервал обязан накрывать всё, что операция может вернуть на домене.

Появился после разбора 25.09.2026: вычитание интервалов было записано покоординатно,
(a0−b0, a1−b1), и на [1,2]−[1,2] давало (0,0) вместо (−1,1). Интервал схлопывался в
точку, а вместе с ним обнулялась и «доказанная граница» — на примере из README она
печаталась как 0 при реальной ошибке около 7e-16.

Проверка property-based: для каждой операции берутся случайные интервалы, по сетке
точек внутри считается настоящее значение, и результат обязан лежать внутри
интервала, выданного анализом. Такой тест ловит ошибку в любой из операций, а не
только в той, про которую вспомнили.
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import eval_interval, tree_cost
from pareto.parser import parse

SEED = 20260925
GRID = 12          # точек на переменную
TRIALS = 40        # случайных интервалов на операцию

UNARY = {
    'neg': lambda x: -x,
    'sqrt': math.sqrt,
    'exp': math.exp,
    'log': math.log,
    'expm1': math.expm1,
    'log1p': math.log1p,
}
BINARY = {
    '+': lambda a, b: a + b,
    '-': lambda a, b: a - b,
    '*': lambda a, b: a * b,
    '/': lambda a, b: a / b,
    'hypot': math.hypot,
}


def grid(iv, n=GRID):
    lo, hi = iv
    if lo == hi:
        return [lo]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


class IntervalsCover(unittest.TestCase):
    def test_unary_operations_cover_their_range(self):
        rng = random.Random(SEED)
        for op, fn in UNARY.items():
            for _ in range(TRIALS):
                lo = rng.uniform(0.25, 8.0) * rng.choice([1.0, -1.0])
                hi = lo + rng.uniform(0.0, 6.0)
                if op in ('sqrt', 'log'):
                    lo, hi = abs(lo) + 0.1, abs(hi) + 0.1
                if op == 'log1p':
                    lo, hi = max(lo, -0.5), max(hi, 0.5)
                lo, hi = min(lo, hi), max(lo, hi)   # после поправок порядок мог сбиться
                iv = eval_interval(op, [(lo, hi)])
                for x in grid((lo, hi)):
                    val = fn(x)
                    self.assertLessEqual(iv[0] - 1e-9 * max(1.0, abs(val)), val,
                                         f'{op}: {val} ниже интервала {iv} на [{lo}, {hi}]')
                    self.assertLessEqual(val, iv[1] + 1e-9 * max(1.0, abs(val)),
                                         f'{op}: {val} выше интервала {iv} на [{lo}, {hi}]')

    def test_binary_operations_cover_their_range(self):
        rng = random.Random(SEED + 1)
        for op, fn in BINARY.items():
            for _ in range(TRIALS):
                a0 = rng.uniform(-6.0, 6.0)
                a1 = a0 + rng.uniform(0.0, 5.0)
                b0 = rng.uniform(-6.0, 6.0)
                b1 = b0 + rng.uniform(0.0, 5.0)
                if op == '/' and b0 <= 0.0 <= b1:
                    b0, b1 = 1.0, 3.0            # деление на интервал с нулём не определено
                iv = eval_interval(op, [(a0, a1), (b0, b1)])
                for x in grid((a0, a1)):
                    for y in grid((b0, b1)):
                        val = fn(x, y)
                        tol = 1e-9 * max(1.0, abs(val))
                        self.assertLessEqual(iv[0] - tol, val,
                                             f'{op}: {val} ниже {iv} на [{a0},{a1}] и [{b0},{b1}]')
                        self.assertLessEqual(val, iv[1] + tol,
                                             f'{op}: {val} выше {iv} на [{a0},{a1}] и [{b0},{b1}]')

    def test_readme_example_has_a_real_bound(self):
        """Тот самый пример, на котором граница печаталась нулём."""
        tree = parse('x*x - y*y')
        dom = {'x': (1.0, 2.0), 'y': (1.0, 2.0)}
        _, bound, iv, _, _ = tree_cost(tree, dom)
        self.assertLessEqual(iv[0], -2.9, f'интервал {iv} не накрывает −3')
        self.assertGreaterEqual(iv[1], 2.9, f'интервал {iv} не накрывает 3')

        rng = random.Random(SEED + 2)
        worst = 0.0
        from decimal import Decimal, getcontext
        getcontext().prec = 60
        for _ in range(400):
            x = rng.uniform(*dom['x'])
            y = rng.uniform(*dom['y'])
            got = x * x - y * y
            ref = Decimal(x) * Decimal(x) - Decimal(y) * Decimal(y)
            worst = max(worst, abs(Decimal(got) - ref))
        self.assertGreater(bound, 0.0, 'граница обязана быть положительной')
        self.assertLessEqual(float(worst), bound,
                             f'измеренная ошибка {float(worst):.3e} больше границы {bound:.3e}')


if __name__ == '__main__':
    unittest.main()
