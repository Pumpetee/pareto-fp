# -*- coding: utf-8 -*-
"""Минимальный набор проверок: парсер, насыщение, фронт, CLI.

Запуск: python -m unittest discover -s tests
Ни node, ни компилятор не нужны — только питон, чтобы гонять это в CI.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import tree_cost
from pareto.cli import analyse, parse_domain
from pareto.codegen import to_c, to_text
from pareto.parser import ParseError, parse, variables


class TestParser(unittest.TestCase):
    def test_priority(self):
        self.assertEqual(to_text(parse('a + b * c')), '(a + (b * c))')
        self.assertEqual(to_text(parse('(a + b) * c')), '((a + b) * c)')

    def test_functions(self):
        self.assertEqual(to_text(parse('sqrt(x + 1)')), 'sqrt((x + 1))')
        self.assertEqual(to_text(parse('log(a) - log(b)')), '(log(a) - log(b))')

    def test_power_expands(self):
        self.assertEqual(to_text(parse('x^3')), '((x * x) * x)')
        self.assertEqual(to_text(parse('x**2')), '(x * x)')

    def test_scientific_numbers(self):
        self.assertEqual(to_text(parse('1e-9 * x')), '(1e-09 * x)')

    def test_unary_minus(self):
        self.assertEqual(to_text(parse('-x + y')), '(-x + y)')

    def test_variables_in_order(self):
        self.assertEqual(variables(parse('x*y + z*x')), ['x', 'y', 'z'])

    def test_errors_are_readable(self):
        with self.assertRaises(ParseError):
            parse('sin(x)')          # функция не поддержана
        with self.assertRaises(ParseError):
            parse('a + ')            # оборвалось
        with self.assertRaises(ParseError):
            parse('a $ b')           # мусорный символ


class TestDomain(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_domain(['x=1..2'], ['x']), {'x': (1.0, 2.0)})

    def test_missing_variable_is_fatal(self):
        with self.assertRaises(SystemExit):
            parse_domain(['x=1..2'], ['x', 'y'])


class TestAnalysis(unittest.TestCase):
    def test_sq_diff_becomes_exact(self):
        """Разность квадратов переписывается там, где это реально выигрывает.

        Домен взят с близкими x и y — именно там теряются значащие цифры и
        произведение суммы на разность окупается. На широком домене вроде
        [1,2]×[1,2] выигрыша по АБСОЛЮТНОЙ границе нет, и раньше тест этого не
        замечал только потому, что вычитание интервалов было сломано и граница
        схлопывалась в ноль.
        """
        r = analyse('x*x - y*y', {'x': (1000.0, 1000.001), 'y': (999.999, 1000.0)})
        self.assertLess(r['most_exact']['err'], r['base']['err'])
        self.assertIn('+', r['most_exact']['form'])

    def test_bound_is_not_zero_on_a_wide_domain(self):
        """Граница обязана быть положительной там, где округление есть.

        Регрессия на конкретный дефект: пример из README печатал границу 0 при
        реальной ошибке около 7e-16.
        """
        r = analyse('x*x - y*y', {'x': (1.0, 2.0), 'y': (1.0, 2.0)})
        self.assertGreater(r['base']['err'], 0.0)
        for point in r['front']:
            self.assertGreater(point['err'], 0.0, f"нулевая граница у формы {point['form']}")

    def test_diff_sqrt_gets_stable_form(self):
        """Разность корней — классическое сокращение значащих цифр."""
        r = analyse('sqrt(x+1) - sqrt(x)', {'x': (1e6, 1e9)})
        self.assertLess(r['most_exact']['err'], r['base']['err'] / 1000)

    def test_front_is_not_empty_and_sorted(self):
        r = analyse('(a/c) + (b/c)', {'a': (1.0, 2.0), 'b': (1.0, 2.0), 'c': (1.0, 2.0)})
        costs = [p['cost'] for p in r['front']]
        self.assertTrue(r['front'])
        self.assertEqual(costs, sorted(costs))

    def test_cost_matches_tree(self):
        tree = parse('a * b + c')
        cost, err, _, _, _ = tree_cost(tree, {'a': (1.0, 2.0), 'b': (1.0, 2.0), 'c': (1.0, 2.0)})
        self.assertGreater(cost, 0)
        self.assertGreaterEqual(err, 0)

    def test_c_output_is_compilable_text(self):
        r = analyse('x*x - y*y', {'x': (1.0, 2.0), 'y': (1.0, 2.0)})
        self.assertNotIn('num', r['fastest']['c'])
        self.assertEqual(r['fastest']['c'].count('('), r['fastest']['c'].count(')'))


class TestMlir(unittest.TestCase):
    def test_module_shape(self):
        from pareto.to_mlir import to_mlir
        text = to_mlir(parse('x*x - y*y'), 'sq')
        self.assertIn('func.func @sq(%arg0: f64, %arg1: f64) -> f64', text)
        self.assertIn('arith.mulf', text)
        self.assertIn('arith.subf', text)
        self.assertTrue(text.strip().endswith('}'))

    def test_argument_order_is_fixed_by_caller(self):
        """Регрессия: форма упоминает переменные в другом порядке, чем исходная.

        Пока порядок выводился из самой формы, вызывающий код подавал значения не в
        те параметры, и ошибка на two_div вырастала с 1e-16 до 6.99.
        """
        from pareto.to_mlir import to_mlir
        text = to_mlir(parse('(b + a) / c'), 'f', vars_=['a', 'b', 'c'])
        head = [l for l in text.splitlines() if 'func.func' in l][0]
        self.assertIn('%arg0: f64, %arg1: f64, %arg2: f64', head)
        # a — первый аргумент, значит в сложении он должен стоять как %arg0
        add = [l for l in text.splitlines() if 'arith.addf' in l][0]
        self.assertIn('%arg1, %arg0', add)

    def test_unknown_variable_is_rejected(self):
        from pareto.to_mlir import to_mlir
        with self.assertRaises(ValueError):
            to_mlir(parse('a + b'), 'f', vars_=['a'])

    def test_math_dialect_used_for_functions(self):
        from pareto.to_mlir import to_mlir
        text = to_mlir(parse('sqrt(x) + log(x)'), 'f')
        self.assertIn('math.sqrt', text)
        self.assertIn('math.log', text)


if __name__ == '__main__':
    unittest.main()
