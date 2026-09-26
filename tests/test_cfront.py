# -*- coding: utf-8 -*-
"""Разбор файла на C: типы по правилам языка и громкий отказ на всём остальном.

Второе здесь важнее первого. Разборщик, который на непонятной конструкции делает
вид, что понял, выдаст границу ошибки не для той программы, которая лежит в файле.
Это хуже, чем отсутствие инструмента: человек получает число, которому верит, а
оно относится к другому коду. Поэтому половина тестов ниже проверяет не разбор, а
отказ.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.cfront import CParseError, functions, parse_function, read_domains
from pareto.codegen import to_text
from pareto.precision import FLOAT32, FLOAT64


def one(src, name=None):
    return parse_function(src, name)


def ret(src, name=None):
    """Возвращаемое выражение единственного пути, уже с подставленными локальными.

    Смотреть на сырой оператор `return` бессмысленно: там стоит имя вида `t#2`, а
    подстановку делает перечисление путей. Проверять надо итог.
    """
    from pareto.program import paths
    fn = parse_function(src, name)
    p = paths(fn['stmts'])
    assert len(p) == 1, 'ожидался один путь, получено {}'.format(len(p))
    return to_text(p[0].expr)


class Basics(unittest.TestCase):
    def test_finds_functions_and_ranges(self):
        src = """
// @domain x: 1 .. 2
double f(double x) { return x * x; }
float g(float y) { return y + y; }
"""
        self.assertEqual(functions(src), ['f', 'g'])
        self.assertEqual(read_domains(src), {'x': (1.0, 2.0)})

    def test_range_syntax_variants(self):
        src = ('// @domain x: 1 .. 2\n'
               '// @range y = [-3, 4.5]\n'
               '// @domain z: 1e6 .. 1e9\n'
               'double f(double x) { return x; }\n')
        self.assertEqual(read_domains(src),
                         {'x': (1.0, 2.0), 'y': (-3.0, 4.5), 'z': (1e6, 1e9)})

    def test_locals_and_reassignment(self):
        src = 'double f(double x) { double t = x * x; t = t + 1.0; return t; }'
        self.assertEqual(ret(src), '((x * x) + 1)')

    def test_compound_assignment(self):
        src = 'double f(double x) { double t = x; t *= 2.0; return t; }'
        self.assertEqual(ret(src), '(x * 2)')

    def test_function_calls(self):
        src = 'double f(double x) { return sqrt(x) + exp(x) - log(x); }'
        self.assertEqual(ret(src), '((sqrt(x) + exp(x)) - log(x))')

    def test_fma_is_one_rounding(self):
        src = 'double f(double x, double y) { return fma(x, y, 1.0); }'
        self.assertEqual(ret(src), 'fma(x, y, 1)')

    def test_pow_with_integer_exponent(self):
        src = 'double f(double x) { return pow(x, 3); }'
        self.assertEqual(ret(src), '((x * x) * x)')

    def test_loop_with_known_trip_count_is_unrolled(self):
        src = """
double f(double x) {
    double s = 0.0;
    for (int i = 0; i < 3; i++) {
        s = s + x;
    }
    return s;
}
"""
        self.assertEqual(ret(src), '(((0 + x) + x) + x)')

    def test_loop_counter_is_a_value(self):
        src = """
double f(double x) {
    double s = 0.0;
    for (int i = 1; i <= 3; i++) {
        s = s + x * i;
    }
    return s;
}
"""
        self.assertEqual(ret(src), '(((0 + (x * 1)) + (x * 2)) + (x * 3))')

    def test_if_else_becomes_two_paths(self):
        src = """
double f(double x) {
    if (x > 0.0) { return sqrt(x); } else { return 0.0; }
}
"""
        fn = one(src)
        self.assertEqual(fn['stmts'][0][0], 'if')
        self.assertEqual(to_text(fn['stmts'][0][2][0][1]), 'sqrt(x)')
        self.assertEqual(to_text(fn['stmts'][0][3][0][1]), '0')

    def test_and_or_are_lowered_to_nested_ifs(self):
        src = """
double f(double x, double y) {
    if (x > 0.0 && y > 0.0) { return x; }
    return y;
}
"""
        from pareto.program import paths
        fn = one(src)
        p = paths(fn['stmts'])
        # два условия дают три пути: оба верны, первое верно а второе нет, первое нет
        self.assertEqual(len(p), 3)
        self.assertEqual([to_text(q.expr) for q in p], ['x', 'y', 'y'])

    def test_or_is_lowered_too(self):
        src = """
double f(double x, double y) {
    if (x > 0.0 || y > 0.0) { return x; }
    return y;
}
"""
        from pareto.program import paths
        p = paths(one(src)['stmts'])
        self.assertEqual([to_text(q.expr) for q in p], ['x', 'x', 'y'])

    def test_early_return_without_else(self):
        src = """
