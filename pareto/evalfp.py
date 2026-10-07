# -*- coding: utf-8 -*-
"""Выполнение дерева ровно так, как его выполнит машина.

Отдельный модуль, потому что это не замерочная утилита, а часть продукта: на нём
держится проверка «граница не ниже измеренной ошибки», и его же зовёт исполнение
программы с ветвлениями, где условие обязано решаться по вычисленным величинам.
Раньше эта функция жила внутри сравнительного скрипта, и любой, кому нужно было
просто посчитать формулу, тащил за собой набор бенчмарков.

Узлы округления к узкому формату здесь ВЫПОЛНЯЮТСЯ. Иначе замер шёл бы для
программы в binary64, а граница печаталась бы для программы в binary32, и
сравнивать их было бы бессмысленно.

Особые случаи считаются по IEEE-754, а не по правилам Python. Это не придирка:
Python на делении на ноль бросает исключение, а машина выдаёт бесконечность и
идёт дальше. Пока здесь было исключение, фаззер МОЛЧА пропускал все такие точки —
то есть целая область входов никогда не проверялась, и нарушение границы в ней
осталось бы незамеченным. Нашлось это 06.10.2026 побитовой сверкой с clang на
Normalize и Remap из raylib: компилятор отдавал inf, мы падали.
"""
from __future__ import annotations

import math

from pareto.fma_compat import fma as exact_fma
from pareto.precision import ROUND_OPS


NAN = float('nan')
INF = float('inf')


def _div(a, b):
    """Деление по IEEE-754: на нуле не исключение, а бесконечность или NaN."""
    if b == 0.0:
        if a == 0.0 or a != a:
            return NAN
        # Знак нуля значим: 1/-0.0 это минус бесконечность, а не плюс.
        return math.copysign(INF, a) * math.copysign(1.0, b)
    try:
        return a / b
    except OverflowError:
        return math.copysign(INF, a) * math.copysign(1.0, b)


def _log(v):
    """Логарифм по IEEE-754: у нуля минус бесконечность, у отрицательного NaN."""
    if v != v:
        return NAN
    if v == 0.0:
        return -INF
    if v < 0.0:
        return NAN
    return math.log(v)


def _log1p(v):
    if v != v:
        return NAN
    if v == -1.0:
        return -INF
    if v < -1.0:
        return NAN
    return math.log1p(v)


def _overflows(fn, v):
    """Переполнение в libm даёт бесконечность, а не исключение."""
    if v != v:
        return NAN
    try:
        return fn(v)
    except OverflowError:
        return INF


def eval_float(tree, env):
    op = tree[0]
    if op in ('approx', 'eft'):
        return eval_float(tree[1], env)
    if op in ROUND_OPS:
        return ROUND_OPS[op].round(eval_float(tree[1], env))
    if op == 'num':
        return float(tree[1])
    if op == 'var':
        return float(env[tree[1]])
    if op == 'neg':
        return -eval_float(tree[1], env)
    if op == 'sqrt':
        v = eval_float(tree[1], env)
        return math.sqrt(v) if v >= 0.0 else NAN
    if op == 'exp':
        return _overflows(math.exp, eval_float(tree[1], env))
    if op == 'log':
        return _log(eval_float(tree[1], env))
    if op == 'expm1':
        return _overflows(math.expm1, eval_float(tree[1], env))
    if op == 'log1p':
        v = eval_float(tree[1], env)
        return _log1p(v)
    if op == 'fabs':
        return abs(eval_float(tree[1], env))
    if op in ('sin', 'cos', 'atan'):
        return getattr(math, op)(eval_float(tree[1], env))
    if op in ('asin', 'acos'):
        v = eval_float(tree[1], env)
        # Вне отрезка живой код даёт NaN, а не исключение.
        if v != v or v < -1.0 or v > 1.0:
            return NAN
        return getattr(math, op)(v)
    a = eval_float(tree[1], env)
    b = eval_float(tree[2], env)
    if op == 'hypot':
        return math.hypot(a, b)
    if op == 'atan2':
        return math.atan2(a, b)
    if op == 'fmin':
        # IEEE-754 требует вернуть НЕ-NaN операнд, если второй NaN. Питоновский
        # min этого не делает: min(nan, 0.0) отдаёт nan, потому что сравнение с
        # NaN ложно и min возвращает первый аргумент. Из-за этого наш ответ
        # расходился с исполнением на limitTilt из PX4, где acosf получает
        # аргумент вне области и даёт NaN. Поймано судьёй.
        if a != a:
            return b
        if b != b:
            return a
        return min(a, b)
    if op == 'fmax':
        if a != a:
            return b
        if b != b:
            return a
        return max(a, b)
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if op == '/':
        return _div(a, b)
    if op == 'fma':
        c = eval_float(tree[3], env)
        return exact_fma(a, b, c)
    raise AssertionError('unknown node: ' + op)
