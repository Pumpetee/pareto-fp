# -*- coding: utf-8 -*-
"""Границы для программы с ветвлениями: то, чего в проекте не было вовсе.

Проверяется главное утверждение, но теперь для программы, а не для выражения:

    |то, что посчитает машина − идеальное значение| ≤ напечатанная граница

Отдельно и подробно проверяется неустойчивый тест. Это место, где наивная
реализация врёт молча: она берёт максимум границ по ветвям и печатает его, а на
границе между ветвями программа уходит не туда, и ошибка оказывается размером со
СКАЧОК между ветвями. Поэтому здесь есть тест, который заведомо заставляет тест
сорваться, а не надеется на случайную точку.
"""
from __future__ import annotations

import math
import os
import random
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.api import analyse_c_function, rewritten_c
from pareto.program import (ProgramError, analyse_program, guard_status, paths,
                            run_exact, run_float, substitute, unroll)

SEED = int(os.environ.get('PARETO_FUZZ_SEED', 20260926))


def var(n):
    return ('var', n)


def num(v):
    return ('num', float(v))


class Paths(unittest.TestCase):
    def test_locals_are_inlined(self):
        stmts = [('let', 't', ('*', var('x'), var('x'))),
                 ('return', ('+', var('t'), var('t')))]
        p = paths(stmts)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0].expr, ('+', ('*', var('x'), var('x')), ('*', var('x'), var('x'))))

    def test_reassignment_does_not_recurse(self):
        # t = t + 1 после подстановки обязано быть конечным деревом
        stmts = [('let', 't1', var('x')),
                 ('let', 't2', ('+', var('t1'), num(1))),
                 ('return', var('t2'))]
        p = paths(stmts)
        self.assertEqual(p[0].expr, ('+', var('x'), num(1.0)))

    def test_branches_become_separate_paths(self):
        stmts = [('if', ('>', var('x'), num(0)),
                  [('return', var('x'))], [('return', ('neg', var('x')))])]
        p = paths(stmts)
        self.assertEqual(len(p), 2)
        self.assertEqual([q.label for q in p], ['T', 'F'])

    def test_early_return_keeps_the_tail(self):
        # if без else, а за ним ещё один return — это два пути, и второй берёт хвост
        stmts = [('if', ('<', var('x'), num(0)), [('return', num(0))], []),
                 ('return', ('sqrt', var('x')))]
        p = paths(stmts)
        self.assertEqual(len(p), 2)
        self.assertEqual(p[0].expr, num(0.0))
        self.assertEqual(p[1].expr, ('sqrt', var('x')))

    def test_missing_return_is_a_loud_refusal(self):
        with self.assertRaises(ProgramError):
            paths([('let', 't', var('x'))])

    def test_too_many_paths_is_a_loud_refusal(self):
        stmts = []
        for _ in range(8):
            stmts = [('if', ('>', var('x'), num(0)), list(stmts) or [('return', var('x'))],
                      list(stmts) or [('return', num(0))])]
            stmts = [stmts[0], ('return', var('x'))]
        with self.assertRaises(ProgramError):
            paths(stmts * 3)

    def test_unroll_is_exact_and_bounded(self):
        body = [('let', 's', ('+', var('s'), var('i')))]
        out = unroll('i', 0, 3, 1, body)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0][2], ('+', var('s'), num(0.0)))
        self.assertEqual(out[2][2], ('+', var('s'), num(2.0)))
        with self.assertRaises(ProgramError):
            unroll('i', 0, 1000, 1, body)


class GuardStatus(unittest.TestCase):
    def test_a_comparison_without_rounding_cannot_flip(self):
        # x > 1 при переменной x считается без единого округления
        cond = ('>', var('x'), num(1))
        value, unstable = guard_status(cond, {'x': (0.0, 2.0)})
        self.assertIsNone(value)
        self.assertFalse(unstable)

    def test_decided_on_a_narrow_box(self):
        cond = ('>', var('x'), num(1))
        self.assertEqual(guard_status(cond, {'x': (2.0, 3.0)})[0], True)
        self.assertEqual(guard_status(cond, {'x': (0.0, 0.5)})[0], False)

    def test_a_comparison_with_rounding_can_flip(self):
        # x*x - y*y около диагонали: разность считается с ошибкой порядка улпы
        # квадратов, то есть сравнение может уйти в любую сторону
        cond = ('>', ('-', ('*', var('x'), var('x')), ('*', var('y'), var('y'))), num(0))
        box = {'x': (1e8, 1e8 + 1e-9), 'y': (1e8, 1e8 + 1e-9)}
        value, unstable = guard_status(cond, box)
        self.assertTrue(unstable)


