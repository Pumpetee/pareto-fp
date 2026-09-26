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
"""
from __future__ import annotations

import math

from pareto.fma_compat import fma as exact_fma
from pareto.precision import ROUND_OPS


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
        return math.sqrt(eval_float(tree[1], env))
    if op == 'exp':
        return math.exp(eval_float(tree[1], env))
    if op == 'log':
        return math.log(eval_float(tree[1], env))
    if op == 'expm1':
        return math.expm1(eval_float(tree[1], env))
    if op == 'log1p':
        return math.log1p(eval_float(tree[1], env))
    a = eval_float(tree[1], env)
    b = eval_float(tree[2], env)
    if op == 'hypot':
        return math.hypot(a, b)
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if op == '/':
        return a / b
    if op == 'fma':
        c = eval_float(tree[3], env)
        return exact_fma(a, b, c)
    raise AssertionError('unknown node: ' + op)