double f(double x) {
    if (x < 0.0) return 0.0;
    return sqrt(x);
}
"""
        from pareto.program import paths
        p = paths(one(src)['stmts'])
        self.assertEqual([to_text(q.expr) for q in p], ['0', 'sqrt(x)'])


class CTypeRules(unittest.TestCase):
    """Типы по правилам C, а не по интуиции. Ошибка здесь = граница не для того кода."""

    def test_float_times_float_rounds_to_float(self):
        src = 'float f(float a, float b) { return a * b; }'
        self.assertEqual(ret(src), 'f32((a * b))')

    def test_float_times_double_literal_stays_double(self):
        src = 'double f(float a) { return a * 2.0; }'
        self.assertEqual(ret(src), '(a * 2)')

    def test_float_times_float_literal_rounds(self):
        src = 'double f(float a) { return a * 2.0f; }'
        self.assertEqual(ret(src), 'f32((a * 2))')

    def test_assignment_to_float_rounds(self):
        src = 'double f(double x) { float t = x * x; return t; }'
        self.assertEqual(ret(src), 'f32((x * x))')

    def test_float_result_rounds_the_return(self):
        src = 'float f(double x) { return x * x; }'
        self.assertEqual(ret(src), 'f32((x * x))')

    def test_explicit_cast(self):
        src = 'double f(double x) { return (float)(x * x) + x; }'
        self.assertEqual(ret(src), '(f32((x * x)) + x)')

    def test_cast_to_the_same_type_is_not_an_operation(self):
        src = 'double f(double x) { return (double)(x * x); }'
        self.assertEqual(ret(src), '(x * x)')

    def test_sqrtf_is_single_precision(self):
        src = 'float f(float a) { return sqrtf(a); }'
        self.assertEqual(ret(src), 'f32(sqrt(a))')

    def test_sqrt_of_a_float_is_double(self):
        src = 'double f(float a) { return sqrt(a); }'
        self.assertEqual(ret(src), 'sqrt(a)')

    def test_float_literal_is_rounded_to_float(self):
        src = 'double f(float a) { return a + 0.1f; }'
        # 0.1f это не 1/10, а ближайшее к нему binary32
        self.assertIn('0.10000000149011612', ret(src))


class LoudRefusals(unittest.TestCase):
    """Всё, что метод не умеет доказывать, обязано быть отказом с номером строки."""

    def refuse(self, src, fragment=None):
        with self.assertRaises(CParseError) as cm:
            parse_function(src)
        if fragment:
            self.assertIn(fragment, str(cm.exception))
        return str(cm.exception)

    def test_while_loop(self):
        self.refuse('double f(double x) { while (x > 1.0) { x = x / 2.0; } return x; }',
                    'while')

    def test_loop_with_unknown_trip_count(self):
        self.refuse('double f(double x, double n) { double s = 0.0; '
                    'for (int i = 0; i < n; i++) { s = s + x; } return s; }')

    def test_array_argument(self):
        self.refuse('double f(double *a) { return a[0]; }', 'pointer')

    def test_array_declaration(self):
        self.refuse('double f(double x) { double a[4]; return x; }')

    def test_integer_declaration(self):
        self.refuse('double f(double x) { int k = 2; return x; }', 'int')

    def test_unsupported_function(self):
        self.refuse('double f(double x) { return sin(x); }', 'sin')

    def test_unknown_name(self):
        self.refuse('double f(double x) { return x + q; }', 'unknown name')

    def test_uninitialised_local(self):
        self.refuse('double f(double x) { double t; t = x; return t; }',
                    'declared without a value')

    def test_no_return(self):
        # Это ловит не разборщик, а перечисление путей: синтаксически функция без
        # возврата корректна, бессмысленна она именно как программа.
        from pareto.program import ProgramError, paths
        fn = parse_function('double f(double x) { double t = x; }')
        with self.assertRaises(ProgramError):
            paths(fn['stmts'])

    def test_remainder_operator(self):
        self.refuse('double f(double x, double y) { return x % y; }', 'remainder')

    def test_string_in_the_body(self):
        self.refuse('double f(double x) { const char *s = "hi"; return x; }')

    def test_goto(self):
        self.refuse('double f(double x) { goto end; end: return x; }', 'goto')

    def test_pow_with_a_variable_exponent(self):
        self.refuse('double f(double x, double y) { return pow(x, y); }', 'pow')

    def test_condition_that_is_not_a_comparison(self):
        self.refuse('double f(double x) { if (x) { return 1.0; } return 0.0; }',
                    'comparison')

    def test_function_not_found(self):
        with self.assertRaises(CParseError):
            parse_function('double f(double x) { return x; }', 'nosuch')

    def test_no_float_function_at_all(self):
        with self.assertRaises(CParseError):
            parse_function('int main(void) { return 0; }')

    def test_integer_cast(self):
        self.refuse('double f(double x) { return (int)(x); }', 'integer')


class MissingRanges(unittest.TestCase):
    def test_a_missing_range_is_a_refusal_not_a_guess(self):
        from pareto.api import analyse_c_function
        from pareto.program import ProgramError
        src = 'double f(double x) { return x * x; }'
        with self.assertRaises(ProgramError) as cm:
            analyse_c_function(src)
        self.assertIn('no range given', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
