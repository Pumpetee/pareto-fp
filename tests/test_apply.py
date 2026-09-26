# -*- coding: utf-8 -*-
"""Обратная запись в файл и проверка требования.

Главная проверка здесь — не форматирование, а КРУГ: файл разбирается, тело
переписывается, результат записывается, и разобранный заново результат обязан
иметь ту самую границу, которую инструмент обещал. Без этой проверки обещание
«переписанная форма держит 1e-16» относилось бы к дереву в памяти, а не к тому
тексту, который человек положит в свой проект.

Запуск: python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto import budget as _budget
from pareto.api import (MET, NEEDS_REWRITE, UNPROVABLE, analyse_c_function,
                        check_requirement)
from pareto.apply import rewrite_source, unified_diff
from pareto.cli import main

SQ_DIFF = '''#include <math.h>

// @domain x: 1000.0 .. 1000.001
// @domain y: 999.999 .. 1000.0
double sq_diff(double x, double y) {
    return x * x - y * y;
}

/* A second function, to prove we do not touch it. */
double untouched(double t) {
    return t * t;
}
'''

NO_INCLUDE = '''/* A header comment, which must stay on top. */

// @domain x: 1000000.0 .. 1000000000.0
double diff_sqrt(double x) {
    return sqrt(x + 1.0) - sqrt(x);
}
'''


def analyse(src, name):
    return analyse_c_function(src, name, refine=True)


class Rewrite(unittest.TestCase):
    def test_only_the_body_changes(self):
        r = analyse(SQ_DIFF, 'sq_diff')
        new, notes = rewrite_source(SQ_DIFF, r)
        # Всё, что не тело выбранной функции, обязано остаться байт в байт.
        lo, hi = r['body_span']
        self.assertEqual(SQ_DIFF[:lo], new[:lo])
        self.assertEqual(SQ_DIFF[hi:], new[len(new) - len(SQ_DIFF[hi:]):])
        self.assertIn('double untouched(double t) {', new)
        self.assertIn('return t * t;', new)
        self.assertEqual(notes, [])
        self.assertNotEqual(new, SQ_DIFF)

    def test_the_written_file_keeps_the_promised_bound(self):
        """Круг: то, что записано, обязано отвечать обещанной границе."""
        r = analyse(SQ_DIFF, 'sq_diff')
        new, _ = rewrite_source(SQ_DIFF, r)
        again = analyse(new, 'sq_diff')
        # Записанная форма как написана — это и есть та, что мы обещали.
        self.assertLessEqual(again['base_bound'], r['best_bound'] * 1.000001)
        # И она заметно лучше исходной: иначе проверять было бы нечего.
        self.assertLess(again['base_bound'], r['base_bound'])

    def test_math_h_appears_once_and_after_the_header_comment(self):
        r = analyse(NO_INCLUDE, 'diff_sqrt')
        new, notes = rewrite_source(NO_INCLUDE, r)
        self.assertEqual(new.count('#include <math.h>'), 1)
        self.assertEqual(len(notes), 1)
        self.assertIn('math.h', notes[0])
        # Комментарий шапки остаётся первым, include идёт за ним.
        self.assertTrue(new.lstrip().startswith('/* A header comment'))
        self.assertLess(new.index('A header comment'), new.index('#include <math.h>'))

    def test_existing_include_is_not_duplicated(self):
        r = analyse(SQ_DIFF, 'sq_diff')
        new, notes = rewrite_source(SQ_DIFF, r)
        self.assertEqual(new.count('#include <math.h>'), 1)
        self.assertEqual(notes, [])

    def test_diff_is_a_real_unified_diff(self):
        r = analyse(SQ_DIFF, 'sq_diff')
        new, _ = rewrite_source(SQ_DIFF, r)
        d = unified_diff(SQ_DIFF, new, path='k.c')
        self.assertTrue(d.startswith('--- k.c'))
        self.assertIn('@@', d)
        self.assertIn('-    return x * x - y * y;', d)


class Requirement(unittest.TestCase):
    def test_three_verdicts(self):
        met = check_requirement(1e-9, 1e-10, 1e-16)
        self.assertEqual((met['verdict'], met['exit_code']), ('met', MET))
        needs = check_requirement(1e-12, 1e-10, 1e-16)
        self.assertEqual((needs['verdict'], needs['exit_code']),
                         ('needs_rewrite', NEEDS_REWRITE))
        no = check_requirement(1e-20, 1e-10, 1e-16)
        self.assertEqual((no['verdict'], no['exit_code']), ('unprovable', UNPROVABLE))

    def test_infinite_bound_is_not_provable(self):
        """Бесконечность — это «доказать не удалось», а не «очень большая ошибка»."""
        inf = float('inf')
        q = check_requirement(1e30, inf, inf)
        self.assertEqual(q['verdict'], 'unprovable')
        self.assertEqual(q['exit_code'], UNPROVABLE)

    def test_exactly_on_the_boundary_counts_as_met(self):
        q = check_requirement(1e-10, 1e-10, 1e-16)
        self.assertEqual(q['verdict'], 'met')


class CliExitCodes(unittest.TestCase):
    """Код возврата и есть ответ: именно на него смотрит скрипт сборки."""

    ARGS = ['x*x - y*y', '--domain', 'x=1000..1000.001',
            '--domain', 'y=999.999..1000', '--time-budget', '10']

    def tearDown(self):
        # Часы живут в модуле, а не в вызове, и `main` их выставляет. Не сбросить
        # их здесь — значит отдать следующему тесту в этом же процессе почти
        # истёкший бюджет и получить загадочно слабые границы у соседей.
        _budget.clear()

    def test_met(self):
        self.assertEqual(main(self.ARGS + ['--require', '1e-9']), MET)

    def test_needs_rewrite(self):
        self.assertEqual(main(self.ARGS + ['--require', '1e-12']), NEEDS_REWRITE)

    def test_unprovable(self):
        self.assertEqual(main(self.ARGS + ['--require', '1e-30']), UNPROVABLE)

    def test_without_require_a_normal_run_is_zero(self):
        self.assertEqual(main(self.ARGS), 0)

    def test_a_usage_error_is_not_a_verdict(self):
        """Опечатка в аргументах не должна читаться как «требование не держится»."""
        with self.assertRaises(SystemExit) as cm:
            main(['x*x', '--domain', 'nonsense'])
        self.assertEqual(cm.exception.code, 64)
        with self.assertRaises(SystemExit) as cm:
            main(['x*x', '--domain', 'x=1..2', '--diff'])
        self.assertEqual(cm.exception.code, 64)


if __name__ == '__main__':
    unittest.main()
