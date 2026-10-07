# -*- coding: utf-8 -*-
"""Подстановка функции с выбором (минимум, максимум, модуль) внутрь выражения.

Повод конкретный и неприятный: поддержав тернарный оператор, я сделал b2MinFloat
и cpfmax функциями с ветвлением — по смыслу верно, — а подстановку таких внутрь
других функций сломал, положив в оператор возврата обёртку Typed вместо дерева.
Падало с TypeError там, где раньше просто отказывало, и ни один тест этого не
заметил: ни один не подставлял функцию с выбором в середину выражения.

Поэтому тест проверяет именно это сочетание: вызов min/max/abs ВНУТРИ арифметики.
"""
import unittest

from pareto.cfront import (collect_context, constants, functions, globals_of,
                           macro_aliases, make_resolver, parse_function)
from pareto.codegen import to_text

SRC = '''
static float lo(float a, float b) { return a < b ? a : b; }
static float hi(float a, float b) { return a > b ? a : b; }
static float mag(float a) { return a < 0 ? -a : a; }

float span(float x, float y, float z, float w)
{
    return (hi(x, y) - lo(x, y)) * (hi(z, w) - lo(z, w));
}

float spread(float a, float b)
{
    return mag(a - b) + hi(a, b);
}
'''


def parse(name, src=SRC):
    files = []
    types, table = collect_context(files)
    texts = [src]
    macros = macro_aliases(texts)
    consts = constants(texts, types)
    globs = globals_of(texts, types, table)
    resolve = make_resolver(texts, types, table, macros=macros, consts=consts,
                            globs=globs)
    return parse_function(src, name, types, table, resolve, macros, consts, globs)


class TestInlineSelect(unittest.TestCase):

    def test_min_and_max_inline_into_arithmetic(self):
        prog = parse('span')
        self.assertEqual(len(prog['stmts']), 1)
        text = to_text(prog['stmts'][0][1])
        self.assertIn('fmin', text)
        self.assertIn('fmax', text)

    def test_abs_inlines_into_arithmetic(self):
        prog = parse('spread')
        text = to_text(prog['stmts'][0][1])
        self.assertIn('fabs', text)
        self.assertIn('fmax', text)

    def test_callee_with_select_is_not_a_crash(self):
        # Отказ допустим, падение — нет. Эта строка сторожит именно разницу.
        for name in ('span', 'spread'):
            with self.subTest(name):
                try:
                    parse(name)
                except TypeError as e:
                    self.fail('подстановка упала вместо работы или отказа: {}'.format(e))


if __name__ == '__main__':
    unittest.main()
