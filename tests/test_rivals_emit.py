# -*- coding: utf-8 -*-
"""Запись выражения для FPTaylor и Daisy обязана считать то же самое, что и мы.

Сравнение с соперником стоит ровно столько, сколько стоит уверенность, что ему
предъявили ТУ ЖЕ формулу. Печать в чужой синтаксис — самое тихое место, где эта
уверенность теряется: перепутанный приоритет операций или унарный минус меняют
задачу, а таблица продолжает выглядеть убедительно.

Поэтому тут не проверка строк на глаз, а проверка численная: напечатанное выражение
читается обратно (обе записи — обычная инфиксная арифметика, наш же парсер её берёт)
и считается в тех же точках, что и оригинал. Расхождение хотя бы в одном бите — ошибка
печати. Плюс проверка отбора форм: то, чего соперники не понимают, в сравнение не идёт.
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.parser import parse
from pareto.evalfp import eval_float
from pareto.run_rivals import shared_form, to_fptaylor, to_scala

SEED = 20260925
POINTS = 200

CASES = [
    ('x + x', {'x': (1.0, 20.0)}),
    ('(4*x) / (1 + x/1.11)', {'x': (0.1, 0.3)}),
    ('x*x - y*y', {'x': (1.0, 2.0), 'y': (1.0, 2.0)}),
    ('-x1 - x3 - x2*x1 - x2*(x3*2)', {'x1': (-15.0, 15.0), 'x2': (-15.0, 15.0),
                                      'x3': (-15.0, 15.0)}),
    ('sqrt(x + 1) - sqrt(x)', {'x': (1.0, 100.0)}),
    ('(a - b) / (a + b)', {'a': (2.0, 6.0), 'b': (0.5, 1.5)}),
]


class EmitKeepsTheMeaning(unittest.TestCase):

    def _check(self, text, domain, printer):
        tree = parse(text)
        printed = printer(tree)
        back = parse(printed)
        rng = random.Random(SEED)
        names = sorted(domain)
        for _ in range(POINTS):
            env = {n: rng.uniform(*domain[n]) for n in names}
            self.assertEqual(eval_float(tree, env), eval_float(back, env),
                             msg=f'{text!r} -> {printed!r} at {env}')

    def test_fptaylor_printer(self):
        for text, domain in CASES:
            with self.subTest(case=text):
                self._check(text, domain, to_fptaylor)

    def test_daisy_printer(self):
        for text, domain in CASES:
            with self.subTest(case=text):
                # Scala-печать отличается от нашей только скобками вокруг унарного минуса,
                # так что обратно она читается тем же парсером.
                self._check(text, domain, to_scala)

    def test_unary_minus_is_not_lost(self):
        # (-x) * y и -(x * y) совпадают по значению, но (-x - y) и -(x - y) — нет.
        tree = parse('-(x - y)')
        for printer in (to_fptaylor, to_scala):
            env = {'x': 1.0, 'y': 5.0}
            self.assertEqual(eval_float(parse(printer(tree)), env), 4.0)


class OnlyFormsRivalsUnderstand(unittest.TestCase):

    def test_plain_arithmetic_is_shared(self):
        self.assertTrue(shared_form(parse('(x + 1) * (x - 1)')))
        self.assertTrue(shared_form(parse('sqrt(x*x + 1) - 1')))

    def test_fma_is_not_shared(self):
        # FPTaylor раскрывает fma в два округления, у Daisy его нет вовсе:
        # предъявлять им такую форму как «ту же самую» нельзя.
        self.assertFalse(shared_form(parse('fma(x, y, 1)')))

    def test_extra_functions_are_not_shared(self):
        self.assertFalse(shared_form(parse('log1p(x)')))
        self.assertFalse(shared_form(parse('expm1(x)')))

    def test_approx_is_not_shared_but_eft_is(self):
        # approx — полином вместо функции, это уже другая математика.
        self.assertFalse(shared_form(('approx', parse('x + x*x'), 1e-9)))
        # eft — обычный код, в точной арифметике тождественный исходному.
        self.assertTrue(shared_form(('eft', parse('(x + y) - x'), parse('y'), 1)))


if __name__ == '__main__':
    unittest.main()