class StableBranches(unittest.TestCase):
    """Ветвление, где скачка нет: ветви сходятся на границе."""

    def test_continuous_branch_has_no_jump_term(self):
        # обе ветви на границе x = 1 дают одно и то же, но через рounding-ошибку
        stmts = [('let', 'c', ('-', ('*', var('x'), var('x')), num(1))),
                 ('if', ('>', var('c'), num(0)),
                  [('return', ('*', var('x'), var('x')))],
                  [('return', ('*', var('x'), var('x')))])]
        r = analyse_program(stmts, {'x': (0.5, 2.0)})
        self.assertEqual(r['divergence_extra'], 0.0)
        self.assertGreater(r['base_bound'], 0.0)
        self.assertLess(r['base_bound'], 1e-14)


class UnstableBranch(unittest.TestCase):
    """Заведомо сорванный тест: проверяем, что скачок попал в границу."""

    # Условие с катастрофическим сокращением: (x + 1e16) - 1e16 обязано быть равно
    # x, но сложение округляет до сетки с шагом 2, и вычитание возвращает 0 или 2,
    # а не x. Идеально условие c > 0 верно на всём домене; машина же для x < 1
    # получает ровно ноль и уходит в ДРУГУЮ ветку. Это и есть сорвавшийся тест, и
    # здесь он не предполагается, а воспроизводится.
    #
    # Предыдущая попытка — x*x - y*y > 0 на близких x и y — оказалась пустой, и это
    # полезный факт: один шаг улпы в x меняет x² примерно на две улпы, то есть
    # больше, чем ошибка вычисления самих квадратов. Такое сравнение не срывается
    # никогда, наш анализ помечает его неустойчивым лишь из осторожности.
    C = ('-', ('+', var('x'), num(1e16)), num(1e16))
    STMTS = [('if', ('>', C, num(0)), [('return', num(1))], [('return', num(2))])]
    DOM = {'x': (0.1, 1.9)}

    def test_the_test_really_flips(self):
        """Сначала убедимся, что случай настоящий, а не придуманный."""
        rng = random.Random(SEED)
        flips = 0
        for _ in range(500):
            env = {'x': rng.uniform(0.1, 1.9)}
            got = run_float(self.STMTS, env)
            ref = run_exact(self.STMTS, {k: Decimal(v) for k, v in env.items()})
            if ref is None:
                continue
            if abs(Decimal(got) - ref) > 0:
                flips += 1
        self.assertGreater(flips, 0, 'подобрать сорванный тест не удалось — тест бесполезен')

    def test_the_bound_covers_the_jump(self):
        r = analyse_program(self.STMTS, self.DOM)
        self.assertGreater(r['divergence_extra'], 0.0)
        # скачок между ветвями ровно единица, и граница обязана быть не меньше
        self.assertGreaterEqual(r['base_bound'], 1.0)

    def test_measured_error_stays_under_the_bound(self):
        r = analyse_program(self.STMTS, self.DOM)
        rng = random.Random(SEED + 1)
        worst = 0.0
        checked = 0
        for _ in range(1000):
            env = {'x': rng.uniform(0.1, 1.9)}
            got = run_float(self.STMTS, env)
            ref = run_exact(self.STMTS, {k: Decimal(v) for k, v in env.items()})
            if ref is None or not math.isfinite(got):
                continue
            checked += 1
            worst = max(worst, float(abs(Decimal(got) - ref)))
        self.assertGreater(checked, 500)
        self.assertGreater(worst, 0.0, 'ни одного срыва не поймано — нечего проверять')
        self.assertLessEqual(worst, r['base_bound'] * 1.000001)


