# -*- coding: utf-8 -*-
"""Печать дерева выражения: человекочитаемо и как JS-код."""

JS_FUN = {'sqrt': 'Math.sqrt', 'exp': 'Math.exp', 'log': 'Math.log',
          'expm1': 'Math.expm1', 'log1p': 'Math.log1p'}


def to_js(t):
    op = t[0]
    if op == 'num':
        v = t[1]
        return repr(float(v))
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '(-' + to_js(t[1]) + ')'
    if op in JS_FUN:
        return JS_FUN[op] + '(' + to_js(t[1]) + ')'
    if op == 'hypot':
        return 'Math.hypot(' + to_js(t[1]) + ', ' + to_js(t[2]) + ')'
    if op == 'fma':
        return '(' + to_js(t[1]) + ' * ' + to_js(t[2]) + ' + ' + to_js(t[3]) + ')'
    return '(' + to_js(t[1]) + ' ' + op + ' ' + to_js(t[2]) + ')'


C_FUN = {'sqrt': 'sqrt', 'exp': 'exp', 'log': 'log',
         'expm1': 'expm1', 'log1p': 'log1p'}


def to_c(t):
    op = t[0]
    if op == 'num':
        return repr(float(t[1]))
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '(-' + to_c(t[1]) + ')'
    if op in C_FUN:
        return C_FUN[op] + '(' + to_c(t[1]) + ')'
    if op in ('fma', 'hypot'):
        return op + '(' + ', '.join(to_c(k) for k in t[1:]) + ')'
    return '(' + to_c(t[1]) + ' ' + op + ' ' + to_c(t[2]) + ')'


def to_text(t):
    op = t[0]
    if op == 'num':
        v = float(t[1])
        return str(int(v)) if v == int(v) else repr(v)
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '-' + to_text(t[1])
    if op in ('sqrt', 'exp', 'log', 'expm1', 'log1p'):
        return op + '(' + to_text(t[1]) + ')'
    if op in ('fma', 'hypot'):
        return op + '(' + ', '.join(to_text(k) for k in t[1:]) + ')'
    return '(' + to_text(t[1]) + ' ' + op + ' ' + to_text(t[2]) + ')'


def op_count(t):
    """Сколько операций каждого вида в дереве."""
    from collections import Counter
    c = Counter()

    def walk(n):
        if n[0] in ('num', 'var'):
            return
        c[n[0]] += 1
        for k in n[1:]:
            walk(k)

    walk(t)
    return dict(c)
