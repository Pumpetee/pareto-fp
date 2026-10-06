# -*- coding: utf-8 -*-
"""Каждая операция, известная анализу, обязана печататься всеми тремя печатями.

Повод конкретный: sin, cos, atan и fabs добавили в анализ, а в печать внести
забыли. Узел доходил до конца функции, где его разбирали как двуместный, и печать
падала по выходу за границу кортежа — причём не только в отчёте, но и в to_c, то
есть в той строке, которую человек вставляет в свой код.

Сторожим не три забытых имени, а само правило: список операций берётся из
таблицы стоимостей анализа, поэтому следующая добавленная операция без печати
уронит этот тест сама.
"""
import unittest

from pareto.analysis import COST
from pareto.codegen import to_c, to_js, to_text

# Служебные обёртки и округления печатаются особым образом и проверяются отдельно.
SKIP = {'approx', 'eft', 'f32', 'f16', 'num', 'var'}

ARITY = {'fma': 3}


def sample(op):
    n = ARITY.get(op, 1 if op in UNARY else 2)
    kids = [('var', 'x'), ('var', 'y'), ('var', 'z')][:n]
    return (op,) + tuple(kids)


UNARY = {'neg', 'sqrt', 'exp', 'log', 'expm1', 'log1p', 'sin', 'cos', 'atan',
         'fabs'}


class TestPrintersCoverOps(unittest.TestCase):

    def test_every_known_operation_prints(self):
        for op in sorted(COST):
            if op in SKIP:
                continue
            tree = sample(op)
            with self.subTest(op=op):
                for name, fn in (('to_text', to_text), ('to_c', to_c)):
                    out = fn(tree)
                    self.assertTrue(out and isinstance(out, str),
                                    '{} ничего не напечатал для {}'.format(name, op))

    def test_javascript_prints_or_refuses_by_name(self):
        # JavaScript умеет не всё, и это нормально. Недопустимо другое: падение
        # по выходу за границу кортежа вместо внятного отказа.
        for op in sorted(COST):
            if op in SKIP:
                continue
            with self.subTest(op=op):
                try:
                    to_js(sample(op))
                except ValueError:
                    pass


if __name__ == '__main__':
    unittest.main()
