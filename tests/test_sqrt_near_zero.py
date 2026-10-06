# -*- coding: utf-8 -*-
import math
import unittest

from pareto.analysis import tree_cost_refined
from decimal import Decimal

from pareto.exactref import exact_stable
from pareto.evalfp import eval_float
from pareto.parser import parse


class TestSqrtNearZero(unittest.TestCase):
    """Корень на диапазоне, касающемся нуля.

    Производная корня в нуле бесконечна, и перенос ошибки через неё давал INF.
    Из-за одного этого места все функции длины и расстояния в чужих проектах
    считались недоказуемыми. Здесь сторожим два условия: граница конечна и
    граница по-прежнему верхняя.
    """

    CASES = [
        ('sqrt(x*x + y*y)', {'x': (-1e3, 1e3), 'y': (-1e3, 1e3)}),
        ('sqrt(x)', {'x': (0.0, 1.0)}),
        ('sqrt(x*x)', {'x': (-1.0, 1.0)}),
        ('sqrt(x + y)', {'x': (0.0, 1.0), 'y': (0.0, 1.0)}),
    ]

    def test_bound_is_finite(self):
        for src, dom in self.CASES:
            with self.subTest(src):
                b = tree_cost_refined(parse(src), dom)[1]
                self.assertTrue(math.isfinite(b),
                                'граница у корня у нуля обязана быть конечной')
                self.assertGreater(b, 0.0)

    def test_bound_still_holds_on_samples(self):
        # Точки прижаты к нулю нарочно: именно там гёльдерова оценка работает одна,
        # без поддержки производной, и именно там ошибка в ней была бы незаметна.
        for src, dom in self.CASES:
            tree = parse(src)
            bound = tree_cost_refined(tree, dom)[1]
            names = sorted(dom)
            for scale in (0.0, 1e-300, 1e-20, 1e-8, 1e-3, 1.0):
                pt = {}
                for v in names:
                    lo, hi = dom[v]
                    pt[v] = hi * scale if hi > 0 else lo * scale
                with self.subTest(src=src, scale=scale):
                    want = exact_stable(tree, {k: Decimal(v) for k, v in pt.items()})
                    if want is None:
                        continue      # эталон сам признался, что точности не хватило
                    got = eval_float(tree, pt)
                    err = abs(Decimal(got) - want)
                    self.assertLessEqual(err, Decimal(bound) * Decimal('1.000000001'),
                                         'наблюдаемая ошибка вышла за границу')


if __name__ == '__main__':
    unittest.main()


class TestSqrtDomainHazard(unittest.TestCase):
    """Корень от значения, которое код способен увидеть отрицательным.

    Интервальный корень зажимал отрицательный конец в ноль, и на sqrt(x - y) при
    x, y из [0, 1] инструмент печатал уверенную границу 1.05e-08 — при том что
    для y > x живой код возвращает NaN. Уверенная цифра на месте NaN хуже отказа,
    поэтому здесь граница обязана быть бесконечной.
    """

    def test_subtraction_under_sqrt_is_refused(self):
        b = tree_cost_refined(parse('sqrt(x - y)'), {'x': (0.0, 1.0), 'y': (0.0, 1.0)})[1]
        self.assertFalse(math.isfinite(b),
                         'на NaN нельзя отвечать конечной границей')

    def test_sum_of_squares_is_still_proved(self):
        # Отказывать по одному отрицательному концу интервала нельзя: сумма
        # квадратов в IEEE-754 отрицательной не бывает, и победу по трём проектам
        # такой перестраховкой терять нельзя.
        for src in ('sqrt(x*x + y*y)', 'sqrt(x*x + 1)', 'sqrt(x*x)'):
            with self.subTest(src):
                dom = {'x': (-1e3, 1e3), 'y': (-1e3, 1e3)}
                b = tree_cost_refined(parse(src), dom)[1]
                self.assertTrue(math.isfinite(b), src)