class FuzzProgramBound(unittest.TestCase):
    """Случайные программы с ветвлениями против замера."""

    BIN = ('+', '-', '*', '/')

    def random_expr(self, rng, names, depth=0):
        if depth >= 2 or rng.random() < 0.35:
            if rng.random() < 0.75:
                return ('var', rng.choice(names))
            return num(round(rng.uniform(-3.0, 3.0), 3))
        if rng.random() < 0.2:
            return ('sqrt', ('+', self.random_expr(rng, names, depth + 1), num(4.0)))
        return (rng.choice(self.BIN), self.random_expr(rng, names, depth + 1),
                self.random_expr(rng, names, depth + 1))

    def random_program(self, rng, names):
        stmts = [('let', 'a', self.random_expr(rng, names)),
                 ('let', 'b', self.random_expr(rng, names))]
        cond = (rng.choice(('<', '>', '<=', '>=')), ('var', 'a'), ('var', 'b'))
        then_e = self.random_expr(rng, names + ['a'])
        else_e = self.random_expr(rng, names + ['b'])
        stmts.append(('if', cond, [('return', then_e)], [('return', else_e)]))
        return stmts

    def test_bound_holds_on_random_branchy_programs(self):
        rng = random.Random(SEED)
        checked = 0
        violations = []
        for _ in range(60):
            names = ['x', 'y']
            stmts = self.random_program(rng, names)
            lo = rng.uniform(0.5, 4.0)
            domain = {n: (lo, lo * rng.uniform(1.01, 2.0)) for n in names}
            try:
                r = analyse_program(stmts, domain, max_boxes=16, refine_boxes=4)
            except (ProgramError, ValueError, ZeroDivisionError, OverflowError, KeyError):
                continue
            bound = r['base_bound']
            if not math.isfinite(bound) or bound == 0.0:
                continue
            worst = 0.0
            points = 0
            for _ in range(60):
                env = {n: rng.uniform(*domain[n]) for n in names}
                try:
                    got = run_float(stmts, env)
                    ref = run_exact(stmts, {k: Decimal(v) for k, v in env.items()})
                except (ValueError, ZeroDivisionError, OverflowError, KeyError):
                    continue
                if ref is None or not math.isfinite(got):
                    continue
                points += 1
                worst = max(worst, float(abs(Decimal(got) - ref)))
            if points < 10:
                continue
            checked += 1
            if worst > bound * 1.000001 + 1e-300:
                violations.append((bound, worst, dict(domain)))
        self.assertGreater(checked, 15, 'фаззер почти ничего не проверил')
        self.assertFalse(violations, 'граница нарушена: {}'.format(violations[:3]))


class WholeFunction(unittest.TestCase):
    SRC = """
// @domain x: 1e6 .. 1e9
double diff_sqrt(double x) {
    double a = sqrt(x + 1.0);
    double b = sqrt(x);
    return a - b;
}
"""

    def test_a_function_from_source_is_improved(self):
        r = analyse_c_function(self.SRC, 'diff_sqrt')
        self.assertEqual(r['function'], 'diff_sqrt')
        self.assertEqual(len(r['paths']), 1)
        self.assertLess(r['best_bound'], r['base_bound'])
        body = rewritten_c(r)
        self.assertIn('return', body)

    def test_the_rewritten_function_is_measured_not_believed(self):
        r = analyse_c_function(self.SRC, 'diff_sqrt')
        form = r['paths'][0]['form']
        from pareto.mixed import strip_rounding
        from pareto.exactref import exact_stable
        from pareto.evalfp import eval_float
        rng = random.Random(SEED)
        worst = 0.0
        for _ in range(300):
            env = {'x': rng.uniform(1e6, 1e9)}
            got = eval_float(form, env)
            ref = exact_stable(strip_rounding(form), {'x': Decimal(env['x'])})
            if ref is None:
                continue
            worst = max(worst, float(abs(Decimal(got) - ref)))
        self.assertLessEqual(worst, r['best_bound'] * 1.000001)


if __name__ == '__main__':
    unittest.main()
