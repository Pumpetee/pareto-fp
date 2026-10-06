# -*- coding: utf-8 -*-
import math
import unittest

from pareto.api import safety_envelope
from pareto.parser import parse


class TestSafetyEnvelope(unittest.TestCase):
    """Граница безопасности: до какой величины входов граница ещё доказуема.

    Проверяем не числа, а смысл ответа: «уже доказано», «доказано до такой доли»
    и «дело не в величине». Привязываться к точным значениям здесь нельзя — они
    законно меняются при каждом улучшении оценок.
    """

    def test_already_provable_reports_full_domain(self):
        tree = parse('x + y')
        k, full = safety_envelope(tree, {'x': (-1.0, 1.0), 'y': (-1.0, 1.0)})
        self.assertTrue(full)
        self.assertEqual(k, 1.0)

    def test_shape_problem_is_not_a_size_problem(self):
        # Деление на (x - y) при нуле внутри диапазона: сужение не спасает никогда,
        # и инструмент обязан сказать это, а не выдать крошечную огибающую.
        tree = parse('1 / (x - y)')
        k, full = safety_envelope(tree, {'x': (-1.0, 1.0), 'y': (-1.0, 1.0)})
        self.assertFalse(full)
        self.assertEqual(k, 0.0)

    def test_found_scale_is_actually_provable(self):
        tree = parse('x * x + y * y')
        dom = {"x": (-1e200, 1e200), "y": (-1e200, 1e200)}
        k, full = safety_envelope(tree, dom)
        self.assertFalse(full)
        self.assertGreater(k, 0.0)
        from pareto.analysis import tree_cost_refined
        inside = {v: (lo * k, hi * k) for v, (lo, hi) in dom.items()}
        self.assertTrue(math.isfinite(tree_cost_refined(tree, inside)[1]),
                        'найденная огибающая обязана быть доказуемой внутри себя')

    def test_limit_is_respected(self):
        tree = parse('x * x')
        dom = {'x': (-1e10, 1e10)}
        loose, _ = safety_envelope(tree, dom)
        tight, _ = safety_envelope(tree, dom, limit=1.0)
        self.assertLessEqual(tight, loose,
                             'с требованием к точности огибающая не может расшириться')


if __name__ == '__main__':
    unittest.main()
